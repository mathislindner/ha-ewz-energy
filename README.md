# ewz Energy for Home Assistant

Smart-meter data of **ewz (Stadt Zürich)** in Home Assistant. ewz has no public API, so this
integration logs in to the customer portal (`energie.ewz.ch`, Stadt Zürich single sign-on) the way a
browser does and reads the same JSON the portal's table view uses. No headless browser needed.

## What you get

For every load-profile line of your account (for example *Wirk Abgabe*, OBIS 1.29.0 = energy delivered
to you, i.e. your consumption; the integration classifies lines by their OBIS code):

| Entity | Meaning |
| --- | --- |
| `Last 15 minutes` | newest 15 minute value (kWh). Attributes: `last_four_quarters`, `last_four_kwh`, `interval_end`, `reading_state` |
| `Last hour` | sum of the newest four quarters |
| `Today` | energy since local midnight |
| `Total` | everything stored so far |
| `Latest reading` | timestamp of the newest value |

It also writes **long-term statistics** (`ewz_energy:return`, `ewz_energy:consumption`) with the real
hourly timestamps, so the **Energy dashboard** shows the right hours. Pick those statistics there.

## Important: the data is about a day old

The portal publishes a day's values after the day is over. Polling more often
brings nothing new, so the integration refreshes **once a day at 01:00** (local time, changeable in
the options) and every refresh re-reads the last 3 days and overwrites them (late or corrected
values replace old ones). The last four quarters sensor therefore shows the last
published quarters, not "right now".

## Cost

The consumption line also gets a cumulative **cost statistic** `ewz_energy:consumption_cost` (CHF).
In the Energy dashboard, pick it as the consumption's "entity tracking the total costs". Set the price
in the options (CHF/kWh incl. VAT; default 0.3093, the ElCom 2026 estimate for Zurich).

ewz charges a **high tariff** Monday to Saturday 06:00-22:00 and a **low tariff** at all other hours.
The portal's load profile (OBIS 1.29.0) has no tariff split, so the integration can price each hour by
its local time instead: turn on *High/low tariff* in the options. *Price* is then the high tariff and
*Low-tariff price* applies to all other hours. For example, ewz.natur 2026 for private customers in the city
of Zurich (energy, grid and levies):

| 2026 | Rp/kWh excl. VAT | CHF/kWh incl. 8.1 % VAT |
| --- | --- | --- |
| High tariff | 26.25 | 0.2838 |
| Low tariff | 15.90 | 0.1719 |

Other products, Graubünden and the fixed monthly metering fee (not part of the cost statistic) are in
the [ewz tariff sheet](https://www.ewz.ch/dam/ewz/meinewz/Dokumentencenter/2026/Uebersicht_Tarife_ewz-2026.pdf).
Changing a price or switching the tariff re-prices **all** stored data, including earlier years, at
today's prices.

## Install

HACS: add this repository as a custom repository (category *Integration*), install, restart.
Manual: copy `custom_components/ewz_energy` into your config folder, restart.

Then *Settings → Devices & services → Add integration → ewz Energy* and enter the e-mail address and
password you use on `energie.ewz.ch`. Accounts that only log in with **AGOV** or the Zürich Access App
are not supported (those need a phone approval each time).

The portal is slow: the first setup can take 10 to 30 seconds.

## Import old data

Export a CSV from the portal's table view (button *CSV*), put it in your Home Assistant config folder
and call the service:

```yaml
action: ewz_energy.import_csv
data:
  path: /config/ewz_usage.csv
```

Future placeholder rows (status `F`) are skipped, the clock-change day is handled, and importing the
same file twice changes nothing. To re-fetch recent days from the portal and rebuild the statistics:

```yaml
action: ewz_energy.backfill
data:
  days: 60
```

## Privacy

Your e-mail and password are stored in the config entry only. The meter number is never used for
entity ids or logs. Nothing leaves your Home Assistant except requests to `energie.ewz.ch` and
`login.stadt-zuerich.ch`.

## How it works (for contributors)

* `api.py`: login (OIDC form post with CSRF token), session reuse, automatic re-login, JSON endpoint.
* `series.py`: the stored 15 minute series, CSV parser, hourly cumulative statistics. A timestamp is the
  **end** of its interval (same as in the portal CSV).
* `coordinator.py`: refresh, local storage, statistics import.
* Tests run against a small fake portal with made-up data: `pip install -r requirements_test.txt && pytest`.

This is an unofficial project, not affiliated with ewz or the City of Zürich. It can break when the portal changes.
