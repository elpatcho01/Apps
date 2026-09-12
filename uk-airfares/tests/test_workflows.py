"""Static checks on this project's own workflow files.

Scoped to airfares-*.yml for the same reason actionlint is: two projects share
this directory, and a neighbour's mistakes turning this suite red is how a suite
gets ignored. Ours breaking is our problem to see.

These are checks actionlint cannot make. The expressions below are all valid
GitHub syntax -- they parse, they lint clean, they just evaluate to something
other than what they appear to say.
"""

import pathlib
import re

WORKFLOWS = sorted(
    (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows")
    .glob("airfares-*.yml")
)

# Bare `true`/`false`, not the quoted strings. `[ "$x" != "false" ]` in a run
# block is the correct bash form and must not trip this.
BOOLEAN_LITERAL_COMPARISON = re.compile(r"(?:==|!=)\s*(?:true|false)\b")


def _live_lines(text: str) -> list[str]:
    """Every line that is not a YAML comment.

    The comments explaining these traps quote the broken expressions verbatim,
    so a check that read them would fail on its own documentation.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_workflows_are_present():
    assert WORKFLOWS, "no airfares workflow files found"


def test_no_workflow_compares_against_a_boolean_literal():
    """`inputs.commit != false` is false on every scheduled run.

    `inputs` is populated for workflow_dispatch. On a `schedule` event
    `inputs.commit` is null, and GitHub coerces both null and false to 0 before
    comparing, so `null != false` evaluates to FALSE -- the opposite of the
    intent, and specifically on the runs nobody is watching.

    This shipped in the monthly digest's commit step and went unnoticed because
    the four manual dispatches all worked. The first scheduled run, 2026-09-02,
    generated the digest, exported the analytics JSON, and committed neither.
    The run was green and the step was marked "skipped", which reads like a
    condition doing its job. Three weeks of collected fares stayed unexported,
    and because that commit is what resets GitHub's 60-day inactivity timer,
    every schedule in the repository was on course to be disabled.

    Put the condition in bash, where empty is just empty and nothing is coerced.
    """
    offenders = []
    for path in WORKFLOWS:
        for line in _live_lines(path.read_text(encoding="utf-8")):
            if BOOLEAN_LITERAL_COMPARISON.search(line):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "comparison against a bare boolean literal in:\n  " + "\n  ".join(offenders)
    )


def test_the_digest_commit_step_is_not_gated_on_an_expression():
    """The one step whose skipping takes the collection schedules down with it.

    Committing the digest is what keeps GitHub from disabling every cron in this
    repository after 60 days without a commit. Any `if:` on it beyond `always()`
    is a way for that to stop happening quietly, so the gate belongs in the run
    block where it is visible in the log.
    """
    digest = (
        pathlib.Path(__file__).resolve().parents[2]
        / ".github" / "workflows" / "airfares-monthly-digest.yml"
    ).read_text(encoding="utf-8")
    commit_step = digest.split("- name: Commit the report", 1)[1]
    condition = re.search(r"^\s*if:\s*(.+)$", commit_step, re.M).group(1).strip()
    assert condition == "always()", f"unexpected gate on the commit step: {condition}"
