"""End-to-end tests: real Home Assistant core + fake ewz portal (made-up data)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import recorder as recorder_helper
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ewz_energy.const import DOMAIN

from .fake_portal import FakePortal

@pytest.fixture
async def portal(socket_enabled, aiohttp_server):
    p = FakePortal()
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    # data up to midnight today (local), like the real portal (one day behind)
    midnight = dt_util.now(ZoneInfo("Europe/Zurich")).replace(hour=0, minute=0, second=0, microsecond=0)
    first = midnight - timedelta(days=5) + timedelta(minutes=15)
    p.fill(first.astimezone(timezone.utc), 5 * 96, value=0.1)
    server = await aiohttp_server(p.app())
    p.url = f"http://{server.host}:{server.port}"
    return p


@pytest.fixture
def patch_base(portal):
    with patch("custom_components.ewz_energy.BASE_URL", portal.url), patch(
        "custom_components.ewz_energy.config_flow.BASE_URL", portal.url
    ):
        yield


async def _setup(hass, portal) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"email": portal.email, "password": portal.password}, unique_id="a@b.ch")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_sensors_and_statistics(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    entry = await _setup(hass, portal)
    assert entry.state is ConfigEntryState.LOADED
    assert portal.logins == 1

    st = hass.states.get("sensor.ewz_wirk_abgabe_last_15_minutes")
    assert st is not None and float(st.state) == 0.1
    attrs = st.attributes
    assert attrs["last_four_kwh"] == [0.1, 0.1, 0.1, 0.1]
    assert len(attrs["last_four_quarters"]) == 4
    assert float(hass.states.get("sensor.ewz_wirk_abgabe_last_hour").state) == 0.4
    total = float(hass.states.get("sensor.ewz_wirk_abgabe_total").state)
    assert total > 0

    await async_wait_recording_done(hass)
    stats = await hass.async_add_executor_job(
        lambda: statistics_during_period(
            hass, dt_util.utcnow() - timedelta(days=30), None, {"ewz_energy:consumption"}, "hour", None, {"sum", "state"}
        )
    )
    rows = stats["ewz_energy:consumption"]
    assert rows, "hourly statistics were imported"
    sums = [r["sum"] for r in rows]
    assert sums == sorted(sums), "cumulative sum never decreases"
    assert abs(sums[-1] - total) < 0.5, "statistics sum matches the stored total (last partial hour excepted)"


async def test_bad_password_gives_auth_error_state(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    portal.password = "other"
    entry = MockConfigEntry(domain=DOMAIN, data={"email": portal.email, "password": "wrong"}, unique_id="a@b.ch")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_refresh_rereads_overlap_and_picks_up_corrections(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    entry = await _setup(hass, portal)
    coordinator = entry.runtime_data
    first_calls = len(portal.data_calls)
    # the portal corrects yesterday's last value and publishes one new day
    last_end = max(portal.values)
    portal.values[last_end] = (0.9, "W")
    newest = last_end + timedelta(minutes=15)
    portal.fill(newest, 96, value=0.2)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(portal.data_calls) > first_calls
    state = next(iter(coordinator.lines.values()))
    assert state.series.data[int(last_end.timestamp())] == (0.9, "W")
    assert state.series.last_end == int(max(portal.values).timestamp())


async def test_expired_session_logs_in_again(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    entry = await _setup(hass, portal)
    portal.expire_next = True
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success
    assert portal.logins == 2


async def test_future_placeholders_are_ignored(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    portal.fill(max(portal.values) + timedelta(minutes=15), 96, value=0.0, state="F")
    entry = await _setup(hass, portal)
    state = next(iter(entry.runtime_data.lines.values()))
    assert all(s in ("W", "E") for _v, s in state.series.data.values())
    assert state.series.last_end == int(max(t for t, (_v, s) in portal.values.items() if s == "W").timestamp())


async def test_import_csv_service(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base, tmp_path):
    entry = await _setup(hass, portal)
    csv_text = (
        "\ufeff;\"AZ_ZH_Test_8_CH1 / Wirk Abgabe\";;;\n\"Uhrzeit\";\"Wert\";\"Einheit\";\"Status\";\n"
        "\"01.01.2026 00:15:00\";\"0.500\";\"kWh\";\"W\"\n\"01.01.2026 00:30:00\";\"0.500\";\"kWh\";\"W\"\n"
        "\"01.01.2026 00:45:00\";\"0.500\";\"kWh\";\"W\"\n\"01.01.2026 01:00:00\";\"0.500\";\"kWh\";\"W\"\n"
        "\"01.01.2026 01:15:00\";\"0.000\";\"kWh\";\"F\"\n"
    )
    f = tmp_path / "x.csv"
    f.write_text(csv_text, encoding="utf-8")
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    res = await hass.services.async_call(
        DOMAIN, "import_csv", {"path": str(f)}, blocking=True, return_response=True
    )
    assert res["rows_in_file"] == 5 and res["values_added_or_changed"] == 4
    # importing the same file again changes nothing
    res = await hass.services.async_call(DOMAIN, "import_csv", {"path": str(f)}, blocking=True, return_response=True)
    assert res["values_added_or_changed"] == 0
    await async_wait_recording_done(hass)
    stats = await hass.async_add_executor_job(
        lambda: statistics_during_period(
            hass, datetime(2025, 12, 31, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), {"ewz_energy:consumption"}, "hour", None, {"sum"}
        )
    )
    assert [round(r["sum"], 3) for r in stats["ewz_energy:consumption"]] == [2.0]


async def test_import_csv_outside_allowed_dir_is_refused(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base, tmp_path):
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass, portal)
    f = tmp_path / "x.csv"
    f.write_text("x")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "import_csv", {"path": str(f)}, blocking=True)


async def test_config_flow_ok_and_errors(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "a@b.ch", "password": "nope"})
    assert bad["errors"] == {"base": "invalid_auth"}
    ok = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "a@b.ch", "password": "pw"})
    assert ok["type"] == "create_entry" and ok["title"] == "a@b.ch"
    await hass.async_block_till_done()
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    dup = await hass.config_entries.flow.async_configure(again["flow_id"], {"email": "A@b.ch", "password": "pw"})
    assert dup["type"] == "abort" and dup["reason"] == "already_configured"


async def test_options_interval(recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations, portal, patch_base):
    entry = await _setup(hass, portal)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"refresh_hour": 3})
    await hass.async_block_till_done()
    assert entry.options["refresh_hour"] == 3
    assert entry.runtime_data.refresh_hour == 3 and entry.runtime_data.update_interval is None
