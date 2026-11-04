"""uk-hotels and uk-airfares must not drift apart where they share code.

WHY THIS EXISTS
---------------
Three fixes landed in one project and not the other in 2026, and each one went
unnoticed until it broke something:

  * the index-day parser fix (117ba7c) and the stale-month ordering (af19722)
    went into uk-airfares in August; hotels reconciliation failed on the
    identical "rejected: [2026-02-28]" every day from 2026-10-21;
  * the ARRAY<STRING> query-parameter bug sat in both copies of bq.py;
  * the d7ex series ID was hotels-only, but caught by an airfares-shaped probe.

The two projects parse the same ONS bulletin, use the same BigQuery client
wrapper, and share the ONS calendar fundamentals. They are copies, not a shared
package -- splitting them into one would couple their deploys, and the brief
was to build a sibling, not a library. So the copies are held to each other here.

HOW IT WORKS
------------
Function bodies are compared via the AST, with docstrings dropped and package
names normalised, so a reworded docstring is not drift but a changed line is.
Every function defined in BOTH copies of a shared module must be listed as
either IDENTICAL or INTENTIONALLY DIFFERENT, with the reason. A new shared
function fails until someone decides which, which is the decision that was
never taken for the parser.

Runs in hotels CI, which is triggered by changes to the airfares counterparts
too (see hotels-ci.yml). Reads the airfares source as text; never imports it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOTELS = ROOT / "uk-hotels" / "src" / "ukhotels"
AIRFARES = ROOT / "uk-airfares" / "src" / "ukairfares"

#: module -> (functions that must be identical, {function: why it may differ})
SHARED: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "onsfetch": (
        ("_strip_html", "_candidate_dates", "parse_index_day", "bulletin_url",
         "fetch_index_day"),
        {
            "dump_bulletin": "searches item-specific phrases (hotel vs air fare)",
            "main": "hotels exits 3 for 'not published' so a loop over months can "
                    "tell early from broken, and annotates on stderr",
        },
    ),
    "bq": (
        ("__init__", "_json_default", "_to_query_param", "append", "build_writer",
         "chunked", "ensure_tables", "query"),
        {"ensure_dataset": "the dataset description names the project"},
    ),
    "onscal": (
        ("add_months", "candidate_index_days", "index_day_ordinal",
         "index_month_collection", "nth_tuesday", "nth_weekday"),
        {"in_collection_window": "accommodation collects six weeks ahead of the "
                                 "stay night; air fares collect on the index day"},
    ),
}


def _functions(path: pathlib.Path) -> dict[str, str]:
    """Every function in a module, docstring stripped, package name normalised."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
        ) and isinstance(body[0].value.value, str):
            body = body[1:] or [ast.Pass()]
        clone = type(node)(**{**node.__dict__, "body": body})
        text = ast.unparse(clone)
        out[node.name] = text.replace("ukairfares", "PKG").replace("ukhotels", "PKG")
    return out


def _cases():
    for module, (identical, _) in SHARED.items():
        for name in identical:
            yield module, name


@pytest.mark.parametrize("module,name", list(_cases()))
def test_shared_function_is_identical(module, name):
    hotels = _functions(HOTELS / f"{module}.py")
    airfares = _functions(AIRFARES / f"{module}.py")
    assert name in hotels and name in airfares, f"{module}.{name} missing from one project"
    assert hotels[name] == airfares[name], (
        f"{module}.{name} has drifted between uk-hotels and uk-airfares. Port the "
        "fix to the other project, or move it to the intentionally-different list "
        "in tests/test_parity.py with the reason."
    )


@pytest.mark.parametrize("module", list(SHARED))
def test_every_function_both_projects_define_is_classified(module):
    """A new shared function must be decided on, not silently left to drift."""
    identical, intentional = SHARED[module]
    both = set(_functions(HOTELS / f"{module}.py")) & set(_functions(AIRFARES / f"{module}.py"))
    unclassified = both - set(identical) - set(intentional)
    assert not unclassified, (
        f"{module}: {sorted(unclassified)} exist in both projects but are in "
        "neither list in tests/test_parity.py. Decide which."
    )


@pytest.mark.parametrize("module", list(SHARED))
def test_the_intentional_differences_still_exist(module):
    """An exemption for code that has since converged is a hole, not a record."""
    _, intentional = SHARED[module]
    hotels = _functions(HOTELS / f"{module}.py")
    airfares = _functions(AIRFARES / f"{module}.py")
    stale = [n for n in intentional if n in hotels and n in airfares and hotels[n] == airfares[n]]
    assert not stale, f"{module}: {stale} are now identical; move them to the identical list"


def _patterns(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = getattr(node, "target", None) or (
            node.targets[0] if isinstance(node, ast.Assign) else None
        )
        if isinstance(target, ast.Name) and target.id == "_PATTERNS":
            return [
                call.args[0].value
                for call in ast.walk(node.value)
                if isinstance(call, ast.Call) and call.args
                and isinstance(call.args[0], ast.Constant)
            ]
    raise AssertionError(f"no _PATTERNS in {path}")


def test_hotels_parses_every_wording_airfares_has_learned():
    """Hotels may add bulletin patterns of its own; it must never lack one.

    The pattern that reads "collected on or around" was learned from the live
    July 2026 bulletin by uk-airfares and was missing here for eleven weeks.
    """
    missing = set(_patterns(AIRFARES / "onsfetch.py")) - set(_patterns(HOTELS / "onsfetch.py"))
    assert not missing, f"uk-hotels lacks bulletin patterns uk-airfares uses: {missing}"


def test_hotels_ci_runs_when_the_airfares_counterparts_change():
    """Otherwise a change on the airfares side never triggers this check."""
    ci = (ROOT / ".github" / "workflows" / "hotels-ci.yml").read_text(encoding="utf-8")
    for module in SHARED:
        assert f"uk-airfares/src/ukairfares/{module}.py" in ci, (
            f"hotels-ci.yml does not trigger on uk-airfares {module}.py"
        )
