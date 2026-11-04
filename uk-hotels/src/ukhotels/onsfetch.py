"""Locate the confirmed index day in an ONS CPI bulletin.

Index day is a property of the CPI collection round as a whole, not of any one
item, so this module is close to its air-fares sibling and the same structural
check applies: **a valid index day is the 2nd or 3rd Tuesday of the target
month.** That single constraint rejects essentially every possible mis-parse --
publication dates, reference-period dates, next-release dates -- because none of
them reliably land on one of exactly two days in the month. A candidate failing
it is discarded regardless of how confident the surrounding wording looked.

WHAT INDEX DAY MEANS HERE, WHICH IS NOT WHAT IT MEANS FOR AIR FARES
--------------------------------------------------------------------
For air fares, index day is when the collector works *and* when the flight
departs. For accommodation it is neither: it is the night being stayed in.
Collection happened six weeks earlier. So confirming the index day for August
tells us which nights in August our June and July collection runs should have
been pricing -- it validates the panel retrospectively rather than telling us
when to collect.

The practical consequence is that a mis-parsed index day here does not cause a
missed collection (that already happened, correctly or not, six weeks ago). It
causes the wrong rows to be aggregated into the wrong month. Which is quieter,
and therefore worse: a missed collection is a visible gap, a mis-attributed
month is a plausible-looking number. Hence the same refusal to guess.

If nothing parses, this raises rather than guessing. Silence would mean quietly
skipping a month forever.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
import logging
import re
from typing import Iterable

import requests

from .onscal import candidate_index_days

log = logging.getLogger(__name__)

BULLETIN_BASE = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation"
)

USER_AGENT = (
    "uk-hotel-nowcasting/0.1 (research pipeline; contact via repository owner)"
)

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

#: Wording ONS have used for this over the years. Ordered most to least
#: specific; every hit is still validated against the Tuesday constraint, so a
#: loose pattern costs nothing but a rejected candidate.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"index\s+d(?:ay|ate)[^.]{0,120}?"
        r"(?:was|is|of|on)\s+(?:\w+day\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"index\s+d(?:ay|ate)[^.]{0,120}?(?:was|is|of|on)\s+(?:\w+day\s+)?"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)",
        re.IGNORECASE,
    ),
    # The wording ONS actually use in 2026 bulletins, observed in the live July
    # 2026 bulletin by uk-airfares' `--dump`: "The traditionally sourced data used
    # in this release were collected on or around 14 July 2026."
    #
    # Ported from uk-airfares 117ba7c on 2026-11-04, eleven weeks late. Both
    # projects parse the SAME ONS CPI bulletin, so the fix applied here from the
    # day it landed there; it simply never got copied. Every hotels
    # reconciliation from 2026-10-21 failed with "Dates seen but rejected:
    # [2026-02-28]" -- the identical signature airfares diagnosed in August.
    #
    # What the old pattern got wrong, twice over. It required "prices collected",
    # but ONS write "data ... were collected". And it expected `on` straight
    # before the date, where ONS hedge with "on or around".
    #
    # Worse, its `collected[^.]{0,120}?on` wildcard reached across a clause and
    # matched a different sentence in the same bulletin: "All domestic and
    # European flight prices were collected after the outbreak of the conflict in
    # the Middle East on 28 February 2026." That is the 2026-02-28. The Tuesday
    # constraint rejected it -- but by luck of the calendar, not because the
    # pattern was right. A wrong date that happened to land on a 2nd or 3rd
    # Tuesday would have been accepted silently and filed every night under the
    # wrong month. So `on` must now follow `collected` immediately, bar the
    # hedge; the wildcard is gone.
    re.compile(
        r"collected\s+on(?:\s+or\s+(?:around|about))?\s+(?:\w+day,?\s+)?"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\w+day\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})[^.]{0,80}?index\s+d(?:ay|ate)",
        re.IGNORECASE,
    ),
    # Accommodation-specific phrasing: the bulletin sometimes refers to index
    # *week* rather than index day when discussing this item, since the stay
    # night is what matters here.
    re.compile(
        r"index\s+week[^.]{0,120}?(?:beginning|commencing|of|from)\s+"
        r"(?:\w+day\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})",
        re.IGNORECASE,
    ),
)


class IndexDayNotFound(RuntimeError):
    """The bulletin did not yield a defensible index day."""


class BulletinNotPublished(RuntimeError):
    """The bulletin is not out yet. Expected, not an error condition."""


@dataclasses.dataclass(frozen=True, slots=True)
class IndexDayResult:
    index_month: dt.date
    index_day: dt.date
    ordinal: int
    source_url: str
    #: The sentence the date was read out of, kept so a human can audit the parse.
    evidence: str


def bulletin_url(month: dt.date) -> str:
    """URL of the bulletin that confirms the index day for `month`.

    ONS confirm month M's index day in the bulletin published for month M, which
    appears roughly mid-M+1. The bulletin is named for its reference month, so
    the URL uses M itself.
    """
    return f"{BULLETIN_BASE}/{calendar.month_name[month.month].lower()}{month.year}"


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text)


def _candidate_dates(text: str, month: dt.date) -> Iterable[tuple[dt.date, str]]:
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            day_s, month_s = groups[0], groups[1]
            year = int(groups[2]) if len(groups) > 2 and groups[2] else month.year
            month_num = _MONTHS.get(month_s.lower())
            if not month_num:
                continue
            try:
                candidate = dt.date(year, month_num, int(day_s))
            except ValueError:
                continue
            start = max(match.start() - 100, 0)
            yield candidate, text[start : match.end() + 100].strip()


def parse_index_day(html: str, month: dt.date, source_url: str = "") -> IndexDayResult:
    """Extract and validate the index day for `month` from bulletin HTML."""
    text = _strip_html(html)
    second, third = candidate_index_days(month.year, month.month)
    valid = {second: 2, third: 3}

    seen: list[dt.date] = []
    for candidate, evidence in _candidate_dates(text, month):
        seen.append(candidate)
        if candidate in valid:
            log.info("index day for %s: %s", month.strftime("%B %Y"), candidate)
            return IndexDayResult(
                index_month=month.replace(day=1),
                index_day=candidate,
                ordinal=valid[candidate],
                source_url=source_url,
                evidence=evidence[:500],
            )
        log.debug("rejected %s -- not the 2nd or 3rd Tuesday of %s", candidate, month)

    raise IndexDayNotFound(
        f"no index day for {month:%B %Y} in {source_url or 'supplied HTML'}. "
        f"Expected {second} or {third}. "
        + (f"Dates seen but rejected: {sorted(set(seen))}." if seen else "No dates matched at all.")
        + " Read the bulletin's methodology section and pass --index-day explicitly."
    )


def fetch_index_day(
    month: dt.date,
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> IndexDayResult:
    """Fetch the bulletin for `month` and return its confirmed index day."""
    url = bulletin_url(month)
    session = session or requests.Session()
    resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})

    if resp.status_code == 404:
        raise BulletinNotPublished(
            f"bulletin for {month:%B %Y} not published yet ({url})"
        )
    resp.raise_for_status()
    return parse_index_day(resp.text, month, source_url=url)


# --- Diagnostics --------------------------------------------------------------

#: Phrases the index day should sit near. Matched or not, every occurrence is
#: printed, because that is what shows whether the bulletin's prose is even in
#: the served HTML. "collected on" is first for a reason: it is the wording ONS
#: actually used in 2026, which no other pattern here anticipated.
_DUMP_PHRASES = (
    "collected on", "index day", "index date", "index week", "price collection",
    "methodology", "hotel", "accommodation",
)


def dump_bulletin(month: dt.date, *, timeout: float = 30.0, session=None) -> str:
    """Describe what the parser actually sees, for when it sees the wrong thing.

    Ported from uk-airfares (af19722), where it found the 2026 wording in one
    run after the parser had been debugged blind. ons.gov.uk is refused by egress
    policy in the development sandbox, so a parse failure cannot be diagnosed
    where this code is written -- only on a runner. Without this, the only
    evidence a failure leaves is a list of rejected dates, which says what the
    parser matched and nothing about what it missed.
    """
    url = bulletin_url(month)
    session = session or requests.Session()
    resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    text = _strip_html(resp.text)
    second, third = candidate_index_days(month.year, month.month)

    out = [
        f"url            {url}",
        f"http           {resp.status_code}",
        f"html bytes     {len(resp.text):,}",
        f"stripped chars {len(text):,}",
        f"looking for    {second} (2nd Tue) or {third} (3rd Tue)",
        "",
    ]

    hits = list(_candidate_dates(text, month))
    out.append(f"pattern matches: {len(hits)}")
    for candidate, evidence in hits:
        verdict = "ACCEPTED" if candidate in (second, third) else "rejected"
        out += [f"  {candidate}  <-- {verdict}", f"      {evidence[:300]}", ""]

    out.append("context around key phrases:")
    found_any = False
    for phrase in _DUMP_PHRASES:
        for m in re.finditer(re.escape(phrase), text, re.IGNORECASE):
            found_any = True
            lo, hi = max(m.start() - 160, 0), m.end() + 220
            out.append(f"  [{phrase}] ...{text[lo:hi].strip()}...")
    if not found_any:
        out.append("  NONE. The served HTML does not contain the bulletin's prose;")
        out.append("  it is likely rendered client-side or on a linked sub-page.")

    out += ["", "first 600 chars of stripped text:", "  " + text[:600]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Inspect an ONS CPI bulletin.")
    parser.add_argument(
        "--month",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m").date().replace(day=1),
        required=True,
        help="Bulletin reference month, YYYY-MM.",
    )
    parser.add_argument("--dump", action="store_true",
                        help="Print what the parser sees instead of parsing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.dump:
        print(dump_bulletin(args.month))
        return 0
    # Exit codes: 0 parsed, 1 published but unparseable, 3 not published yet.
    # The last two are kept apart because they mean opposite things -- one is
    # the failure this CLI exists to catch, the other is being early -- and a
    # caller looping over recent months has to be able to tell them apart.
    # (The uk-airfares original returns 1 for both.)
    #
    # Annotations go to stderr, unlike the original: stdout is piped into files
    # by the workflows, and an annotation there corrupts them.
    try:
        result = fetch_index_day(args.month)
    except BulletinNotPublished as exc:
        print(f"::notice::{exc}", file=sys.stderr, flush=True)
        return 3
    except IndexDayNotFound as exc:
        print(f"::error::{exc}", file=sys.stderr, flush=True)
        return 1
    print(f"{result.index_day}  (ordinal {result.ordinal})")
    print(f"evidence: {result.evidence}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
