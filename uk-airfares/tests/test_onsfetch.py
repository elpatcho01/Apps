import datetime as dt

import pytest

from ukairfares.onsfetch import IndexDayNotFound, bulletin_url, parse_index_day

AUGUST_2026 = dt.date(2026, 8, 1)
# Tuesdays in August 2026: 4, 11, 18, 25. Valid index days: 11 (2nd), 18 (3rd).


def wrap(body: str) -> str:
    return f"<html><body><section id='methodology'><p>{body}</p></section></body></html>"


class TestBulletinUrl:
    def test_url_shape(self):
        assert bulletin_url(dt.date(2026, 9, 1)).endswith("/consumerpriceinflation/september2026")

    def test_lowercased_month(self):
        assert "/august2026" in bulletin_url(AUGUST_2026)


class TestParsing:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ("The index day for August 2026 was 11 August 2026.", dt.date(2026, 8, 11)),
            ("The index day was Tuesday 18 August 2026.", dt.date(2026, 8, 18)),
            ("Index date of 11 August 2026 applied to this collection.", dt.date(2026, 8, 11)),
            ("The index day is 18 August.", dt.date(2026, 8, 18)),
            ("Prices were collected on Tuesday 11 August 2026.", dt.date(2026, 8, 11)),
            ("The index day for this period was 11th August.", dt.date(2026, 8, 11)),
        ],
    )
    def test_recognised_phrasings(self, body, expected):
        result = parse_index_day(wrap(body), AUGUST_2026)
        assert result.index_day == expected
        assert result.index_month == AUGUST_2026

    def test_ordinal_reported(self):
        assert parse_index_day(wrap("The index day was 11 August 2026."), AUGUST_2026).ordinal == 2
        assert parse_index_day(wrap("The index day was 18 August 2026."), AUGUST_2026).ordinal == 3

    def test_evidence_captured(self):
        result = parse_index_day(wrap("The index day was 11 August 2026."), AUGUST_2026)
        assert "index day" in result.evidence.lower()

    def test_handles_html_entities_and_tags(self):
        html = "<p>The <b>index&nbsp;day</b> was <em>11 August 2026</em>.</p>"
        assert parse_index_day(html, AUGUST_2026).index_day == dt.date(2026, 8, 11)

    def test_ignores_script_content(self):
        html = (
            "<script>var indexDay = '4 August 2026';</script>"
            "<p>The index day was 18 August 2026.</p>"
        )
        assert parse_index_day(html, AUGUST_2026).index_day == dt.date(2026, 8, 18)


class TestTuesdayConstraintRejectsMisparses:
    """The 2nd/3rd-Tuesday rule is the real defence against a bad parse."""

    def test_rejects_publication_date(self):
        # 19 August 2026 is a Wednesday -- a plausible publication date, not an index day.
        with pytest.raises(IndexDayNotFound):
            parse_index_day(wrap("The index day was 19 August 2026."), AUGUST_2026)

    def test_rejects_first_tuesday(self):
        # 4 August is a Tuesday but the 1st, which ONS never use.
        with pytest.raises(IndexDayNotFound):
            parse_index_day(wrap("The index day was 4 August 2026."), AUGUST_2026)

    def test_rejects_fourth_tuesday(self):
        with pytest.raises(IndexDayNotFound):
            parse_index_day(wrap("The index day was 25 August 2026."), AUGUST_2026)

    def test_rejects_wrong_month(self):
        with pytest.raises(IndexDayNotFound):
            parse_index_day(wrap("The index day was 11 September 2026."), AUGUST_2026)

    def test_picks_valid_candidate_over_invalid_one(self):
        body = (
            "This bulletin was released on 16 September 2026. "
            "The next release is 14 October 2026. "
            "The index day was 18 August 2026."
        )
        assert parse_index_day(wrap(body), AUGUST_2026).index_day == dt.date(2026, 8, 18)

    def test_error_lists_expected_days(self):
        with pytest.raises(IndexDayNotFound, match="2026-08-11"):
            parse_index_day(wrap("The index day was 19 August 2026."), AUGUST_2026)

    def test_no_dates_at_all(self):
        with pytest.raises(IndexDayNotFound, match="No dates matched"):
            parse_index_day(wrap("Nothing relevant here."), AUGUST_2026)

    def test_invalid_calendar_date_is_skipped(self):
        with pytest.raises(IndexDayNotFound):
            parse_index_day(wrap("The index day was 31 February 2026."), AUGUST_2026)


class TestAcrossManyMonths:
    def test_only_second_and_third_tuesdays_ever_accepted(self):
        import calendar

        from ukairfares.onscal import candidate_index_days

        for month_num in range(1, 13):
            month = dt.date(2026, month_num, 1)
            name = calendar.month_name[month_num]
            valid = set(candidate_index_days(2026, month_num))
            for day in range(1, calendar.monthrange(2026, month_num)[1] + 1):
                body = f"The index day was {day} {name} 2026."
                candidate = dt.date(2026, month_num, day)
                if candidate in valid:
                    assert parse_index_day(wrap(body), month).index_day == candidate
                else:
                    with pytest.raises(IndexDayNotFound):
                        parse_index_day(wrap(body), month)


class TestDumpBulletin:
    """The diagnostic that exists because the sandbox cannot reach ons.gov.uk."""

    class Resp:
        def __init__(self, text, status=200):
            self.text, self.status_code = text, status

    class Session:
        def __init__(self, text):
            self.text = text

        def get(self, url, **kw):
            return TestDumpBulletin.Resp(self.text)

    def test_reports_a_page_with_no_prose(self):
        from ukairfares.onsfetch import dump_bulletin

        out = dump_bulletin(dt.date(2026, 7, 1),
                            session=self.Session("<html><body><nav>Home</nav></body></html>"))
        assert "pattern matches: 0" in out
        assert "does not contain the bulletin's prose" in out

    def test_shows_rejected_candidates_with_context(self):
        from ukairfares.onsfetch import dump_bulletin

        html = "<p>The index date was 28 February for the reference period.</p>"
        out = dump_bulletin(dt.date(2026, 7, 1), session=self.Session(html))
        assert "2026-02-28" in out
        assert "rejected" in out
        assert "reference period" in out

    def test_marks_an_accepted_candidate(self):
        from ukairfares.onsfetch import dump_bulletin

        html = "<p>The index day was 14 July 2026 for this collection.</p>"
        out = dump_bulletin(dt.date(2026, 7, 1), session=self.Session(html))
        assert "2026-07-14" in out and "ACCEPTED" in out

    def test_surfaces_key_phrases_even_when_no_date_matches(self):
        from ukairfares.onsfetch import dump_bulletin

        html = "<p>See the methodology for how air fares are collected.</p>"
        out = dump_bulletin(dt.date(2026, 7, 1), session=self.Session(html))
        assert "[methodology]" in out
        assert "[air fare]" in out


class TestJuly2026BulletinWording:
    """Fixtures are verbatim from the live July 2026 bulletin, via `--dump`.

    The parser failed on this page in production on 2026-08-19. Both sentences
    below are real and sit in the same bulletin; one carries the index day and
    one is a trap that the previous pattern preferred.
    """

    #: The sentence that actually states the collection date. Note "data ...
    #: collected", not "prices collected", and "on or around" rather than "on".
    REAL = (
        "The consumer price indices are based on prices collected manually from "
        "outlets around the country, information collected centrally over the "
        "internet and by phone, and, from February 2026, data collected from "
        "supermarket scanners at the checkouts or online. The traditionally "
        "sourced data used in this release were collected on or around 14 July "
        "2026. An overview of consumer price statistics is given in our Consumer "
        "price indices, a brief guide: 2026."
    )

    #: Contains "prices were collected" AND a date, but the date is a reference
    #: to a geopolitical event. The old wildcard pattern read it as the index day.
    TRAP = (
        "Routes are less likely to have multiple daily flights to a destination. "
        "All domestic and European flight prices were collected after the "
        "outbreak of the conflict in the Middle East on 28 February 2026. "
        "Long-haul flight prices collected three months and one month before "
        "departure were collected after that date."
    )

    def test_reads_the_real_wording(self):
        from ukairfares.onsfetch import parse_index_day

        result = parse_index_day(f"<p>{self.REAL}</p>", dt.date(2026, 7, 1))
        assert result.index_day == dt.date(2026, 7, 14)
        assert result.ordinal == 2

    def test_trap_sentence_alone_yields_nothing(self):
        from ukairfares.onsfetch import IndexDayNotFound, parse_index_day

        with pytest.raises(IndexDayNotFound):
            parse_index_day(f"<p>{self.TRAP}</p>", dt.date(2026, 7, 1))

    def test_trap_does_not_win_when_both_are_present(self):
        """Page order puts the trap first, as it does in the real bulletin."""
        from ukairfares.onsfetch import parse_index_day

        result = parse_index_day(
            f"<p>{self.TRAP}</p><p>{self.REAL}</p>", dt.date(2026, 7, 1)
        )
        assert result.index_day == dt.date(2026, 7, 14)

    def test_plain_on_without_the_hedge_still_parses(self):
        from ukairfares.onsfetch import parse_index_day

        html = "<p>Prices were collected on 21 July 2026 for this release.</p>"
        result = parse_index_day(html, dt.date(2026, 7, 1))
        assert result.index_day == dt.date(2026, 7, 21)
        assert result.ordinal == 3

    def test_weekday_prefix_and_ordinal_suffix_parse(self):
        from ukairfares.onsfetch import parse_index_day

        html = "<p>Data were collected on or about Tuesday, 14th July 2026.</p>"
        assert parse_index_day(html, dt.date(2026, 7, 1)).index_day == dt.date(2026, 7, 14)

    def test_a_wrong_date_adjacent_to_collected_is_still_rejected(self):
        """The Tuesday constraint remains the backstop, not the only defence."""
        from ukairfares.onsfetch import IndexDayNotFound, parse_index_day

        html = "<p>Data were collected on or around 3 July 2026.</p>"
        with pytest.raises(IndexDayNotFound):
            parse_index_day(html, dt.date(2026, 7, 1))
