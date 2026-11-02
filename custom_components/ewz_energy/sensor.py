"""Sensors for the ewz Energy integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, QUARTER
from .coordinator import EwzCoordinator, LineState


@dataclass(frozen=True, kw_only=True)
class EwzSensorDescription(SensorEntityDescription):
    """Describe an ewz sensor."""

    value_fn: Callable[[LineState], Any]
    attrs_fn: Callable[[LineState], dict[str, Any]] | None = None


def _last(state: LineState):
    rows = state.series.latest(1)
    return rows[0] if rows else None


def _last_quarter(state: LineState) -> float | None:
    row = _last(state)
    return None if row is None else round(row[1], 3)


def _last_quarter_attrs(state: LineState) -> dict[str, Any]:
    rows = state.series.latest(4)
    return {
        "interval_end": dt_util.as_local(dt_util.utc_from_timestamp(rows[-1][0])).isoformat() if rows else None,
        "reading_state": rows[-1][2] if rows else None,
        "last_four_quarters": [
            {
                "interval_end": dt_util.as_local(dt_util.utc_from_timestamp(end)).isoformat(),
                "kwh": round(value, 3),
                "state": st,
            }
            for end, value, st in rows
        ],
        "last_four_kwh": [round(v, 3) for _e, v, _s in rows],
        "statistic_id": state.statistic_id,
    }


def _last_hour(state: LineState) -> float | None:
    rows = state.series.latest(4)
    return round(sum(v for _e, v, _s in rows), 3) if len(rows) == 4 else None


def _today(state: LineState) -> float | None:
    if not state.series.data:
        return None
    midnight = dt_util.start_of_local_day()
    start = int(midnight.timestamp())
    # a value belongs to the day its interval STARTED in: end > midnight
    total = sum(v for end, (v, _s) in state.series.data.items() if end > start)
    return round(total, 3)


def _total(state: LineState) -> float | None:
    if not state.series.data:
        return None
    return round(sum(v for v, _s in state.series.data.values()), 3)


def _last_data(state: LineState) -> datetime | None:
    row = _last(state)
    return None if row is None else datetime.fromtimestamp(row[0], tz=timezone.utc)


DESCRIPTIONS: tuple[EwzSensorDescription, ...] = (
    EwzSensorDescription(
        key="last_quarter",
        translation_key="last_quarter",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_last_quarter,
        attrs_fn=_last_quarter_attrs,
    ),
    EwzSensorDescription(
        key="last_hour",
        translation_key="last_hour",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_last_hour,
    ),
    EwzSensorDescription(
        key="today",
        translation_key="today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_today,
    ),
    EwzSensorDescription(
        key="total",
        translation_key="total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_total,
    ),
    EwzSensorDescription(
        key="last_data",
        translation_key="last_data",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=True,
        value_fn=_last_data,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create sensors for every measuring line."""
    coordinator: EwzCoordinator = entry.runtime_data
    async_add_entities(
        EwzSensor(coordinator, entry, line_id, desc)
        for line_id in coordinator.data
        for desc in DESCRIPTIONS
    )


class EwzSensor(CoordinatorEntity[EwzCoordinator], SensorEntity):
    """One value of one measuring line."""

    entity_description: EwzSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: EwzCoordinator, entry: ConfigEntry, line_id: str, desc: EwzSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._line_id = line_id
        line = coordinator.data[line_id].line
        # The meter number is deliberately not used: ids stay anonymous.
        self._attr_unique_id = f"{entry.entry_id}_{line_id}_{desc.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{entry.entry_id}_{line_id}")},
            name=f"ewz {line.name}",
            manufacturer="ewz",
            model="Smart meter load profile",
        )

    @property
    def _state(self) -> LineState | None:
        return self.coordinator.data.get(self._line_id) if self.coordinator.data else None

    @property
    def native_value(self) -> Any:
        state = self._state
        return None if state is None else self.entity_description.value_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self._state
        fn = self.entity_description.attrs_fn
        return fn(state) if (state is not None and fn is not None) else None
