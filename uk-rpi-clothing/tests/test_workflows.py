import pathlib
import re

WF = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _ours():
    return sorted(WF.glob("rpiclothing-*.yml"))


def test_no_context_compared_against_bare_boolean_literal():
    # GitHub coerces null and false to 0: `inputs.x != false` is false on a
    # schedule, which silently skipped the uk-airfares commit step for weeks.
    pat = re.compile(r"\$\{\{[^}]*(==|!=)\s*(true|false)\b")
    for p in _ours():
        assert not pat.search(p.read_text()), p.name


def test_every_job_has_a_timeout():
    for p in _ours():
        text = p.read_text()
        jobs = re.findall(r"^  [a-z0-9_-]+:\n    runs-on:", text, re.M)
        assert text.count("timeout-minutes:") >= len(jobs), p.name


def test_monthly_commit_step_is_gated_in_bash_not_if():
    text = (WF / "rpiclothing-monthly.yml").read_text()
    step = text[text.index("- name: Commit the digest"):]
    assert "if: always()" in step.split("run:")[0]
    assert 'if [ "${COMMIT}" = "false" ]' in step
