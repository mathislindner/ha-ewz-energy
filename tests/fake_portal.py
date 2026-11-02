"""A tiny fake of the ewz portal (login redirect + JSON endpoints) for tests. Uses made-up data only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiohttp import web

LOGIN_HTML = """<html><body><form name="Email" method="post" action="/login/op/auth">
<input type="hidden" name="csrftoken" value="tok123"><input type="hidden" name="currentRequestedPage" value="x%2By">
<input name="userid"><input type="password" name="password"></form>
<form name="AGOV" method="post" action="/login/op/auth"><input type="hidden" name="csrftoken" value="tok123"></form></body></html>"""
FAIL_HTML = "<html><body><div>Anmeldung fehlgeschlagen.</div>" + LOGIN_HTML + "</body></html>"
SIDEBAR = [
    {
        "name": "my metering points",
        "type": "root",
        "childs": [
            {
                "type": "mp",
                "id": "ISU_1",
                "name": "TEST_METER",
                "childs": [
                    {"type": "line", "id": "42", "name": "Wirk Abgabe", "details": {"mq": "OBIS 1-1:2.29.0*255 (kWh)"}}
                ],
            }
        ],
    }
]


class FakePortal:
    def __init__(self, email="a@b.ch", password="pw") -> None:
        self.email, self.password = email, password
        self.logins = 0
        self.data_calls: list[tuple[str, str]] = []
        self.sessions: set[str] = set()
        # interval END (UTC) -> (kWh, state)
        self.values: dict[datetime, tuple[float, str]] = {}
        self.expire_next = False

    def fill(self, first_end: datetime, count: int, value=0.1, state="W") -> None:
        for i in range(count):
            self.values[first_end + timedelta(minutes=15 * i)] = (value, state)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/privat/analytics", self.analytics)
        app.router.add_get("/login/op/auth", self.login_page)
        app.router.add_post("/login/op/auth", self.login_post)
        app.router.add_get("/privat/sidebarMultiMp/rlm", self.sidebar)
        app.router.add_get(r"/privat/data/mpline/genericto/{mp}/{line}/{a}/{b}/base", self.data)
        return app

    def _authed(self, request) -> bool:
        return request.cookies.get("sid") in self.sessions

    async def analytics(self, request):
        if self._authed(request):
            return web.Response(text="<title>Webportal</title>")
        raise web.HTTPFound("/login/op/auth")

    async def login_page(self, request):
        return web.Response(text=LOGIN_HTML, content_type="text/html")

    async def login_post(self, request):
        form = await request.post()
        if form.get("csrftoken") != "tok123" or form.get("userid") != self.email or form.get("password") != self.password:
            return web.Response(text=FAIL_HTML, content_type="text/html")
        self.logins += 1
        sid = f"s{self.logins}"
        self.sessions.add(sid)
        resp = web.HTTPFound("/privat/analytics")
        resp.set_cookie("sid", sid)
        raise resp

    async def sidebar(self, request):
        if not self._authed(request):
            return web.Response(status=404, text="<html>not found</html>", content_type="text/html")
        return web.json_response(SIDEBAR)

    async def data(self, request):
        if self.expire_next:
            self.expire_next = False
            self.sessions.clear()
        if not self._authed(request):
            return web.Response(status=404, text="<html>not found</html>", content_type="text/html")
        a, b = request.match_info["a"], request.match_info["b"]
        self.data_calls.append((a, b))
        start, end = datetime.fromisoformat(_pad(a)), datetime.fromisoformat(_pad(b))
        ts, vals, st = [], [], []
        for t in sorted(self.values):
            if start < t <= end:
                ts.append(t.astimezone(timezone.utc).isoformat())
                vals.append(self.values[t][0])
                st.append(self.values[t][1])
        return web.json_response({"timestamps": ts, "values": vals, "states": st, "unit": "kWh", "name": "Wirk Abgabe"})


def _pad(s: str) -> str:
    """Portal dates look like 2026-11-2T00:00:00+01:00 (day without zero padding)."""
    d, rest = s.split("T")
    y, m, day = d.split("-")
    return f"{int(y):04d}-{int(m):02d}-{int(day):02d}T{rest}"
