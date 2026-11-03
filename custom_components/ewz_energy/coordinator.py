"""Data coordinator: poll the portal, keep the 15 minute series, feed long-term statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EwzApiError, EwzAuthError, EwzClient, EwzConnectionError, MeasuringLine
from .const import (
    BACKFILL_CHUNK_DAYS,
    CONF_REFRESH_HOUR,
    DEFAULT_REFRESH_HOUR,
    DOMAIN,
    INITIAL_DAYS,
    QUARTER,
    REFRESH_OVERLAP,
    STATS_LAYOUT,
    STORAGE_VERSION,
)
from .series import Series, day_bounds, hourly_statistics

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    type EwzConfigEntry = ConfigEntry[EwzCoordinator]


@dataclass(slots=True)
class LineState:
    """What the entities need to know about one line."""

    line: MeasuringLine
    statistic_id: str
    series: Series


def _slug(line: MeasuringLine, taken: set[str]) -> str:
    base = {"return": "return", "consumption": "consumption"}.get(line.kind, f"line_{line.line_id}")
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    taken.add(slug)
    return slug


class EwzCoordinator(DataUpdateCoordinator[dict[str, LineState]]):
    """Fetch new values on a schedule and keep everything in a local store."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EwzClient) -> None:
        # No fixed interval: the portal has the previous day only after midnight, so ask once a
        # day at ``refresh_hour`` (default 01:00 local time).
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.refresh_hour = int(entry.options.get(CONF_REFRESH_HOUR, DEFAULT_REFRESH_HOUR))
        entry.async_on_unload(
            async_track_time_change(hass, self._scheduled_refresh, hour=self.refresh_hour, minute=0, second=0)
        )
        self.client = client
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._lines: dict[str, LineState] = {}
        self._loaded = False
        self._rebuild_stats = False

    async def _scheduled_refresh(self, _now: datetime) -> None:
        await self.async_request_refresh()

    # ---- persistence -------------------------------------------------
    async def _async_load(self) -> None:
        if self._loaded:
            return
        saved = await self._store.async_load() or {}
        lines = await self._discover_lines(saved)
        taken: set[str] = set()
        for line in lines:
            self._lines[line.line_id] = LineState(
                line=line,
                statistic_id=f"{DOMAIN}:{_slug(line, taken)}",
                series=Series.from_json(saved.get("series", {}).get(line.line_id)),
            )
        # Old layout (v0.1.0) published the consumption line as ewz_energy:return: drop that
        # statistic and rebuild everything from the stored series.
        if saved.get("stats_layout", 1) < STATS_LAYOUT:
            current = {ls.statistic_id for ls in self._lines.values()}
            stale = [f"{DOMAIN}:return", f"{DOMAIN}:consumption"]
            if any(s not in current for s in stale):
                get_instance(self.hass).async_clear_statistics([s for s in stale if s not in current])
            self._rebuild_stats = True
        self._loaded = True

    async def _discover_lines(self, saved: dict) -> list[MeasuringLine]:
        try:
            lines = await self.client.get_lines()
        except EwzAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EwzConnectionError, EwzApiError) as err:
            raise UpdateFailed(str(err)) from err
        if not lines:
            raise UpdateFailed("the account has no measuring lines")
        return lines

    async def _async_save(self) -> None:
        await self._store.async_save({"stats_layout": STATS_LAYOUT, "series": {k: v.series.to_json() for k, v in self._lines.items()}})

    # ---- fetching ----------------------------------------------------
    async def _fetch_range(self, state: LineState, start: datetime, end: datetime) -> int | None:
        """Fetch [start, end) in chunks and merge. Returns the earliest changed END."""
        earliest: int | None = None
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=BACKFILL_CHUNK_DAYS), end)
            intervals = await self.client.get_intervals(state.line, cursor, chunk_end)
            changed = state.series.merge((i.end, i.value, i.state) for i in intervals)
            if changed is not None and (earliest is None or changed < earliest):
                earliest = changed
            cursor = chunk_end
        return earliest

    async def _async_update_data(self) -> dict[str, LineState]:
        await self._async_load()
        now = dt_util.utcnow()
        if self._rebuild_stats:
            for state in self._lines.values():
                self._push_statistics(state, None)
            self._rebuild_stats = False
        for state in self._lines.values():
            last = state.series.last_end
            if last is None:
                start, end = day_bounds(now, INITIAL_DAYS)
            else:
                start = dt_util.utc_from_timestamp(last) - REFRESH_OVERLAP
                _, end = day_bounds(now, 0)
            try:
                earliest = await self._fetch_range(state, start, end)
            except EwzAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (EwzConnectionError, EwzApiError) as err:
                raise UpdateFailed(f"{state.line.name}: {err}") from err
            if earliest is not None:
                self._push_statistics(state, earliest)
        await self._async_save()
        return self._lines

    # ---- statistics ---------------------------------------------------
    def _push_statistics(self, state: LineState, from_end: int | None = None) -> None:
        rows = hourly_statistics(state.series, from_end)
        if not rows:
            return
        metadata = {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": f"ewz {state.line.name}",
            "source": DOMAIN,
            "statistic_id": state.statistic_id,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        }
        async_add_external_statistics(self.hass, metadata, rows)  # type: ignore[arg-type]

    # ---- services ------------------------------------------------------
    async def async_import_rows(self, line_id: str, rows: list[tuple[int, float, str]]) -> int:
        """Merge externally supplied rows (CSV import). Returns the number of changed values."""
        await self._async_load()
        state = self._lines[line_id]
        before = dict(state.series.data)
        earliest = state.series.merge(rows)
        if earliest is not None:
            self._push_statistics(state, None)  # full rebuild: imported data may predate stored data
            await self._async_save()
            self.async_set_updated_data(self._lines)
        return sum(1 for k, v in state.series.data.items() if before.get(k) != v)

    async def async_backfill(self, days: int) -> dict[str, int]:
        """Fetch the last ``days`` days from the portal and rebuild statistics."""
        await self._async_load()
        start, end = day_bounds(dt_util.utcnow(), days)
        result: dict[str, int] = {}
        for lid, state in self._lines.items():
            before = len(state.series.data)
            try:
                await self._fetch_range(state, start, end)
            except EwzAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            self._push_statistics(state, None)
            result[lid] = len(state.series.data) - before
        await self._async_save()
        self.async_set_updated_data(self._lines)
        return result

    def line_for_name(self, name: str | None) -> LineState | None:
        """Pick a line by its portal name, or the only line."""
        if name:
            for st in self._lines.values():
                if st.line.name.lower() == name.lower():
                    return st
        if len(self._lines) == 1:
            return next(iter(self._lines.values()))
        return None

    @property
    def lines(self) -> dict[str, LineState]:
        return self._lines

    @staticmethod
    def quarter_seconds() -> int:
        return QUARTER
