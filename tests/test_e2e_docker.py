"""End-to-end tests against the Dockerised hs-py server.

Requires a running ``make docker-server`` stack (Redis + FastAPI server)
at ``http://localhost:8080``.  Exercises the HTTP :class:`~hs_py.client.Client`
and :class:`~hs_py.ws_client.WebSocketClient` against the real container.

Seed data: Alpha (2032 entities) and Bravo (1077 entities) from
project-haystack.org examples.

Run via::

    make docker-test-e2e
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from hs_py.client import Client
from hs_py.errors import AuthError, CallError, NetworkError
from hs_py.grid import Grid
from hs_py.kinds import Number, Ref
from hs_py.ws_client import WebSocketClient

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_BASE = os.environ.get("HS_PY_SERVER_URL", "http://localhost:8080/api")
_WS_URL = _BASE.replace("http://", "ws://") + "/ws"
_USER = os.environ.get("HS_PY_SERVER_USER", "admin")
_PASS = os.environ.get("HS_PY_SERVER_PASS", "secret")

# Known entity IDs from Alpha/Bravo seed data
_ALPHA_SITE = Ref("a-0000")
_BRAVO_SITE = Ref("b-0000")
_ALPHA_EQUIP = Ref("a-0001")  # Alpha Airside AHU-2


async def _server_reachable() -> bool:
    """Return True if the Docker server is reachable."""
    try:
        async with Client(_BASE, username=_USER, password=_PASS) as c:
            await c.about()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("HS_PY_DOCKER_E2E") != "1",
        reason="Set HS_PY_DOCKER_E2E=1 to run Docker e2e tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def reachable() -> None:
    """Skip the entire module if the server is not up."""
    if not await _server_reachable():
        pytest.skip(f"Server not reachable at {_BASE}")


@pytest.fixture
async def client(reachable: None) -> AsyncGenerator[Client]:
    async with Client(_BASE, username=_USER, password=_PASS) as c:
        yield c


@pytest.fixture
async def raw_client(reachable: None) -> AsyncGenerator[Client]:
    """Client that returns raw Grid objects."""
    async with Client(_BASE, username=_USER, password=_PASS, pythonic=False) as c:
        yield c


@pytest.fixture
async def ws(reachable: None) -> AsyncGenerator[WebSocketClient]:
    """WebSocket client authenticated via SCRAM token."""
    async with Client(_BASE, username=_USER, password=_PASS) as c:
        await c.about()
        token = c._auth_token

    async with WebSocketClient(_WS_URL, auth_token=token or "") as c:
        yield c


@pytest.fixture
async def ws_scram(reachable: None) -> AsyncGenerator[WebSocketClient]:
    """WebSocket client authenticated via HTTP SCRAM token.

    The FastAPI WS endpoint requires a bearer token obtained via HTTP SCRAM.
    Direct WS SCRAM is supported by the standalone WebSocketServer but not
    the FastAPI endpoint used in Docker.
    """
    async with Client(_BASE, username=_USER, password=_PASS) as c:
        await c.about()
        token = c._auth_token

    async with WebSocketClient(_WS_URL, auth_token=token or "") as c:
        yield c


# ---------------------------------------------------------------------------
# HTTP Client — GET ops
# ---------------------------------------------------------------------------


class TestAbout:
    async def test_about_pythonic(self, client: Client) -> None:
        rows = await client.about()
        assert isinstance(rows, list)
        assert len(rows) >= 1
        row = rows[0]
        assert row["serverName"] == "hs-py Redis Server"
        assert row["productName"] == "hs-py"
        assert row["haystackVersion"] == "4.0"

    async def test_about_raw(self, raw_client: Client) -> None:
        grid = await raw_client.about()
        assert isinstance(grid, Grid)
        assert grid[0]["serverName"] == "hs-py Redis Server"


class TestOps:
    async def test_ops_lists_operations(self, client: Client) -> None:
        rows = await client.ops()
        names = {row["name"] for row in rows}
        assert "about" in names
        assert "ops" in names
        assert "formats" in names

    async def test_ops_includes_standard_ops(self, client: Client) -> None:
        rows = await client.read("site", limit=1)
        assert isinstance(rows, list)


class TestFormats:
    async def test_formats(self, client: Client) -> None:
        rows = await client.formats()
        assert isinstance(rows, list)
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    async def test_scram_full_handshake(self, client: Client) -> None:
        """Normal client constructor performs SCRAM automatically."""
        rows = await client.about()
        assert len(rows) >= 1

    async def test_bearer_token_reuse(self, client: Client) -> None:
        """Multiple requests reuse the same bearer token."""
        await client.about()
        token1 = client._auth_token
        await client.about()
        token2 = client._auth_token
        assert token1 == token2
        assert token1 is not None

    async def test_reject_bad_password(self, reachable: None) -> None:
        with pytest.raises(AuthError):
            async with Client(_BASE, username=_USER, password="wrong") as c:
                await c.about()

    async def test_reject_unknown_user(self, reachable: None) -> None:
        with pytest.raises(AuthError):
            async with Client(_BASE, username="nobody", password="x") as c:
                await c.about()

    async def test_unauthenticated_rejected(self, reachable: None) -> None:
        """Requests without auth credentials get 401."""
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{_BASE}/about") as resp,
        ):
            assert resp.status == 401


class TestWebSocketAuth:
    async def test_ws_token_about(self, ws_scram: WebSocketClient) -> None:
        """WS token auth + immediate op."""
        rows = await ws_scram.about()
        assert len(rows) >= 1
        assert rows[0]["haystackVersion"] == "4.0"

    async def test_ws_token_read(self, ws_scram: WebSocketClient) -> None:
        """Multiple ops after WS token auth."""
        rows = await ws_scram.read("site")
        assert len(rows) >= 2  # Alpha + Bravo

    async def test_ws_bad_token_rejected(self, reachable: None) -> None:
        """WS connection with invalid token is closed."""
        from websockets.exceptions import ConnectionClosedOK

        with pytest.raises((AuthError, ConnectionClosedOK, NetworkError)):
            async with WebSocketClient(_WS_URL, auth_token="bad-token") as c:
                await c.about()

    async def test_ws_no_auth_rejected(self, reachable: None) -> None:
        """WS connection with no auth is closed."""
        from websockets.exceptions import ConnectionClosedOK

        with pytest.raises((AuthError, ConnectionClosedOK, NetworkError)):
            async with WebSocketClient(_WS_URL) as c:
                await c.about()


# ---------------------------------------------------------------------------
# HTTP Client — Read with filters
# ---------------------------------------------------------------------------


class TestRead:
    async def test_read_sites(self, client: Client) -> None:
        """Alpha + Bravo = 2 sites."""
        rows = await client.read("site")
        assert len(rows) == 2
        for row in rows:
            assert row.get("site") is True

    async def test_read_with_limit(self, client: Client) -> None:
        rows = await client.read("point", limit=3)
        assert len(rows) <= 3

    async def test_read_equip(self, client: Client) -> None:
        rows = await client.read("equip")
        assert len(rows) >= 1
        for row in rows:
            assert row.get("equip") is True

    async def test_read_all_points(self, client: Client) -> None:
        """Read all points — Alpha has 1846, Bravo has 918 → 2764 total."""
        rows = await client.read("point")
        assert len(rows) >= 2700

    async def test_read_and_filter(self, client: Client) -> None:
        """AND: point and sensor."""
        rows = await client.read("point and sensor")
        assert len(rows) >= 1
        for row in rows:
            assert row.get("point") is True
            assert row.get("sensor") is True

    async def test_read_or_filter(self, client: Client) -> None:
        """OR: site or equip."""
        rows = await client.read("site or equip")
        sites = sum(1 for r in rows if r.get("site") is True)
        equips = sum(1 for r in rows if r.get("equip") is True)
        assert sites == 2  # Alpha + Bravo
        assert equips >= 100

    async def test_read_not_filter(self, client: Client) -> None:
        """NOT: equip and not ahu (equips that are not AHUs)."""
        all_equips = await client.read("equip")
        non_ahu = await client.read("equip and not ahu")
        assert len(non_ahu) < len(all_equips)
        for row in non_ahu:
            assert row.get("equip") is True
            assert row.get("ahu") is not True

    async def test_read_comparison_filter(self, client: Client) -> None:
        """Comparison: kind == "Number"."""
        rows = await client.read('kind == "Number"')
        assert len(rows) >= 100
        for row in rows:
            assert row.get("kind") == "Number"

    async def test_read_ref_path_filter(self, client: Client) -> None:
        """Ref path: point and siteRef == @a-0000 (Alpha points)."""
        rows = await client.read(f"point and siteRef == @{_ALPHA_SITE.val}")
        assert len(rows) >= 100
        for row in rows:
            assert row.get("point") is True

    async def test_read_nested_filter(self, client: Client) -> None:
        """Nested: (point and sensor) or (point and cmd)."""
        rows = await client.read("(point and sensor) or (point and cmd)")
        for row in rows:
            assert row.get("point") is True
            assert row.get("sensor") is True or row.get("cmd") is True

    async def test_read_empty_result(self, client: Client) -> None:
        """Filter that matches nothing."""
        rows = await client.read("site and point")
        assert len(rows) == 0

    async def test_read_by_ids(self, client: Client) -> None:
        rows = await client.read_by_ids([_ALPHA_SITE, _BRAVO_SITE])
        assert len(rows) == 2
        ids = {row["id"] for row in rows}
        assert _ALPHA_SITE in ids
        assert _BRAVO_SITE in ids

    async def test_read_by_ids_mix_existing_missing(self, client: Client) -> None:
        """Mix of existing and non-existing IDs."""
        rows = await client.read_by_ids([_ALPHA_SITE, Ref("does-not-exist")])
        assert len(rows) >= 1
        ids = {row["id"] for row in rows}
        assert _ALPHA_SITE in ids

    async def test_read_missing_id(self, raw_client: Client) -> None:
        grid = await raw_client.read_by_ids([Ref("does-not-exist-at-all")])
        assert isinstance(grid, Grid)
        assert len(grid) == 0


# ---------------------------------------------------------------------------
# HTTP Client — Nav (site → equip → point hierarchy)
# ---------------------------------------------------------------------------


class TestNav:
    async def test_nav_root(self, client: Client) -> None:
        """Root nav returns Alpha + Bravo sites."""
        rows = await client.nav()
        assert len(rows) == 2
        for row in rows:
            assert row.get("site") is True

    async def test_nav_alpha_equips(self, client: Client) -> None:
        """Navigate into Alpha site — returns 184 equips."""
        children = await client.nav(_ALPHA_SITE.val)
        assert len(children) >= 100
        for child in children:
            assert child.get("equip") is True

    async def test_nav_equip_points(self, client: Client) -> None:
        """Navigate into Alpha AHU-2 — returns its direct children."""
        points = await client.nav(_ALPHA_EQUIP.val)
        assert len(points) >= 10
        for pt in points:
            assert pt.get("point") is True

    async def test_nav_leaf_empty(self, client: Client) -> None:
        """Navigate into a point (leaf) — returns empty."""
        points = await client.nav(_ALPHA_EQUIP.val)
        assert len(points) >= 1
        point_id = points[0]["id"]
        leaf = await client.nav(point_id.val if isinstance(point_id, Ref) else str(point_id))
        assert len(leaf) == 0

    async def test_nav_full_alpha_hierarchy(self, client: Client) -> None:
        """Walk the entire Alpha hierarchy: site → equip → point."""
        equips = await client.nav(_ALPHA_SITE.val)
        total_points = 0
        # Check first 3 equips to avoid long test
        for equip in equips[:3]:
            eid = equip["id"]
            pts = await client.nav(eid.val if isinstance(eid, Ref) else str(eid))
            total_points += len(pts)
            for pt in pts:
                assert pt.get("point") is True
        assert total_points >= 10

    async def test_nav_bravo_site(self, client: Client) -> None:
        """Navigate into Bravo site."""
        children = await client.nav(_BRAVO_SITE.val)
        assert len(children) >= 50
        for child in children:
            assert child.get("equip") is True


# ---------------------------------------------------------------------------
# HTTP Client — History
# ---------------------------------------------------------------------------


class TestHistory:
    async def _get_alpha_point(self, client: Client) -> Ref:
        """Return a point Ref from Alpha."""
        rows = await client.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=1)
        assert len(rows) >= 1
        ref = rows[0]["id"]
        assert isinstance(ref, Ref)
        return ref

    async def test_his_write_and_read(self, client: Client) -> None:
        ref = await self._get_alpha_point(client)
        await client.his_write(
            ref,
            [
                {"ts": "2024-06-01T12:00:00Z", "val": Number(72.0)},
                {"ts": "2024-06-01T13:00:00Z", "val": Number(73.5)},
            ],
        )
        rows = await client.his_read(ref, "2024-06-01", raw=False)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    async def test_his_write_multiple_then_read(self, client: Client) -> None:
        """Multiple writes accumulate."""
        ref = await self._get_alpha_point(client)
        await client.his_write(
            ref,
            [{"ts": "2024-07-01T10:00:00Z", "val": Number(70.0)}],
        )
        await client.his_write(
            ref,
            [{"ts": "2024-07-01T11:00:00Z", "val": Number(71.0)}],
        )
        rows = await client.his_read(ref, "2024-07-01", raw=False)
        assert len(rows) >= 2

    async def test_his_read_date_range(self, client: Client) -> None:
        """Read with date,date range."""
        ref = await self._get_alpha_point(client)
        await client.his_write(
            ref,
            [
                {"ts": "2024-08-01T00:00:00Z", "val": Number(60.0)},
                {"ts": "2024-08-02T00:00:00Z", "val": Number(61.0)},
                {"ts": "2024-08-03T00:00:00Z", "val": Number(62.0)},
            ],
        )
        rows = await client.his_read(ref, "2024-08-01,2024-08-02")
        assert len(rows) >= 1

    async def test_his_read_raw(self, raw_client: Client) -> None:
        """Raw mode returns Grid with metadata."""
        pts = await raw_client.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=1)
        assert len(pts) >= 1
        ref = pts[0]["id"]
        await raw_client.his_write(
            ref,
            [{"ts": "2024-09-01T00:00:00Z", "val": Number(50.0)}],
        )
        grid = await raw_client.his_read(ref, "2024-09-01")
        assert isinstance(grid, Grid)
        assert grid.meta["id"] == ref


# ---------------------------------------------------------------------------
# HTTP Client — Point write (priority array)
# ---------------------------------------------------------------------------


class TestPointWrite:
    async def _get_point(self, client: Client) -> Ref:
        rows = await client.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=1)
        assert len(rows) >= 1
        ref = rows[0]["id"]
        assert isinstance(ref, Ref)
        return ref

    async def test_point_write_and_read_array(self, client: Client) -> None:
        ref = await self._get_point(client)
        await client.point_write(ref, 16, Number(65.0), who="e2e-test")
        rows = await client.point_write_array(ref)
        assert len(rows) == 17
        level_16 = rows[15]
        assert "val" in level_16

    async def test_point_write_then_auto(self, client: Client) -> None:
        """Write a level then release (auto) it."""
        ref = await self._get_point(client)
        await client.point_write(ref, 10, Number(70.0), who="e2e")
        rows = await client.point_write_array(ref)
        assert rows[9].get("val") is not None

        # Release the level (write None)
        await client.point_write(ref, 10, None, who="e2e")
        rows = await client.point_write_array(ref)
        assert rows[9].get("val") is None

    async def test_point_write_multiple_levels(self, client: Client) -> None:
        """Write to multiple priority levels."""
        ref = await self._get_point(client)
        await client.point_write(ref, 8, Number(68.0), who="e2e-8")
        await client.point_write(ref, 14, Number(72.0), who="e2e-14")
        rows = await client.point_write_array(ref)
        assert rows[7].get("val") is not None  # level 8
        assert rows[13].get("val") is not None  # level 14


# ---------------------------------------------------------------------------
# HTTP Client — Watch lifecycle
# ---------------------------------------------------------------------------


class TestWatch:
    async def test_watch_sub_poll_unsub(self, raw_client: Client) -> None:
        ids = [_ALPHA_SITE, _BRAVO_SITE]
        result = await raw_client.watch_sub(ids, "e2e-watch")
        assert isinstance(result, Grid)
        watch_id = result.meta.get("watchId")
        assert isinstance(watch_id, str)
        assert len(result) == 2

        poll_result = await raw_client.watch_poll(watch_id)
        assert isinstance(poll_result, Grid)

        refresh_result = await raw_client.watch_poll(watch_id, refresh=True)
        assert isinstance(refresh_result, Grid)
        assert len(refresh_result) == 2

        await raw_client.watch_close(watch_id)

    async def test_watch_multiple_entities(self, raw_client: Client) -> None:
        """Subscribe to multiple entities."""
        pts = await raw_client.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=5)
        ids = [row["id"] for row in pts]
        assert len(ids) >= 3

        result = await raw_client.watch_sub(ids, "e2e-multi")
        watch_id = result.meta["watchId"]
        assert len(result) == len(ids)

        refresh = await raw_client.watch_poll(watch_id, refresh=True)
        assert len(refresh) == len(ids)

        await raw_client.watch_close(watch_id)

    async def test_watch_unsub_specific_entities(self, raw_client: Client) -> None:
        """Unsub specific entities without closing the watch."""
        ids = [_ALPHA_SITE, _BRAVO_SITE]
        result = await raw_client.watch_sub(ids, "e2e-partial-unsub")
        watch_id = result.meta["watchId"]
        assert len(result) == 2

        # Unsub just Bravo
        await raw_client.watch_unsub(watch_id, [_BRAVO_SITE])

        # Refresh should return only Alpha
        refresh = await raw_client.watch_poll(watch_id, refresh=True)
        assert len(refresh) == 1
        assert refresh[0]["id"] == _ALPHA_SITE

        await raw_client.watch_close(watch_id)


# ---------------------------------------------------------------------------
# HTTP Client — Content negotiation
# ---------------------------------------------------------------------------


class TestContentNegotiation:
    async def _get_token(self, client: Client) -> str:
        await client.about()
        return client._auth_token or ""

    async def test_json_response(self, client: Client) -> None:
        token = await self._get_token(client)
        headers = {"Accept": "application/json", "Authorization": f"BEARER authToken={token}"}
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{_BASE}/about", headers=headers) as resp,
        ):
            assert resp.status == 200
            assert "application/json" in resp.content_type

    async def test_zinc_response(self, client: Client) -> None:
        token = await self._get_token(client)
        headers = {"Accept": "text/zinc", "Authorization": f"BEARER authToken={token}"}
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{_BASE}/about", headers=headers) as resp,
        ):
            assert resp.status == 200
            assert "text/zinc" in resp.content_type
            text = await resp.text()
            assert text.startswith("ver:")

    async def test_csv_response(self, client: Client) -> None:
        token = await self._get_token(client)
        headers = {"Accept": "text/csv", "Authorization": f"BEARER authToken={token}"}
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{_BASE}/about", headers=headers) as resp,
        ):
            assert resp.status == 200
            assert "text/csv" in resp.content_type

    async def test_wildcard_defaults_to_json(self, client: Client) -> None:
        token = await self._get_token(client)
        headers = {"Accept": "*/*", "Authorization": f"BEARER authToken={token}"}
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{_BASE}/about", headers=headers) as resp,
        ):
            assert resp.status == 200
            assert "application/json" in resp.content_type


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:
    async def test_bad_filter(self, client: Client) -> None:
        """Malformed filter returns error."""
        with pytest.raises(CallError):
            await client.read("((( bad filter !!!")

    async def test_read_missing_ids_empty(self, raw_client: Client) -> None:
        """Reading a non-existent ID returns empty grid, not error."""
        grid = await raw_client.read_by_ids([Ref("zzz-nonexistent")])
        assert isinstance(grid, Grid)
        assert len(grid) == 0


# ---------------------------------------------------------------------------
# WebSocket Client — Token auth
# ---------------------------------------------------------------------------


class TestWebSocket:
    async def test_ws_about(self, ws: WebSocketClient) -> None:
        rows = await ws.about()
        assert isinstance(rows, list)
        assert len(rows) >= 1
        assert rows[0]["serverName"] == "hs-py Redis Server"

    async def test_ws_read_sites(self, ws: WebSocketClient) -> None:
        rows = await ws.read("site")
        assert len(rows) == 2
        for row in rows:
            assert row.get("site") is True

    async def test_ws_read_with_limit(self, ws: WebSocketClient) -> None:
        rows = await ws.read("point", limit=5)
        assert len(rows) <= 5

    async def test_ws_nav_root(self, ws: WebSocketClient) -> None:
        rows = await ws.nav()
        assert len(rows) == 2

    async def test_ws_ops(self, ws: WebSocketClient) -> None:
        rows = await ws.ops()
        names = {row["name"] for row in rows}
        assert "about" in names
        assert "ops" in names
        assert "formats" in names

    async def test_ws_formats(self, ws: WebSocketClient) -> None:
        rows = await ws.formats()
        assert len(rows) >= 1

    async def test_ws_read_filter(self, ws: WebSocketClient) -> None:
        rows = await ws.read("point and sensor and temp")
        assert len(rows) >= 100
        for row in rows:
            assert row.get("sensor") is True
            assert row.get("temp") is True

    async def test_ws_read_by_ids(self, ws: WebSocketClient) -> None:
        rows = await ws.read_by_ids([_ALPHA_SITE, _BRAVO_SITE])
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# WebSocket Client — Full ops via SCRAM
# ---------------------------------------------------------------------------


class TestWebSocketOps:
    async def test_ws_his_write_and_read(self, ws_scram: WebSocketClient) -> None:
        pts = await ws_scram.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=1)
        ref = pts[0]["id"]
        await ws_scram.his_write(
            ref,
            [
                {"ts": "2024-10-01T12:00:00Z", "val": Number(55.0)},
                {"ts": "2024-10-01T13:00:00Z", "val": Number(56.5)},
            ],
        )
        rows = await ws_scram.his_read(ref, "2024-10-01")
        assert len(rows) >= 1

    async def test_ws_point_write(self, ws_scram: WebSocketClient) -> None:
        pts = await ws_scram.read(f"point and siteRef == @{_ALPHA_SITE.val}", limit=1)
        ref = pts[0]["id"]
        await ws_scram.point_write(ref, 15, Number(60.0), who="ws-e2e")
        rows = await ws_scram.point_write_array(ref)
        assert len(rows) == 17
        assert rows[14].get("val") is not None

    async def test_ws_watch_lifecycle(self, ws_scram: WebSocketClient) -> None:
        """Watch sub → poll → refresh → close over WS."""
        result = await ws_scram.watch_sub([_ALPHA_SITE, _BRAVO_SITE], "ws-watch", raw=True)
        assert isinstance(result, Grid)
        watch_id = result.meta["watchId"]
        assert len(result) == 2

        poll = await ws_scram.watch_poll(watch_id, raw=True)
        assert isinstance(poll, Grid)

        refresh = await ws_scram.watch_poll(watch_id, refresh=True, raw=True)
        assert len(refresh) == 2

        await ws_scram.watch_close(watch_id)

    async def test_ws_nav_hierarchy(self, ws_scram: WebSocketClient) -> None:
        """Full nav hierarchy over WS."""
        sites = await ws_scram.nav()
        assert len(sites) == 2

        equips = await ws_scram.nav(_ALPHA_SITE.val)
        assert len(equips) >= 100

        points = await ws_scram.nav(_ALPHA_EQUIP.val)
        assert len(points) >= 10

    async def test_ws_concurrent_reads(self, ws_scram: WebSocketClient) -> None:
        """Concurrent reads over a single WS connection."""
        tasks = [
            ws_scram.read("site"),
            ws_scram.read("equip", limit=10),
            ws_scram.read("point and sensor", limit=5),
            ws_scram.about(),
            ws_scram.ops(),
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        # Sites
        assert len(results[0]) == 2
        # Equips
        assert len(results[1]) <= 10
        # Points
        assert len(results[2]) <= 5
        # About
        assert results[3][0]["haystackVersion"] == "4.0"

    async def test_ws_raw_mode(self, reachable: None) -> None:
        """WS raw=True returns Grid objects."""
        async with Client(_BASE, username=_USER, password=_PASS) as c:
            await c.about()
            token = c._auth_token

        async with WebSocketClient(_WS_URL, auth_token=token or "", pythonic=False) as ws:
            grid = await ws.about()
            assert isinstance(grid, Grid)
            assert grid[0]["serverName"] == "hs-py Redis Server"


# ---------------------------------------------------------------------------
# Scale / large result sets
# ---------------------------------------------------------------------------


class TestScale:
    async def test_read_all_alpha_points(self, client: Client) -> None:
        """Read all 1846 Alpha points."""
        rows = await client.read(f"point and siteRef == @{_ALPHA_SITE.val}")
        assert len(rows) >= 1800

    async def test_read_all_bravo_points(self, client: Client) -> None:
        """Read all 918 Bravo points."""
        rows = await client.read(f"point and siteRef == @{_BRAVO_SITE.val}")
        assert len(rows) >= 900

    async def test_read_all_equips(self, client: Client) -> None:
        """Alpha (184) + Bravo (149) = 333+ equips."""
        rows = await client.read("equip")
        assert len(rows) >= 300

    async def test_concurrent_http_clients(self, reachable: None) -> None:
        """Multiple HTTP clients reading concurrently."""

        async def _read_sites() -> list[dict[str, Any]]:
            async with Client(_BASE, username=_USER, password=_PASS) as c:
                return await c.read("site")

        results = await asyncio.gather(*[_read_sites() for _ in range(5)])
        for r in results:
            assert len(r) == 2

    async def test_ws_large_read(self, ws_scram: WebSocketClient) -> None:
        """Large read over WebSocket."""
        rows = await ws_scram.read(f"point and siteRef == @{_ALPHA_SITE.val}")
        assert len(rows) >= 1800
