"""Draw and maintain the pinned property sample.

ONS re-price a fixed sample of properties month after month and substitute one
only when it drops out. That is the behaviour this module maintains, and it is
the piece with no equivalent in the air-fares project at all -- there, the panel
is a list of routes written by hand once, because routes do not stop existing.

TWO MODES
---------
`--discover` draws an initial sample: query each location, apply the same
comparability filter collection uses, and pin the first few properties per
(location, tier) *by token order*. Token order is arbitrary and, crucially,
price-independent. Drawing by price would make every subsequent month a
comparison against a base chosen for being cheap; drawing by rating would bias
toward well-reviewed properties. An arbitrary reproducible draw is what is
wanted, and it is the closest thing available to ONS's random selection.

`--substitute` replaces pinned properties that have stopped appearing. It reads
the `property_churn` view for properties whose `churn_status` is `left`, draws a
replacement from the same cell, and records the substitution in
`selection_basis` as `substitute_for:<old token>`. Substitutions are never
silent: a sample that quietly reconstitutes itself is a sample whose history
cannot be interpreted.

WHY THE PANEL IS A COMMITTED CSV
---------------------------------
The Actions checkout is ephemeral, so a pinned sample held anywhere but the
repository would be redrawn on every run -- which is not a pinned sample at all,
it is a fresh random draw wearing one's clothes. Committing it also means the
sample's history is in git: when it changed, and to what.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import pathlib
import sys
from typing import Any

from . import panel, selection
from .config import Config, ConfigError
from .onscal import ADVANCE_DAYS
from .panel import PanelProperty
from .providers import AccommodationProvider, ProviderError, build_provider

log = logging.getLogger("ukhotels.discover")

#: Properties to pin per (location, tier). Three is the minimum a matched-sample
#: relative is computed on, so pinning five leaves headroom for two to churn
#: before the cell stops producing a defensible number.
DEFAULT_PER_CELL = 5

CHURNED_QUERY = """
SELECT location, property_tier, property_token, property_name
FROM `{churn_view}`
WHERE churn_status = 'left' AND is_panel_property
"""


def _probe_date(reference: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """A stay night to draw the sample against.

    Uses the standard six-week lead so the properties returned are the ones that
    actually have availability at the lead we collect at. Drawing against
    tomorrow would pin a sample skewed toward properties with last-minute
    inventory, which is a different population -- and precisely the population
    ONS moved *away* from in 2025.
    """
    reference = reference or dt.date.today()
    check_in = reference + dt.timedelta(days=ADVANCE_DAYS)
    return check_in, check_in + dt.timedelta(days=1)


def discover(
    config: Config,
    *,
    provider: AccommodationProvider | None = None,
    per_cell: int = DEFAULT_PER_CELL,
    existing: dict[tuple[str, str], tuple[PanelProperty, ...]] | None = None,
    substitutions: dict[tuple[str, str], list[str]] | None = None,
    reference_date: dt.date | None = None,
    dump_raw: pathlib.Path | None = None,
) -> tuple[list[PanelProperty], dict[str, Any]]:
    """Draw (or top up) the pinned sample. Returns the panel and a summary.

    `existing` entries are preserved: a pinned property is never re-drawn away
    from, because the whole value of pinning is continuity. Only cells short of
    `per_cell` are topped up, and only tokens listed in `substitutions` are
    replaced.
    """
    existing = dict(existing or {})
    substitutions = substitutions or {}

    if provider is None:
        config.require_provider_credential()
        kwargs: dict[str, Any] = {}
        if config.provider_name == "serpapi":
            kwargs = {"api_key": config.provider_credential, "market": config.market}
        provider = build_provider(config.provider_name, **kwargs)

    check_in, check_out = _probe_date(reference_date)
    today = dt.date.today().isoformat()
    out: list[PanelProperty] = []
    summary: dict[str, Any] = {
        "probe_check_in": check_in.isoformat(),
        "provider": provider.name,
        "per_cell": per_cell,
        "rate_basis": config.rate_basis,
        "cells": {},
        "errors": {},
        "added": 0,
        "retired": 0,
    }

    # A census of the field values the provider actually returned, before any
    # filtering. When a cell comes back with properties returned but none
    # comparable, this is what says which control rejected them -- and it does
    # so without anyone having to download and read a payload. The first live
    # run needed exactly this and did not have it: 18-20 properties per city,
    # zero comparable, and no way to tell whether the cause was the star rating,
    # the property type or the cancellation basis.
    survey: dict[str, collections.Counter] = {
        "hotel_class": collections.Counter(),
        "property_type": collections.Counter(),
        "free_cancellation": collections.Counter(),
        "price_present": collections.Counter(),
        # The keys the provider actually puts on a property object. This is what
        # distinguishes "our parser looks in the wrong place" from "the engine
        # does not return this at all" -- a distinction that otherwise costs a
        # payload download to make, and which decides whether a missing field is
        # a bug or a methodology limitation.
        "raw_property_keys": collections.Counter(),
    }

    for location in panel.LOCATIONS:
        try:
            result = provider.search(
                query=location.query,
                check_in=check_in,
                check_out=check_out,
                adults=panel.ADULTS,
                children=panel.CHILDREN,
                currency=config.currency,
            )
        except ProviderError as exc:
            # One location failing must not abandon the draw: the other eleven
            # are still worth pinning, and this location can be topped up on the
            # next run.
            log.warning("%s: %s", location.code, exc)
            summary["errors"][location.code] = str(exc)
            for key, props in existing.items():
                if key[0] == location.code:
                    out.extend(props)
            continue

        # One payload is enough to diagnose a parser mismatch, and the first
        # location is as good as any. Already redacted by the provider -- see
        # `serpapi_hotels.redact` -- which matters because this file is meant to
        # be uploaded as a workflow artifact or pasted into an issue.
        if dump_raw is not None and not dump_raw.exists():
            dump_raw.parent.mkdir(parents=True, exist_ok=True)
            dump_raw.write_text(
                json.dumps(result.raw_payload, indent=1, default=str), encoding="utf-8"
            )
            log.info("wrote a sample raw payload to %s", dump_raw)

        for quote in result.quotes:
            survey["hotel_class"][repr(quote.hotel_class)] += 1
            survey["property_type"][repr(quote.property_type)] += 1
            survey["free_cancellation"][repr(quote.free_cancellation)] += 1
            survey["price_present"][repr(quote.price is not None)] += 1
            for key in (quote.raw or {}):
                survey["raw_property_keys"][key] += 1

        sets = selection.comparable_sets(
            result.quotes,
            rate_basis=config.rate_basis,
            max_price_ratio=config.max_price_ratio,
        )

        for tier, comparable in sets.items():
            key = (location.code, tier)
            retired = set(substitutions.get(key, ()))
            kept = [p for p in existing.get(key, ()) if p.property_token not in retired]
            summary["retired"] += len(existing.get(key, ())) - len(kept)
            have = {p.property_token for p in kept}

            candidates = [
                q for q in selection.pick_panel_candidates(comparable, n=per_cell * 3)
                if q.property_token not in have and q.property_token not in retired
            ]
            basis = (
                f"substitute_for:{','.join(sorted(retired))}" if retired else "discovered"
            )
            for quote in candidates[: max(per_cell - len(kept), 0)]:
                kept.append(
                    PanelProperty(
                        location=location.code,
                        tier=tier,
                        property_token=quote.property_token,
                        property_name=quote.property_name,
                        first_seen=today,
                        selection_basis=basis,
                    )
                )
                summary["added"] += 1

            summary["cells"][f"{location.code}/{tier}"] = {
                "pinned": len(kept),
                "comparable_available": comparable.n_considered,
                "returned": comparable.n_returned,
                # Which control rejected what. `returned` high and
                # `comparable_available` zero is only actionable with this.
                "dropped": {
                    "property_type": comparable.n_dropped_property_type,
                    "tier": comparable.n_dropped_tier,
                    "rate_basis": comparable.n_dropped_rate_basis,
                    "outlier": comparable.n_dropped_outlier,
                    "other_tier": comparable.n_other_tier,
                },
                # False means the counts do not account for everything the
                # provider returned, i.e. this breakdown cannot be trusted.
                "counts_reconcile": comparable.reconciles(),
            }
            out.extend(kept)

    summary["field_survey"] = {
        field: dict(counter.most_common(40 if field == "raw_property_keys" else 12))
        for field, counter in survey.items()
    }

    thin = [k for k, v in summary["cells"].items() if v["pinned"] < 3]
    if thin:
        summary["thin_cells"] = thin
    return out, summary


def churned_tokens(reader, churn_view: str) -> dict[tuple[str, str], list[str]]:
    """Pinned properties the panel has stopped seeing, grouped by cell."""
    out: dict[tuple[str, str], list[str]] = {}
    for row in reader.query(CHURNED_QUERY.format(churn_view=churn_view)):
        out.setdefault((row["location"], row["property_tier"]), []).append(
            row["property_token"]
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Draw or maintain the pinned property sample."
    )
    parser.add_argument(
        "--per-cell",
        type=int,
        default=DEFAULT_PER_CELL,
        help=f"Properties to pin per (location, tier). Default {DEFAULT_PER_CELL}.",
    )
    parser.add_argument(
        "--substitute",
        action="store_true",
        help=(
            "Replace pinned properties that have stopped appearing, read from the "
            "property_churn view. Requires BigQuery access."
        ),
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="Where to write the panel. Defaults to the packaged property_panel.csv.",
    )
    parser.add_argument(
        "--dry-run-panel",
        action="store_true",
        help="Print the panel that would be written without writing it.",
    )
    parser.add_argument(
        "--dump-raw",
        type=pathlib.Path,
        default=None,
        help=(
            "Write one location's raw provider payload here, for diagnosing a "
            "parser mismatch. The credential is already scrubbed out of it."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    existing = panel.load_property_panel(args.out)
    substitutions: dict[tuple[str, str], list[str]] = {}
    if args.substitute:
        from . import bq

        try:
            reader = bq.BigQueryWriter(config.project)
            substitutions = churned_tokens(reader, config.table_ref("property_churn"))
        except Exception as exc:  # noqa: BLE001
            # Substitution is an improvement, not a prerequisite. Failing the
            # whole run because the churn view is unreadable would also skip the
            # top-up of cells that are merely short.
            print(
                f"::warning::could not read churned properties ({type(exc).__name__}); "
                "continuing without substitutions",
                file=sys.stderr,
                flush=True,
            )

    try:
        properties, summary = discover(
            config,
            per_cell=args.per_cell,
            existing=existing,
            substitutions=substitutions,
            dump_raw=args.dump_raw,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::discovery failed: {exc}", file=sys.stderr, flush=True)
        log.exception("discovery failed")
        return 1

    if args.dry_run_panel:
        # stderr, not stdout: the workflow pipes stdout through `tee` into
        # smoke-summary.json, and a human-readable table ahead of the JSON makes
        # that file unparseable. It only survived the first live run because
        # zero properties were discovered, so nothing was printed before it.
        for prop in sorted(properties, key=lambda p: (p.location, p.tier, p.property_name)):
            print(
                f"{prop.location:26} {prop.tier:9} {prop.property_token:24} {prop.property_name}",
                file=sys.stderr,
            )
    else:
        path = panel.write_property_panel(properties, args.out)
        summary["path"] = str(path)

    print(json.dumps(summary, indent=2))

    for cell in summary.get("thin_cells", []):
        print(
            f"::warning::{cell} has fewer than 3 pinned properties; matched-sample "
            "relatives for it will be refused",
            file=sys.stderr,
            flush=True,
        )
    if summary["errors"]:
        for code, err in summary["errors"].items():
            print(f"::warning::discovery failed for {code}: {err}", file=sys.stderr, flush=True)
    if not properties:
        print("::error::no properties discovered at all", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
