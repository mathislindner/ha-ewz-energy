# ewz Energy for Home Assistant

Smart-meter data of **ewz (Stadt Zürich)** in Home Assistant. ewz has no public API, so this
integration logs in to the customer portal (`energie.ewz.ch`, Stadt Zürich single sign-on) the way a
browser does and reads the same JSON the portal's table view uses. No headless browser needed.

## What you get

For every load-profile line of your account (for example *Wirk Abgabe* = energy fed into the grid,
or *Wirk Bezug* = energy drawn from the grid):

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

The portal publishes a day's values after the day is over. Polling every 15 minutes or every hour
brings nothing new, so the default refresh is **hourly** and every refresh re-reads the last 3 days
and overwrites them (late or corrected values replace old ones). You can change the interval in the
integration options (15 min to 24 h). The last four quarters sensor therefore shows the last
published quarters, not "right now".

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
