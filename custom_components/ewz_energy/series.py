"""Pure helpers: the stored 15 minute series, CSV import and hourly statistics.

Convention (verified against a portal CSV export and the portal JSON): every
timestamp is the END of its 15 minute interval, in UTC epoch seconds once parsed.
The interval a value belongs to is therefore ``[end - 900, end)``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Iterable
from zoneinfo import ZoneInfo

from .const import HOUR, QUARTER, VALID_STATES

PORTAL_TZ = ZoneInfo("Europe/Zurich")


@dataclass(slots=True)
class Series:
    """Sorted 15 minute values of one line: end-of-interval epoch -> (kWh, state)."""

    data: dict[int, tuple[float, str]]

    @classmethod
    def empty(cls) -> "Series":
        return cls({})

    @classmethod
    def from_json(cls, raw: dict | None) -> "Series":
        if not raw:
            return cls.empty()
        return cls({int(t): (float(v), s) for t, v, s in zip(raw["t"], raw["v"], raw["s"], strict=True)})

    def to_json(self) -> dict:
        ts = sorted(self.data)
        return {"t": ts, "v": [self.data[t][0] for t in ts], "s": "".join(self.data[t][1][:1] or "?" for t in ts)}

    @property
    def last_end(self) -> int | None:
        return max(self.data) if self.data else None

    def merge(self, items: Iterable[tuple[int, float, str]]) -> int | None:
        """Merge values; return the earliest interval END whose value changed (or None)."""
        earliest: int | None = None
        for end, value, state in items:
            if state not in VALID_STATES:
                continue  # F = placeholder for the future
            if self.data.get(end) != (value, state):
                self.data[end] = (value, state)
                if earliest is None or end < earliest:
                    earliest = end
        return earliest

    def latest(self, count: int) -> list[tuple[int, float, str]]:
        """Return the newest ``count`` values, newest last."""
        return [(t, *self.data[t]) for t in sorted(self.data)[-count:]]


def hourly_statistics(series: Series, from_end: int | None = None) -> list[dict]:
    """Build cumulative hourly statistics rows (``start``, ``state``, ``sum``).

    The running sum always starts at the first stored value, so the result is
    identical whatever ``from_end`` is; ``from_end`` only limits what is returned
    (everything from the hour containing that value on).
    """
    if not series.data:
        return []
    hours: dict[int, list[float]] = {}
    for end, (value, _state) in series.data.items():
        start = end - QUARTER
        hours.setdefault(start - start % HOUR, []).append(value)
    ordered = sorted(hours)
    last_hour = ordered[-1]
    rows: list[dict] = []
    total = 0.0
    first_wanted = None if from_end is None else (from_end - QUARTER) - (from_end - QUARTER) % HOUR
    for hour in ordered:
        quarters = hours[hour]
        # An hour that is still filling up is not emitted yet; permanent gaps in older hours are.
        if len(quarters) < 4 and hour == last_hour:
            continue
        total += sum(quarters)
        if first_wanted is None or hour >= first_wanted:
            rows.append(
                {
                    "start": datetime.fromtimestamp(hour, tz=timezone.utc),
                    "state": round(total, 6),
                    "sum": round(total, 6),
                }
            )
    return rows


def _local_to_utc_epoch(naive: datetime, previous: int | None) -> int:
    """Convert a naive Zurich wall-clock label to a UTC epoch.

    Rows are 15 minutes apart, so the right answer is normally ``previous + 900``.
    That matters on the day clocks go back: the portal labels the end of the last
    summer-time quarter ``03:00`` (the summer clock keeps counting) and then repeats
    ``02:15``..``03:00`` on winter time. Taking the label literally would run backwards.
    So: accept either offset (+2h / +1h) if it equals ``previous + 900``; otherwise
    fall back to the normal local-time conversion (second occurrence if the first
    would not move forward).
    """
    as_utc = int(naive.replace(tzinfo=timezone.utc).timestamp())
    if previous is not None:
        for offset in (7200, 3600):
            if as_utc - offset == previous + QUARTER:
                return as_utc - offset
    first = int(naive.replace(tzinfo=PORTAL_TZ, fold=0).timestamp())
    if previous is None or first > previous:
        return first
    return int(naive.replace(tzinfo=PORTAL_TZ, fold=1).timestamp())


def parse_portal_csv(text: str) -> tuple[str | None, list[tuple[int, float, str]]]:
    """Parse a CSV exported from the portal.

    Returns ``(series_name, rows)``. ``series_name`` is the text after `` / `` in the
    header line (e.g. ``Wirk Abgabe``). Rows in state F (future placeholders) are kept
    here and dropped by :meth:`Series.merge`.
    """
    name: str | None = None
    rows: list[tuple[int, float, str]] = []
    previous: int | None = None
    for rec in csv.reader(StringIO(text.lstrip("\ufeff")), delimiter=";"):
        if not rec:
            continue
        first = rec[0].strip()
        if not first:
            if len(rec) > 1 and " / " in rec[1]:
                name = rec[1].rsplit(" / ", 1)[1].strip()
            continue
        if first == "Uhrzeit" or len(rec) < 4:
            continue
        try:
            local = datetime.strptime(first, "%d.%m.%Y %H:%M:%S")
            value = float(rec[1].replace(",", "."))
        except ValueError:
            continue
        end = _local_to_utc_epoch(local, previous)
        previous = end
        rows.append((end, value, rec[3].strip()))
    return name, rows


def day_bounds(now: datetime, days_back: int) -> tuple[datetime, datetime]:
    """Return (start, end) datetimes for a refresh: ``days_back`` days ago 00:00 to tomorrow 00:00 (Zurich)."""
    local = now.astimezone(PORTAL_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days_back), midnight + timedelta(days=1)
