"""Locate the confirmed index day in an ONS CPI bulletin.

ONS withhold the index day in advance -- they consider publishing it ahead of
time commercially sensitive, on the grounds that retailers could game it -- and
confirm it retrospectively in the *following* month's bulletin, in the
methodology section.

Bulletin URLs follow a stable pattern:
    https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/
        consumerpriceinflation/<month><year>          e.g. .../september2026

Parsing prose for a date is inherently brittle, so this module leans on a strong
structural check rather than on the regex being clever: **a valid index day is
the 2nd or 3rd Tuesday of the target month.** That single constraint rejects
almost every possible mis-parse -- publication dates, reference-period dates,
next-release dates -- because none of them reliably land on one of exactly two
days in the month. A candidate that fails it is discarded no matter how
confident the surrounding wording looked.

If nothing parses, this raises rather than guessing. A wrong index day would
silently corrupt every reconstruction built on it, which is far worse than a
loud failure that a human resolves by reading the bulletin.
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
    "uk-airfares-nowcasting/1.0 (research pipeline; contact via repository owner)"
)

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

#: Wording ONS have used for this over the years. Ordered most to least
#: specific; every hit is still validated against the Tuesday constraint.
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
)


class IndexDayNotFound(RuntimeError):
    """The bulletin did not yield a defensible index day."""


@dataclasses.dataclass(frozen=True, slots=True)
class IndexDayResult:
    index_month: dt.date
    index_day: dt.date
    ordinal: int
    source_url: str
    #: The sentence the date was read out of, kept so a human can audit the parse.
    evidence: str


def bulletin_url(month: dt.date) -> str:
    """URL of the bulletin that *confirms* the index day for `month`.

    ONS confirm month M's index day in the bulletin published for month M,
    which appears roughly mid-M+1. The bulletin is named for its reference
    month, so the URL uses M itself.
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


class BulletinNotPublished(RuntimeError):
    """The bulletin is not out yet. Expected, not an error condition."""
