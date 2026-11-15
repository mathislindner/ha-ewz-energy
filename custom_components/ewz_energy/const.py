"""Constants for the ewz Energy integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ewz_energy"

BASE_URL = "https://energie.ewz.ch"

CONF_REFRESH_HOUR = "refresh_hour"
CONF_PRICE = "price"
DEFAULT_PRICE = 0.3093  # CHF/kWh incl. VAT; ElCom 2026 H1 standard for Zuerich, an estimate
DEFAULT_REFRESH_HOUR = 1  # local time: the portal has the previous day by then

# Optional high/low tariff (time of use) for the cost statistic. ewz charges the high tariff
# Monday to Saturday 06:00-22:00 local time and the low tariff otherwise: unchanged since
# 2009 (Stadt Zurich tariff, council decision of 2008-09-03) up to the 2027 tariffs.
# With it on, CONF_PRICE is the high tariff. Default low price: ewz.natur 2026, private customers
# in the city of Zurich, 15.90 Rp/kWh + 8.1 % VAT (high: 26.25 Rp); they change every year.
CONF_TIME_OF_USE = "time_of_use"
CONF_PRICE_LOW = "price_low"
DEFAULT_PRICE_LOW = 0.1719
HIGH_TARIFF_DAYS = frozenset(range(6))  # datetime.weekday(): Monday = 0 .. Saturday = 5
HIGH_TARIFF_HOURS = range(6, 22)

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
