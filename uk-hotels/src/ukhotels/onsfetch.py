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
    re.compile(
        r"prices?\s+(?:were\s+)?collected[^.]{0,120}?on\s+(?:\w+day\s+)?"
        r"(\d{1,2})\s+(\w+)\s+(\d{4})",
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
