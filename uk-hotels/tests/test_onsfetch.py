"""Index-day parser. No network: bulletins are fixtures, sessions are stubs.

The parser fixtures below are the verbatim sentences uk-airfares captured from
the live July 2026 bulletin with `--dump` on 2026-08-19. Both projects parse the
same ONS CPI bulletin, so the same sentences sit in every page this one reads --
including the trap, which is about flight prices and appears in the bulletin
regardless of which item is being reconstructed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ukhotels import onsfetch
from ukhotels.onsfetch import IndexDayNotFound, dump_bulletin, parse_index_day

#: The sentence that actually states the collection date. "data ... collected",
#: not "prices collected"; "on or around", not "on".
REAL_JULY = (
    "The consumer price indices are based on prices collected manually from "
    "outlets around the country, information collected centrally over the "
    "internet and by phone, and, from February 2026, data collected from "
    "supermarket scanners at the checkouts or online. The traditionally "
    "sourced data used in this release were collected on or around 14 July "
    "2026. An overview of consumer price statistics is given in our Consumer "
    "price indices, a brief guide: 2026."
)

#: "prices were collected" AND a date -- but the date is a geopolitical event.
#: The old wildcard pattern read it as the index day: this is the 2026-02-28 in
#: every hotels reconciliation failure from 2026-10-21.
TRAP = (
    "Routes are less likely to have multiple daily flights to a destination. "
    "All domestic and European flight prices were collected after the "
    "outbreak of the conflict in the Middle East on 28 February 2026. "
    "Long-haul flight prices collected three months and one month before "
    "departure were collected after that date."
)


class TestTheWordingOnsActuallyUse:
    def test_reads_the_real_july_sentence(self):
        result = parse_index_day(f"<p>{REAL_JULY}</p>", dt.date(2026, 7, 1))
        assert result.index_day == dt.date(2026, 7, 14)
        assert result.ordinal == 2

    def test_trap_does_not_win_when_it_comes_first(self):
        """Page order puts the trap first, as it does in the real bulletin."""
        result = parse_index_day(f"<p>{TRAP}</p><p>{REAL_JULY}</p>", dt.date(2026, 7, 1))
        assert result.index_day == dt.date(2026, 7, 14)

    def test_plain_on_without_the_hedge_still_parses(self):
        html = "<p>Prices were collected on 21 July 2026 for this release.</p>"
        result = parse_index_day(html, dt.date(2026, 7, 1))
        assert result.index_day == dt.date(2026, 7, 21)
        assert result.ordinal == 3

    def test_weekday_prefix_and_ordinal_suffix_parse(self):
        html = "<p>Data were collected on or about Tuesday, 14th July 2026.</p>"
        assert parse_index_day(html, dt.date(2026, 7, 1)).index_day == dt.date(2026, 7, 14)

    def test_a_wrong_date_adjacent_to_collected_is_still_rejected(self):
        """The Tuesday constraint stays the backstop, not the only defence."""
        with pytest.raises(IndexDayNotFound):
            parse_index_day("<p>Data were collected on or around 3 July 2026.</p>",
                            dt.date(2026, 7, 1))


class TestTheSeptember2026Failure:
    """The production failure, reproduced.

    Every hotels reconciliation from 2026-10-21 to 2026-10-25 ended with:

        no index day for September 2026 ... Expected 2026-09-08 or 2026-09-15.
        Dates seen but rejected: [datetime.date(2026, 2, 28)].

    The September 2026 page itself has not been observed from here -- ONS is
    refused by egress policy in the development sandbox -- so the "real" sentence
    in the second test is the July wording with September's date, not verbatim
    September text. The live check is hotels-bulletin-check.yml, which runs the
    parser against the actual page whenever this module changes.
    """

    def test_the_trap_alone_is_rejected_rather_than_accepted(self):
        with pytest.raises(IndexDayNotFound) as exc:
            parse_index_day(f"<p>{TRAP}</p>", dt.date(2026, 9, 1))
        # The fixed pattern no longer reaches the geopolitical date at all, so it
        # is not even offered as a candidate.
        assert "2026, 2, 28" not in str(exc.value)

    def test_september_in_the_july_wording_parses(self):
        sentence = REAL_JULY.replace("14 July 2026", "15 September 2026")
        result = parse_index_day(f"<p>{TRAP}</p><p>{sentence}</p>", dt.date(2026, 9, 1))
        assert result.index_day == dt.date(2026, 9, 15)
        assert result.ordinal == 3


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, text, status=200):
        self._resp = _Resp(text, status)

    def get(self, url, **kw):
        return self._resp


class TestDumpBulletin:
    """The diagnostic that exists because the sandbox cannot reach ons.gov.uk."""

    def test_reports_a_page_with_no_prose(self):
        out = dump_bulletin(dt.date(2026, 9, 1),
                            session=_Session("<html><body><nav>Home</nav></body></html>"))
        assert "pattern matches: 0" in out
        assert "does not contain the bulletin's prose" in out

    def test_marks_an_accepted_candidate(self):
        out = dump_bulletin(dt.date(2026, 7, 1), session=_Session(f"<p>{REAL_JULY}</p>"))
        assert "2026-07-14" in out and "ACCEPTED" in out

    def test_shows_the_collected_on_phrase_even_when_no_pattern_matches(self):
        """The phrase list must include the wording ONS actually use.

        The airfares original searched for "collected on" too; a dump that only
        looked for "index day" would have shown nothing useful on a 2026 page.
        """
        html = "<p>The data were collected on a date stated elsewhere.</p>"
        out = dump_bulletin(dt.date(2026, 9, 1), session=_Session(html))
        assert "[collected on]" in out

    def test_surfaces_accommodation_context(self):
        html = "<p>See the methodology for how hotel prices are collected.</p>"
        out = dump_bulletin(dt.date(2026, 9, 1), session=_Session(html))
        assert "[methodology]" in out
        assert "[hotel]" in out


class TestCliExitCodes:
    """0 parsed, 1 unparseable, 3 not published -- kept apart deliberately.

    The bulletin-check workflow loops over recent months and must not treat
    "ONS have not published October yet" as the failure it exists to catch.
    """

    def _patch(self, monkeypatch, exc=None):
        def fake(month, **kw):
            if exc:
                raise exc
            return onsfetch.IndexDayResult(
                index_month=month, index_day=dt.date(2026, 9, 15), ordinal=3,
                source_url="x", evidence="y",
            )
        monkeypatch.setattr(onsfetch, "fetch_index_day", fake)

    def test_parsed(self, monkeypatch, capsys):
        self._patch(monkeypatch)
        assert onsfetch.main(["--month", "2026-09"]) == 0
        assert capsys.readouterr().out.startswith("2026-09-15")

    def test_unparseable(self, monkeypatch, capsys):
        self._patch(monkeypatch, onsfetch.IndexDayNotFound("nope"))
        assert onsfetch.main(["--month", "2026-09"]) == 1
        captured = capsys.readouterr()
        assert "::error::" in captured.err
        assert "::error::" not in captured.out, "annotations must not reach stdout"

    def test_not_published(self, monkeypatch, capsys):
        self._patch(monkeypatch, onsfetch.BulletinNotPublished("not yet"))
        assert onsfetch.main(["--month", "2026-10"]) == 3
        assert "::notice::" in capsys.readouterr().err
