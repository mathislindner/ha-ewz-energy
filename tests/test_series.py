from datetime import datetime, timezone

from custom_components.ewz_energy.api import format_portal_time, parse_mp_tree
from custom_components.ewz_energy.series import Series, hourly_statistics, parse_portal_csv

CSV = (
    "\ufeff;\"AZ_ZH_Test_8_CH1 / Wirk Abgabe\";;;\n"
    "\"Uhrzeit\";\"Wert\";\"Einheit\";\"Status\";\n"
    "\"25.10.2026 01:45:00\";\"0.100\";\"kWh\";\"W\"\n"
    "\"25.10.2026 02:00:00\";\"0.200\";\"kWh\";\"W\"\n"
    "\"25.10.2026 02:15:00\";\"0.300\";\"kWh\";\"W\"\n"
    "\"25.10.2026 02:30:00\";\"0.400\";\"kWh\";\"W\"\n"
    "\"25.10.2026 02:45:00\";\"0.500\";\"kWh\";\"W\"\n"
    "\"25.10.2026 03:00:00\";\"0.600\";\"kWh\";\"E\"\n"   # end of last summer quarter, labelled on the summer clock
    "\"25.10.2026 02:15:00\";\"0.700\";\"kWh\";\"W\"\n"   # winter time begins
    "\"25.10.2026 02:30:00\";\"0.800\";\"kWh\";\"W\"\n"
    "\"25.10.2026 02:45:00\";\"0.900\";\"kWh\";\"W\"\n"
    "\"25.10.2026 03:00:00\";\"1.000\";\"kWh\";\"W\"\n"
    "\"25.10.2026 03:15:00\";\"0.000\";\"kWh\";\"F\"\n"
)


def test_csv_name_and_dst():
    name, rows = parse_portal_csv(CSV)
    assert name == "Wirk Abgabe"
    ends = [r[0] for r in rows]
    assert all(b - a == 900 for a, b in zip(ends, ends[1:])), "every step must be exactly 15 minutes"
    assert len(set(ends)) == len(ends)


def test_merge_skips_future_and_reports_change():
    s = Series.empty()
    _, rows = parse_portal_csv(CSV)
    first = s.merge(rows)
    assert first == min(r[0] for r in rows)
    assert all(st in ("W", "E") for _v, st in s.data.values())
    assert s.merge(rows) is None, "same data again changes nothing"
    end, val, st = rows[0]
    assert s.merge([(end, val + 1, st)]) == end, "a corrected value is reported"


def test_hourly_sum_is_cumulative_and_skips_partial_last_hour():
    s = Series.empty()
    base = int(datetime(2026, 11, 1, 10, 15, tzinfo=timezone.utc).timestamp())
    s.merge([(base + 900 * i, 0.25, "W") for i in range(6)])  # 10:15..11:30 ends -> hour 10 complete, hour 11 partial
    rows = hourly_statistics(s)
    assert [r["start"].hour for r in rows] == [10]
    assert rows[0]["sum"] == 1.0
    s.merge([(base + 900 * i, 0.25, "W") for i in range(6, 8)])
    rows = hourly_statistics(s)
    assert [r["start"].hour for r in rows] == [10, 11]
    assert rows[1]["sum"] == 2.0 and rows[1]["state"] == 2.0


def test_hourly_from_end_keeps_running_sum_consistent():
    s = Series.empty()
    base = int(datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc).timestamp())
    s.merge([(base + 900 * i, 0.1, "W") for i in range(40)])
    full = hourly_statistics(s)
    part = hourly_statistics(s, from_end=base + 900 * 20)
    assert part == full[len(full) - len(part):]


def test_gap_in_old_hour_is_emitted_not_dropped():
    s = Series.empty()
    base = int(datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc).timestamp())
    vals = [(base + 900 * i, 0.1, "W") for i in range(12) if i != 2]
    s.merge(vals)
    rows = hourly_statistics(s)
    assert len(rows) == 3 and abs(rows[0]["sum"] - 0.3) < 1e-9, rows


def test_portal_time_format_has_local_offset():
    assert format_portal_time(datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)) == "2026-11-02T01:00:00+01:00"
    assert format_portal_time(datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)) == "2026-07-02T02:00:00+02:00"


def test_parse_mp_tree():
    from tests.fake_portal import SIDEBAR

    lines = parse_mp_tree(SIDEBAR)
    assert len(lines) == 1 and lines[0].kind == "consumption" and lines[0].line_id == "42"
    assert parse_mp_tree({"weird": 1}) == []


def test_kind_from_name_when_obis_missing():
    from custom_components.ewz_energy.api import MeasuringLine

    assert MeasuringLine("m", "1", "Wirk Abgabe", None).kind == "unknown"  # ambiguous word
    assert MeasuringLine("m", "1", "Wirk Einspeisung", None).kind == "return"
    assert MeasuringLine("m", "1", "Wirk Abgabe", "OBIS 1-1:1.29.0*255 (kWh)").kind == "consumption"
    assert MeasuringLine("m", "1", "x", "OBIS 1-1:2.29.0*255 (kWh)").kind == "return"
    assert MeasuringLine("m", "1", "Wirk Bezug", None).kind == "consumption"
    assert MeasuringLine("m", "1", "Blindenergie", None).kind == "unknown"
