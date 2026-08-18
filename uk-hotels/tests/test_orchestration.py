"""Cross-project orchestration checks.

Both projects in this repository share a GitHub Actions runner pool, a SerpApi
key, a BigQuery dataset and a git branch. Anything they do at the same instant
they do to each other. These are static checks -- no network, no YAML
dependency, just the workflow files as text.
"""

from __future__ import annotations

import collections
import pathlib
import re

import pytest

WORKFLOWS = sorted(
    (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml")
)

CRON = re.compile(r'^\s*-\s*cron:\s*"([^"]+)"', re.M)
ENV_NAME = re.compile(r'"((?:BQ|GCP|FARE|HOTEL|ALLOW|DRY|RATE|TAX|SERPAPI)[A-Z_]*)"')


def crons() -> dict[str, list[str]]:
    return {p.name: CRON.findall(p.read_text(encoding="utf-8")) for p in WORKFLOWS}


def test_workflows_are_present():
    assert WORKFLOWS, "no workflow files found"


def test_no_two_workflows_share_a_cron_expression():
    """Simultaneous runs contend for things neither project owns alone.

    All four hotels schedules originally matched an air fares schedule exactly:

      0 9 * * *        both pull from the SAME SerpApi key -- shared quota and
                       shared concurrency limits
      0 7 2 * *        both `git pull --rebase && git push` the same branch, in
                       the same minute
      0 6 3 * *        both hit ONS and BigQuery
      0 12 15-25 * *   both fetch the same CPI bulletin

    The digest pair was the concrete hazard: two jobs racing to push main. Both
    carry retry loops, but not colliding is cheaper than recovering.
    """
    seen: dict[str, list[str]] = collections.defaultdict(list)
    for name, expressions in crons().items():
        for expression in expressions:
            seen[expression].append(name)
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not clashes, f"workflows sharing a cron expression: {clashes}"


def test_concurrency_groups_are_distinct():
    groups = re.findall(
        r"^\s*group:\s*(\S+)",
        "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOWS),
        re.M,
    )
    duplicates = [g for g, n in collections.Counter(groups).items() if n > 1]
    assert not duplicates, f"shared concurrency groups: {duplicates}"


@pytest.mark.parametrize("name", ["BQ_ACCOMMODATION_SCRAPES_TABLE",
                                  "BQ_ACCOMMODATION_INDEX_TABLE"])
def test_table_overrides_are_namespaced(name):
    """The air fares package reads BQ_SCRAPES_TABLE and BQ_INDEX_TABLE.

    Sharing those names would mean a repository variable set for one project
    silently retargets the other -- the same shared-table collision that broke
    reconciliation on 2026-08-18, arrived at through configuration rather than
    through defaults.
    """
    config = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "ukhotels" / "config.py"
    ).read_text(encoding="utf-8")
    assert name in config
    assert '"BQ_SCRAPES_TABLE"' not in config
    assert '"BQ_INDEX_TABLE"' not in config


def _live_expressions(text: str) -> list[str]:
    """Workflow expression lines, excluding YAML comments."""
    return [
        line for line in text.splitlines()
        if "${{" in line and not line.lstrip().startswith("#")
    ]


def test_no_workflow_uses_the_empty_string_ternary():
    """`${{ cond && '' || '--flag' }}` does not do what it looks like.

    GitHub's expression language is JavaScript-like, so `''` is falsy: the `&&`
    yields an empty string, the `||` sees a falsy left side, and the flag is
    returned *whatever the condition was*. The flag can never be suppressed.

    This is not theoretical. `write_panel: true` on the smoke-test workflow still
    passed --dry-run-panel, so the run that was supposed to commit the pinned
    property sample printed it and discarded it -- and reported success
    throughout, because from the workflow's point of view nothing failed. Three
    more instances were found alongside it, one of them in uk-airfares.

    Put the condition in bash, where an empty string is just an empty string.
    """
    offenders = []
    for path in WORKFLOWS:
        for line in _live_expressions(path.read_text(encoding="utf-8")):
            if re.search(r"&&\s*''\s*\|\|", line):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, "empty-string ternary in:\n  " + "\n  ".join(offenders)


def test_conditional_flags_put_a_non_empty_value_in_the_true_branch():
    """The safe shape is `cond && '--flag' || ''`, never the reverse."""
    for path in WORKFLOWS:
        for line in _live_expressions(path.read_text(encoding="utf-8")):
            for match in re.finditer(r"&&\s*('(?:[^']*)')\s*\|\|", line):
                assert match.group(1) != "''", f"{path.name}: {line.strip()}"
