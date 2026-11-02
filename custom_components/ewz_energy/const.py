"""Constants for the ewz Energy integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ewz_energy"

BASE_URL = "https://energie.ewz.ch"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60  # minutes
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 24 * 60

# The portal publishes the previous day with a delay, so every refresh re-reads
# the last few days and overwrites them (late or corrected values replace old ones).
REFRESH_OVERLAP = timedelta(days=3)
INITIAL_DAYS = 14
BACKFILL_CHUNK_DAYS = 14

# The portal is slow (login ~6 s, a data call ~5 s, sometimes much more).
REQUEST_TIMEOUT = 180

STORAGE_VERSION = 1
QUARTER = 900
HOUR = 3600

# Interval states published by the portal: W = measured, E = estimated, F = future (placeholder).
VALID_STATES = ("W", "E")

SERVICE_IMPORT_CSV = "import_csv"
SERVICE_BACKFILL = "backfill"
