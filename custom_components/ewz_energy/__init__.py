"""The ewz Energy integration: smart-meter data of ewz (Zürich) from the Netz-Privat portal."""

from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EwzClient
from .const import BASE_URL, DOMAIN, SERVICE_BACKFILL, SERVICE_IMPORT_CSV
from .coordinator import EwzCoordinator
from .series import parse_portal_csv

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("path"): cv.string,
        vol.Optional("line"): cv.string,
    }
)
BACKFILL_SCHEMA = vol.Schema({vol.Optional("days", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=800))})


def _coordinator(hass: HomeAssistant) -> EwzCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("ewz Energy is not set up")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register domain services (they work on the first loaded entry)."""

    async def import_csv(call: ServiceCall) -> dict:
        coordinator = _coordinator(hass)
        path = call.data["path"]
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"{path} is not in an allowed folder; put the file under the Home Assistant config folder"
            )
        text = await hass.async_add_executor_job(lambda: Path(path).read_text(encoding="utf-8-sig"))
        name, rows = parse_portal_csv(text)
        if not coordinator.lines:
            await coordinator.async_refresh()
        state = coordinator.line_for_name(call.data.get("line") or name)
        if state is None:
            raise ServiceValidationError(
                f"cannot tell which line the CSV belongs to (header says {name!r}); pass 'line'"
            )
        changed = await coordinator.async_import_rows(state.line.line_id, rows)
        return {"rows_in_file": len(rows), "values_added_or_changed": changed, "line": state.line.name}

    async def backfill(call: ServiceCall) -> dict:
        coordinator = _coordinator(hass)
        result = await coordinator.async_backfill(call.data["days"])
        return {"values_added": sum(result.values())}

    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_CSV, import_csv, schema=IMPORT_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BACKFILL, backfill, schema=BACKFILL_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = EwzClient(session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD], BASE_URL)
    coordinator = EwzCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

