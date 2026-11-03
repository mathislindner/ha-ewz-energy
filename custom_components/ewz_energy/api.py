"""Client for the ewz "Netz-Privat" web portal (Stadt Zürich single sign-on).

The portal has no public API. Its analytics page loads load profiles from a JSON
endpoint that only answers to a logged-in browser session, so this client logs in
the way a browser does (OIDC redirect to login.stadt-zuerich.ch, e-mail form) and
then calls that endpoint. Nothing here needs a headless browser.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from yarl import URL

from .const import BASE_URL, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

PORTAL_TZ = ZoneInfo("Europe/Zurich")
_XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


class EwzError(Exception):
    """Base error."""


class EwzAuthError(EwzError):
    """The portal rejected the credentials (or wants AGOV)."""


class EwzConnectionError(EwzError):
    """The portal could not be reached or timed out."""


class EwzApiError(EwzError):
    """The portal answered with something unexpected."""


@dataclass(frozen=True, slots=True)
class MeasuringLine:
    """One load-profile line of a measuring point."""

    mp_id: str
    line_id: str
    name: str
    obis: str | None

    @property
    def kind(self) -> str:
        """Return consumption / return / unknown based on the OBIS code."""
        if self.obis:
            if "1.29.0" in self.obis or "1.8.0" in self.obis:
                return "consumption"
            if "2.29.0" in self.obis or "2.8.0" in self.obis:
                return "return"
        # No OBIS code: use the German line name, but only for unambiguous words.
        # (ewz calls the line "Wirk Abgabe" although its OBIS 1.29.0 is energy delivered TO the
        # customer = consumption, so "abgabe" alone must not be read as feed-in.)
        name = self.name.lower()
        if "einspeis" in name:
            return "return"
        if "bezug" in name or "verbrauch" in name:
            return "consumption"
        return "unknown"


@dataclass(frozen=True, slots=True)
class Interval:
    """One 15 minute value. ``end`` is the interval END as a UTC epoch."""

    end: int
    value: float
    state: str


class _FormParser(HTMLParser):
    """Collect forms and their inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            self._cur = {"name": a.get("name"), "action": a.get("action"), "inputs": {}}
            self.forms.append(self._cur)
        elif tag == "input" and self._cur is not None and a.get("name"):
            self._cur["inputs"][a["name"]] = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._cur = None


def format_portal_time(dt: datetime) -> str:
    """Format like the portal does: ``2026-11-2T00:00:00+01:00`` (local Zurich offset)."""
    local = dt.astimezone(PORTAL_TZ)
    offset = local.strftime("%z")
    return f"{local:%Y-%m-%dT%H:%M:%S}{offset[:3]}:{offset[3:]}"


def parse_mp_tree(tree: Any) -> list[MeasuringLine]:
    """Flatten the sidebar JSON into measuring lines."""
    lines: list[MeasuringLine] = []
    if not isinstance(tree, list):
        return lines
    for root in tree:
        for mp in root.get("childs", []) if isinstance(root, dict) else []:
            if mp.get("type") != "mp":
                continue
            for ln in mp.get("childs", []):
                if ln.get("type") != "line":
                    continue
                details = ln.get("details") or {}
                mq = details.get("measured quantity") or details.get("mq")
                lines.append(
                    MeasuringLine(
                        mp_id=str(mp["id"]),
                        line_id=str(ln["id"]),
                        name=str(ln.get("name") or "line"),
                        obis=str(mq) if mq else None,
                    )
                )
    return lines


class EwzClient:
    """Logged-in session against energie.ewz.ch."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str, base_url: str = BASE_URL
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._analytics = f"{self._base}/privat/analytics"
        self._email = email
        self._password = password
        self._logged_in = False
        self._lock = asyncio.Lock()

    async def _request(self, method: str, url: str | URL, **kwargs: Any) -> aiohttp.ClientResponse:
        kwargs.setdefault("timeout", aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
        headers = {"User-Agent": _BROWSER_UA, "Accept-Language": "de-CH,de;q=0.9"}
        headers.update(kwargs.pop("headers", {}))
        try:
            return await self._session.request(method, url, headers=headers, **kwargs)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EwzConnectionError(f"{type(err).__name__}: {err}") from err

    async def login(self) -> None:
        """Log in through the Stadt Zürich e-mail form."""
        self._logged_in = False
        async with await self._request("GET", self._analytics) as resp:
            page = await resp.text()
            final = resp.url
        parser = _FormParser()
        parser.feed(page)
        form = next((f for f in parser.forms if f["name"] == "Email"), None)
        if form is None:
            if final.host == URL(self._base).host:
                self._logged_in = True  # a surviving cookie is still valid
                return
            raise EwzApiError("login form not found (portal changed?)")
        if "csrftoken" not in form["inputs"]:
            raise EwzApiError("login form has no csrf token (portal changed?)")
        data = dict(form["inputs"])
        data["userid"] = self._email
        data["password"] = self._password
        post_url = final.join(URL(form["action"] or ""))
        async with await self._request(
            "POST",
            post_url,
            data=data,
            headers={"Referer": str(final), "Origin": f"{final.scheme}://{final.host}"},
        ) as resp:
            body = await resp.text()
            still_login = _FormParser()
            still_login.feed(body)
            if resp.url.host != URL(self._base).host or any(f["name"] == "Email" for f in still_login.forms):
                if "fehlgeschlagen" in body or "failed" in body.lower():
                    raise EwzAuthError("login rejected")
                raise EwzAuthError("login did not complete (AGOV-only account?)")
        self._logged_in = True

    async def _get_json(self, url: str) -> Any:
        """GET JSON, logging in first or again when the session is missing/expired."""
        async with self._lock:
            for attempt in (1, 2):
                if not self._logged_in:
                    await self.login()
                async with await self._request(
                    "GET", url, headers={**_XHR_HEADERS, "Referer": self._analytics}
                ) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    text = await resp.text()
                    if resp.status == 200 and ("json" in ctype or text.lstrip()[:1] in "[{"):
                        try:
                            return await resp.json(content_type=None)
                        except ValueError as err:
                            raise EwzApiError("invalid JSON from portal") from err
                    if attempt == 1:
                        _LOGGER.debug("Session looks expired (HTTP %s), logging in again", resp.status)
                        self._logged_in = False
                        continue
                    raise EwzApiError(f"unexpected answer from portal: HTTP {resp.status}")
        raise EwzApiError("unreachable")  # pragma: no cover

    async def get_lines(self) -> list[MeasuringLine]:
        """Return all measuring lines of the account.

        An empty tree is retried once with a fresh login: the portal sometimes answers an
        otherwise valid session with an empty list.
        """
        url = f"{self._base}/privat/sidebarMultiMp/rlm"
        lines = parse_mp_tree(await self._get_json(url))
        if not lines:
            _LOGGER.debug("Sidebar was empty, logging in again and retrying once")
            self._logged_in = False
            await asyncio.sleep(2)
            lines = parse_mp_tree(await self._get_json(url))
        return lines

    async def get_intervals(self, line: MeasuringLine, start: datetime, end: datetime) -> list[Interval]:
        """Return the 15 minute values of ``line`` between two datetimes."""
        url = (
            f"{self._base}/privat/data/mpline/genericto/{line.mp_id}/{line.line_id}/"
            f"{format_portal_time(start)}/{format_portal_time(end)}/base"
        )
        payload = await self._get_json(url)
        if not isinstance(payload, dict) or "timestamps" not in payload:
            raise EwzApiError("data answer has no timestamps")
        unit = payload.get("unit")
        if unit not in (None, "kWh"):
            raise EwzApiError(f"unsupported unit {unit!r}")
        out: list[Interval] = []
        for ts, val, state in zip(
            payload["timestamps"], payload["values"], payload.get("states") or [], strict=False
        ):
            if val is None:
                continue
            out.append(Interval(int(datetime.fromisoformat(ts).timestamp()), float(val), str(state)))
        return out


_OBIS_RE = re.compile(r"\d-\d+:\d+\.\d+\.\d+")


def obis_of(line: MeasuringLine) -> str | None:
    """Return the bare OBIS code of a line, if any."""
    if not line.obis:
        return None
    match = _OBIS_RE.search(line.obis)
    return match.group(0) if match else None
