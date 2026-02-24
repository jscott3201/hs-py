"""Tests for RedisOps -- requires a running Redis 8 instance.

Start Redis with:
    docker compose -f docker/docker-compose.yml up -d redis

Tests are skipped if Redis is not available on localhost:6379.
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.fastapi_server import create_fastapi_app
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref, Symbol
from hs_py.ops import HaystackOps

_DATA_DIR = Path(__file__).resolve().parent.parent / "_data"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_redis_available: bool | None = None


def _check_redis() -> bool:
    """Check if Redis is available on localhost:6379."""
    global _redis_available
    if _redis_available is not None:
        return _redis_available

    try:
        from redis.asyncio import Redis

        async def _ping() -> bool:
            r = Redis(host="localhost", port=6379, protocol=3)
            try:
                return bool(await r.ping())
            except Exception:
                return False
            finally:
                await r.aclose()

        _redis_available = (
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_ping())
        )
    except ImportError:
        _redis_available = False

    return _redis_available


requires_redis = pytest.mark.skipif(
    not _check_redis(),
    reason="Redis not available on localhost:6379",
)


class _RedisTestOps(HaystackOps):
    """HaystackOps subclass for Redis testing with about/filetypes/invoke_action."""

    async def about(self) -> Grid:
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "tz": "New_York",
                    "serverName": "hs-py Redis Server",
                    "productName": "hs-py",
                    "productVersion": "0.3.0",
                }
            ]
        )

    async def filetypes(self, grid: Grid) -> Grid:
        return Grid.make_rows(
            [
                {"def": Symbol("filetype:json"), "dis": "JSON", "mime": "application/json"},
                {"def": Symbol("filetype:zinc"), "dis": "Zinc", "mime": "text/zinc"},
                {"def": Symbol("filetype:trio"), "dis": "Trio", "mime": "text/trio"},
                {"def": Symbol("filetype:csv"), "dis": "CSV", "mime": "text/csv"},
            ]
        )

    async def invoke_action(self, grid: Grid) -> Grid:
        action = grid.meta.get("action", "unknown")
        return Grid.make_rows([{"action": str(action), "result": "ok"}])

    async def watch_unsub(self, grid: Grid) -> Grid:
        from hs_py.kinds import Ref

        watch_id = grid.meta.get("watchId")
        if not isinstance(watch_id, str):
            return Grid.make_error("Unknown watch")
        close = "close" in grid.meta
        ids = [row["id"] for row in grid if isinstance(row.get("id"), Ref)]
        try:
            await self._storage.watch_unsub(watch_id, ids, close=close)  # type: ignore[union-attr]
        except ValueError:
            return Grid.make_error("Unknown watch")
        return Grid.make_empty()

    async def watch_poll(self, grid: Grid) -> Grid:
        watch_id = grid.meta.get("watchId")
        if not isinstance(watch_id, str):
            return Grid.make_error("Unknown watch")
        refresh = "refresh" in grid.meta
        try:
            rows = await self._storage.watch_poll(watch_id, refresh=refresh)  # type: ignore[union-attr]
        except ValueError:
            return Grid.make_error("Unknown watch")
        return Grid.make_rows(rows) if rows else Grid.make_empty()


async def _make_redis_ops() -> _RedisTestOps:
    """Create a _RedisTestOps instance with RedisAdapter, loading data only if Redis is empty.

    Uses a lightweight Redis client (no health checks/retry/keepalive) to
    avoid connection pool cleanup issues across event loops.
    """
    from redis.asyncio import Redis

    from hs_py.storage.redis import RedisAdapter

    r: Redis[str] = Redis(protocol=3, decode_responses=True)  # type: ignore[assignment]

    adapter = RedisAdapter(r)
    ops = _RedisTestOps(storage=adapter)
    ops._adapter = adapter  # type: ignore[attr-defined]  # for test access
    await adapter.start()

    # Only flush + reload if no data is loaded yet (persists across classes)
    if not await r.scard("hs:ids") and _DATA_DIR.is_dir():
        for json_file in sorted(_DATA_DIR.rglob("*.json")):
            raw = json_file.read_bytes()
            grid = decode_grid(raw)
            await adapter.load_entities([dict(row) for row in grid])

    return ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JSON_CT = "application/json"


async def _make_client(ops: HaystackOps) -> AsyncClient:
    """Create an httpx AsyncClient backed by the FastAPI app."""
    app = create_fastapi_app(ops=ops)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# GET ops
# ---------------------------------------------------------------------------


@requires_redis
class TestAbout:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_about_returns_expected_fields(self) -> None:
        resp = await self._client.get("/api/about")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        row = grid[0]
        assert row["haystackVersion"] == "4.0"
        assert "tz" in row


@requires_redis
class TestOps:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_ops_lists_all_implemented(self) -> None:
        resp = await self._client.get("/api/ops")
        grid = decode_grid(resp.content)
        names = {row["name"] for row in grid}
        for op in (
            "about",
            "ops",
            "formats",
            "read",
            "nav",
            "hisRead",
            "hisWrite",
            "pointWrite",
            "watchSub",
            "watchUnsub",
            "watchPoll",
        ):
            assert op in names, f"{op} missing from ops"
        # Stub-only ops and namespace-backed ops should NOT appear
        for op in ("defs", "libs"):
            assert op not in names, f"{op} should not appear without a namespace"


@requires_redis
class TestFormats:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_formats_returns_json(self) -> None:
        resp = await self._client.get("/api/formats")
        grid = decode_grid(resp.content)
        assert grid[0]["mime"] == "application/json"


# ---------------------------------------------------------------------------
# Read ops
# ---------------------------------------------------------------------------


@requires_redis
class TestReadFilter:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def _post_read(self, filter_str: str, limit: int | None = None) -> Grid:
        row: dict[str, Any] = {"filter": filter_str}
        if limit is not None:
            row["limit"] = Number(float(limit))
        req = GridBuilder().add_col("filter").add_col("limit").add_row(row).to_grid()
        resp = await self._client.post(
            "/api/read", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        return decode_grid(resp.content)

    async def test_read_site(self) -> None:
        grid = await self._post_read("site")
        assert len(grid) >= 2
        for row in grid:
            assert "site" in row

    async def test_read_point(self) -> None:
        grid = await self._post_read("point")
        assert len(grid) > 10
        for row in grid:
            assert "point" in row

    async def test_read_equip(self) -> None:
        grid = await self._post_read("equip")
        assert len(grid) > 5
        for row in grid:
            assert "equip" in row

    async def test_read_compound_filter(self) -> None:
        grid = await self._post_read("point and temp")
        assert len(grid) >= 1
        for row in grid:
            assert "point" in row
            assert "temp" in row

    async def test_read_limit(self) -> None:
        grid = await self._post_read("point", limit=3)
        assert len(grid) == 3

    async def test_read_cmp_siteref_eq(self) -> None:
        """RediSearch handles siteRef == @ref filters."""
        grid = await self._post_read("siteRef == @a-0000")
        assert len(grid) >= 1
        for row in grid:
            site_ref = row.get("siteRef")
            assert isinstance(site_ref, Ref)
            assert site_ref.val == "a-0000"

    async def test_read_cmp_equipref_eq(self) -> None:
        """RediSearch handles equipRef == @ref filters."""
        grid = await self._post_read("equipRef == @a-0001")
        assert len(grid) >= 1
        for row in grid:
            equip_ref = row.get("equipRef")
            assert isinstance(equip_ref, Ref)
            assert equip_ref.val == "a-0001"

    async def test_read_compound_cmp_filter(self) -> None:
        """Combined tag + ref comparison filter via RediSearch."""
        grid = await self._post_read("point and siteRef == @a-0000")
        assert len(grid) >= 1
        for row in grid:
            assert "point" in row
            site_ref = row.get("siteRef")
            assert isinstance(site_ref, Ref)
            assert site_ref.val == "a-0000"

    async def test_read_or_filter(self) -> None:
        """Or filter via RediSearch."""
        grid = await self._post_read("site or equip")
        assert len(grid) >= 2
        for row in grid:
            assert "site" in row or "equip" in row


@requires_redis
class TestReadByIds:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_read_valid_ids(self) -> None:
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("a-0000")})
        builder.add_row({"id": Ref("a-0001")})
        resp = await self._client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("a-0000")
        assert "site" in grid[0]

    async def test_read_missing_id_returns_empty(self) -> None:
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("nonexistent")})
        resp = await self._client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        # Per Haystack spec, missing entities are omitted from the response.
        assert grid.is_empty


# ---------------------------------------------------------------------------
# Nav ops
# ---------------------------------------------------------------------------


@requires_redis
class TestNav:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def _post_nav(self, nav_id: str | None = None) -> Grid:
        row: dict[str, Any] = {"navId": nav_id}
        req = GridBuilder().add_col("navId").add_row(row).to_grid()
        resp = await self._client.post(
            "/api/nav", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        return decode_grid(resp.content)

    async def test_nav_root_returns_sites(self) -> None:
        grid = await self._post_nav()
        assert len(grid) >= 2
        for row in grid:
            assert "site" in row

    async def test_nav_site_returns_equips(self) -> None:
        grid = await self._post_nav("a-0000")
        assert len(grid) >= 1
        for row in grid:
            assert "equip" in row
            site_ref = row.get("siteRef")
            assert isinstance(site_ref, Ref)
            assert site_ref.val == "a-0000"

    async def test_nav_equip_returns_points(self) -> None:
        grid = await self._post_nav("a-0000")
        equip_id = grid[0]["id"]
        assert isinstance(equip_id, Ref)

        points = await self._post_nav(equip_id.val)
        if len(points) > 0:
            for row in points:
                equip_ref = row.get("equipRef")
                assert isinstance(equip_ref, Ref)
                assert equip_ref.val == equip_id.val

    async def test_nav_nonexistent_returns_empty(self) -> None:
        grid = await self._post_nav("nonexistent")
        assert grid.is_empty


# ---------------------------------------------------------------------------
# History ops
# ---------------------------------------------------------------------------


@requires_redis
class TestHisOps:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_his_read_empty(self) -> None:
        req = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": Ref("c-no-history"), "range": "today"})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisRead", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert grid.meta["id"] == Ref("c-no-history")
        assert len(grid) == 0

    async def test_his_write_then_read(self) -> None:
        ref = Ref("c-0004")
        write_grid = (
            GridBuilder()
            .set_meta({"id": ref})
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": 1704067200000, "val": Number(72.0, "\u00b0F")})
            .add_row({"ts": 1704070800000, "val": Number(73.0, "\u00b0F")})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisWrite",
            content=encode_grid(write_grid),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        read_grid = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": ref, "range": "today"})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisRead",
            content=encode_grid(read_grid),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.meta["id"] == ref
        assert len(grid) == 2

        # Timestamps should be proper datetime objects
        ts0 = grid[0]["ts"]
        assert isinstance(ts0, datetime.datetime), f"Expected datetime, got {type(ts0)}"
        expected_dt = datetime.datetime.fromtimestamp(1704067200000 / 1000, tz=datetime.UTC)
        assert ts0 == expected_dt

    async def test_his_write_with_datetime_timestamps(self) -> None:
        """Verify his_write accepts datetime.datetime timestamps."""
        ref = Ref("c-0005")
        dt1 = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        dt2 = datetime.datetime(2024, 1, 1, 13, 0, 0, tzinfo=datetime.UTC)
        write_grid = (
            GridBuilder()
            .set_meta({"id": ref})
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": dt1, "val": Number(70.0)})
            .add_row({"ts": dt2, "val": Number(71.0)})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisWrite",
            content=encode_grid(write_grid),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        read_grid = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": ref, "range": "today"})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisRead",
            content=encode_grid(read_grid),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert isinstance(grid[0]["ts"], datetime.datetime)

    async def test_his_write_creates_ts_with_labels(self) -> None:
        """Verify TS.CREATE uses labels and DUPLICATE_POLICY."""
        ref = Ref("c-0004")
        write_grid = (
            GridBuilder()
            .set_meta({"id": ref})
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": 1704067200000, "val": Number(72.0)})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/hisWrite",
            content=encode_grid(write_grid),
            headers={"Content-Type": _JSON_CT},
        )
        assert resp.status_code == 200

        # Verify TS key has labels via ts().info() (RESP3 returns dict)
        info = await self._ops._adapter._r.ts().info("hs:ts:c-0004")  # type: ignore[attr-defined]
        assert info["duplicatePolicy"] == "last"
        assert info["labels"].get("entity") == "c-0004"


# ---------------------------------------------------------------------------
# Point write ops
# ---------------------------------------------------------------------------


@requires_redis
class TestPointWrite:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_point_write_array(self) -> None:
        req = GridBuilder().add_col("id").add_row({"id": Ref("c-0004")}).to_grid()
        resp = await self._client.post(
            "/api/pointWrite", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 17
        levels = [row["level"] for row in grid]
        assert levels[0] == 1.0
        assert levels[-1] == 17.0

    async def test_point_write_set_and_read(self) -> None:
        ref = Ref("c-0004")
        write_req = (
            GridBuilder()
            .add_col("id")
            .add_col("level")
            .add_col("val")
            .add_col("who")
            .add_row(
                {"id": ref, "level": Number(10.0), "val": Number(72.0, "\u00b0F"), "who": "test"}
            )
            .to_grid()
        )
        resp = await self._client.post(
            "/api/pointWrite",
            content=encode_grid(write_req),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        read_req = GridBuilder().add_col("id").add_row({"id": ref}).to_grid()
        resp = await self._client.post(
            "/api/pointWrite",
            content=encode_grid(read_req),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 17
        level_10 = grid[9]
        assert level_10["level"] == 10.0
        assert level_10["val"] == Number(72.0, "\u00b0F")


# ---------------------------------------------------------------------------
# Watch ops
# ---------------------------------------------------------------------------


@requires_redis
class TestWatchOps:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def _watch_sub(self, ids: list[str], watch_id: str | None = None) -> Grid:
        meta: dict[str, Any] = {"watchDis": "test"}
        if watch_id:
            meta["watchId"] = watch_id
        builder = GridBuilder().set_meta(meta).add_col("id")
        for ref_val in ids:
            builder.add_row({"id": Ref(ref_val)})
        resp = await self._client.post(
            "/api/watchSub",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        return decode_grid(resp.content)

    async def test_watch_sub_returns_state(self) -> None:
        grid = await self._watch_sub(["a-0000", "b-0000"])
        assert "watchId" in grid.meta
        assert len(grid) == 2
        ids = {row["id"].val for row in grid if isinstance(row.get("id"), Ref)}
        assert "a-0000" in ids
        assert "b-0000" in ids

    async def test_watch_poll_empty(self) -> None:
        sub = await self._watch_sub(["a-0000"])
        watch_id = sub.meta["watchId"]

        req = GridBuilder().set_meta({"watchId": watch_id}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

    async def test_watch_poll_refresh(self) -> None:
        sub = await self._watch_sub(["a-0000", "b-0000"])
        watch_id = sub.meta["watchId"]

        req = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2

    async def test_watch_unsub_removes_ids(self) -> None:
        sub = await self._watch_sub(["a-0000", "b-0000"])
        watch_id = sub.meta["watchId"]

        unsub_req = (
            GridBuilder()
            .set_meta({"watchId": watch_id})
            .add_col("id")
            .add_row({"id": Ref("b-0000")})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/watchUnsub",
            content=encode_grid(unsub_req),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        req = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 1
        assert grid[0]["id"] == Ref("a-0000")

    async def test_watch_unsub_close(self) -> None:
        sub = await self._watch_sub(["a-0000"])
        watch_id = sub.meta["watchId"]

        close_req = (
            GridBuilder().set_meta({"watchId": watch_id, "close": MARKER}).add_col("id").to_grid()
        )
        resp = await self._client.post(
            "/api/watchUnsub",
            content=encode_grid(close_req),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        req = GridBuilder().set_meta({"watchId": watch_id}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert grid.is_error


# ---------------------------------------------------------------------------
# Invoke action
# ---------------------------------------------------------------------------


@requires_redis
class TestInvokeAction:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_invoke_action(self) -> None:
        req = (
            GridBuilder()
            .set_meta({"id": Ref("a-0001"), "action": "doIt"})
            .add_col("arg1")
            .add_row({"arg1": "val1"})
            .to_grid()
        )
        resp = await self._client.post(
            "/api/invokeAction",
            content=encode_grid(req),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert grid[0]["action"] == "doIt"
        assert grid[0]["result"] == "ok"


# ---------------------------------------------------------------------------
# Ontology ops
# ---------------------------------------------------------------------------


@requires_redis
class TestDefsOps:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        from hs_py.ontology.defs import Def, Lib
        from hs_py.ontology.namespace import Namespace

        self._ops = await _make_redis_ops()
        defs = [
            Def(symbol=Symbol("site"), tags={"def": Symbol("site"), "doc": "Site entity"}),
            Def(
                symbol=Symbol("equip"),
                tags={"def": Symbol("equip"), "doc": "Equipment entity", "is": Symbol("entity")},
            ),
            Def(
                symbol=Symbol("point"),
                tags={"def": Symbol("point"), "doc": "Point entity", "is": Symbol("entity")},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:demo"), version="1.0.0", defs=tuple(defs))
        ns = Namespace([lib])
        self._ops._namespace = ns
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_defs_returns_rows(self) -> None:
        resp = await self._client.post(
            "/api/defs",
            content=encode_grid(Grid.make_empty()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 3
        names = {str(row["def"]) for row in grid if "def" in row}
        assert "site" in names or Symbol("site") in {row.get("def") for row in grid}

    async def test_defs_with_filter(self) -> None:
        req = GridBuilder().add_col("filter").add_row({"filter": "doc"}).to_grid()
        resp = await self._client.post(
            "/api/defs", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 1
        for row in grid:
            assert "doc" in row

    async def test_libs_returns_rows(self) -> None:
        resp = await self._client.post(
            "/api/libs",
            content=encode_grid(Grid.make_empty()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 1
        assert grid[0]["version"] == "1.0.0"

    async def test_filetypes_returns_formats(self) -> None:
        resp = await self._client.post(
            "/api/filetypes",
            content=encode_grid(Grid.make_empty()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 4
        mimes = {row["mime"] for row in grid}
        assert "application/json" in mimes
        assert "text/zinc" in mimes
        assert "text/trio" in mimes
        assert "text/csv" in mimes


# ---------------------------------------------------------------------------
# Entity update with stale tag cleanup
# ---------------------------------------------------------------------------


@requires_redis
class TestEntityUpdate:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_store_entity_removes_stale_tags(self) -> None:
        """When updating an entity, removed tags should be cleaned from indexes."""
        r = self._ops._adapter._r  # type: ignore[attr-defined]

        # Store an entity with tags: id, site, geoCity
        entity_v1 = {"id": Ref("test-update"), "site": MARKER, "geoCity": "London"}
        await self._ops._adapter._store_entity("test-update", entity_v1)  # type: ignore[attr-defined]

        # Verify tag indexes
        assert await r.sismember("hs:tag:geoCity", "test-update")

        # Update: remove geoCity, add geoCountry
        entity_v2 = {"id": Ref("test-update"), "site": MARKER, "geoCountry": "UK"}
        await self._ops._adapter._store_entity("test-update", entity_v2)  # type: ignore[attr-defined]

        # geoCity tag index should no longer contain this ref
        assert not await r.sismember("hs:tag:geoCity", "test-update")
        # geoCountry should be indexed
        assert await r.sismember("hs:tag:geoCountry", "test-update")
        # site should still be indexed
        assert await r.sismember("hs:tag:site", "test-update")


# ---------------------------------------------------------------------------
# RediSearch index schema migration
# ---------------------------------------------------------------------------


@requires_redis
class TestSearchIndexMigration:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_index_has_expected_fields(self) -> None:
        """Verify the RediSearch index contains siteRef and equipRef fields."""
        from hs_py.storage.redis import _FT_INDEX, _parse_ft_fields

        info = await self._ops._adapter._r.ft(_FT_INDEX).info()  # type: ignore[attr-defined]
        fields = _parse_ft_fields(info)
        assert "_tags" in fields
        assert "siteRef" in fields
        assert "equipRef" in fields


# ---------------------------------------------------------------------------
# Client <-> Server integration
# ---------------------------------------------------------------------------


@requires_redis
class TestClientIntegration:
    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_client_read_filter(self) -> None:
        req = GridBuilder().add_col("filter").add_row({"filter": "site"}).to_grid()
        resp = await self._client.post(
            "/api/read", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 2

    async def test_client_read_by_ids(self) -> None:
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("a-0000")})
        builder.add_row({"id": Ref("a-0001")})
        resp = await self._client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2

    async def test_client_nav(self) -> None:
        req = GridBuilder().add_col("navId").add_row({"navId": None}).to_grid()
        resp = await self._client.post(
            "/api/nav", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 2
        for row in grid:
            assert "site" in row

    async def test_client_watch_lifecycle(self) -> None:
        # Subscribe
        builder = (
            GridBuilder()
            .set_meta({"watchDis": "test-watch"})
            .add_col("id")
            .add_row({"id": Ref("a-0000")})
            .add_row({"id": Ref("a-0001")})
        )
        resp = await self._client.post(
            "/api/watchSub",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        sub = decode_grid(resp.content)
        watch_id = sub.meta["watchId"]
        assert len(sub) == 2

        # Poll (empty)
        req = GridBuilder().set_meta({"watchId": watch_id}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        poll = decode_grid(resp.content)
        assert poll.is_empty

        # Poll (refresh)
        req = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        poll = decode_grid(resp.content)
        assert len(poll) == 2

        # Unsub one
        unsub_req = (
            GridBuilder()
            .set_meta({"watchId": watch_id})
            .add_col("id")
            .add_row({"id": Ref("a-0001")})
            .to_grid()
        )
        await self._client.post(
            "/api/watchUnsub",
            content=encode_grid(unsub_req),
            headers={"Content-Type": _JSON_CT},
        )

        # Poll (refresh) - should have only 1
        req = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        poll = decode_grid(resp.content)
        assert len(poll) == 1

        # Close
        close_req = (
            GridBuilder().set_meta({"watchId": watch_id, "close": MARKER}).add_col("id").to_grid()
        )
        await self._client.post(
            "/api/watchUnsub",
            content=encode_grid(close_req),
            headers={"Content-Type": _JSON_CT},
        )
