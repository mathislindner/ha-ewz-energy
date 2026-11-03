"""Constants for the ewz Energy integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ewz_energy"

BASE_URL = "https://energie.ewz.ch"

CONF_REFRESH_HOUR = "refresh_hour"
DEFAULT_REFRESH_HOUR = 1  # local time: the portal has the previous day by then

# The portal publishes the previous day with a delay, so every refresh re-reads
# the last few days and overwrites them (late or corrected values replace old ones).
REFRESH_OVERLAP = timedelta(days=3)
INITIAL_DAYS = 14
BACKFILL_CHUNK_DAYS = 14

# The portal is slow (login ~6 s, a data call ~5 s, sometimes much more).
REQUEST_TIMEOUT = 180

STORAGE_VERSION = 1
STATS_LAYOUT = 2  # 2: statistic ids follow the OBIS code (1.29.0 = consumption)
QUARTER = 900
HOUR = 3600

# Interval states published by the portal: W = measured, E = estimated, F = future (placeholder).
VALID_STATES = ("W", "E")

SERVICE_IMPORT_CSV = "import_csv"
SERVICE_BACKFILL = "backfill"
