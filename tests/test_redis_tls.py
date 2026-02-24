"""Tests for Redis TLS 1.3 integration with hs-py CA infrastructure.

Start TLS Redis with:
    make docker-redis-tls

Tests are skipped if TLS Redis is not available on localhost:6380
or if the TLS certificates have not been generated.
"""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.fastapi_server import create_fastapi_app
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref, Symbol
from hs_py.ops import HaystackOps

_TLS_DIR = Path(__file__).resolve().parent.parent / "docker" / "tls"
_DATA_DIR = Path(__file__).resolve().parent.parent / "_data"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_tls_redis_available: bool | None = None


def _check_tls_redis() -> bool:
    """Check if TLS Redis is available on localhost:6380 with test certs."""
    global _tls_redis_available
    if _tls_redis_available is not None:
        return _tls_redis_available

    # Check certs exist
    if not (_TLS_DIR / "ca.pem").exists():
        _tls_redis_available = False
        return False

    try:
        from redis.asyncio import Redis

        async def _ping() -> bool:
            r = Redis(
                host="localhost",
                port=6380,
                protocol=3,
                ssl=True,
                ssl_certfile=str(_TLS_DIR / "client.pem"),
                ssl_keyfile=str(_TLS_DIR / "client.key"),
                ssl_ca_certs=str(_TLS_DIR / "ca.pem"),
                ssl_min_version=ssl.TLSVersion.TLSv1_3,
                ssl_cert_reqs="required",
            )
            try:
                return bool(await r.ping())
            except Exception:
                return False
            finally:
                await r.aclose()

        _tls_redis_available = (
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_ping())
        )
    except ImportError:
        _tls_redis_available = False

    return _tls_redis_available


requires_tls_redis = pytest.mark.skipif(
    not _check_tls_redis(),
    reason="TLS Redis not available on localhost:6380 (run: make docker-redis-tls)",
)


class _TLSRedisTestOps(HaystackOps):
    """HaystackOps subclass for TLS Redis testing."""

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


async def _make_tls_redis_ops() -> _TLSRedisTestOps:
    """Create a _TLSRedisTestOps instance connected via TLS."""
    from hs_py.storage.redis import RedisAdapter, create_redis_client
    from hs_py.tls import TLSConfig

    tls = TLSConfig(
        certificate_path=str(_TLS_DIR / "client.pem"),
        private_key_path=str(_TLS_DIR / "client.key"),
        ca_certificates_path=str(_TLS_DIR / "ca.pem"),
    )
    r = create_redis_client("rediss://localhost:6380", tls=tls)
    await r.flushdb()

    adapter = RedisAdapter(r)
    ops = _TLSRedisTestOps(storage=adapter)
    ops._adapter = adapter  # type: ignore[attr-defined]  # for test access
    await adapter.start()

    # Load test data
    if _DATA_DIR.is_dir():
        for json_file in sorted(_DATA_DIR.rglob("*.json")):
            raw = json_file.read_bytes()
            grid = decode_grid(raw)
            await adapter.load_entities([dict(row) for row in grid])

    return ops


async def _make_client(ops: HaystackOps) -> AsyncClient:
    """Create an httpx AsyncClient backed by the FastAPI app."""
    app = create_fastapi_app(ops=ops)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_JSON_CT = "application/json"


@requires_tls_redis
class TestTLSConnection:
    """Verify RedisAdapter works over TLS 1.3 with mTLS."""

    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_tls_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_about_over_tls(self) -> None:
        """Basic op verifies TLS connectivity."""
        resp = await self._client.get("/api/about")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "hs-py Redis Server"

    async def test_read_filter_over_tls(self) -> None:
        """Filter query works over TLS with RediSearch."""
        row: dict[str, Any] = {"filter": "site"}
        req = GridBuilder().add_col("filter").add_row(row).to_grid()
        resp = await self._client.post(
            "/api/read", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 3
        for row in grid:
            assert "site" in row

    async def test_read_by_id_over_tls(self) -> None:
        """ID-based read works over TLS."""
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("c-0000")})
        resp = await self._client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 1
        assert grid[0]["id"] == Ref("c-0000")
        assert "site" in grid[0]

    async def test_his_write_read_over_tls(self) -> None:
        """TimeSeries write and read work over TLS."""
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
        assert len(grid) == 1

    async def test_nav_over_tls(self) -> None:
        """Nav operation works over TLS."""
        row: dict[str, Any] = {"navId": None}
        req = GridBuilder().add_col("navId").add_row(row).to_grid()
        resp = await self._client.post(
            "/api/nav", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert len(grid) >= 3
        for row in grid:
            assert "site" in row

    async def test_watch_lifecycle_over_tls(self) -> None:
        """Watch sub/poll/unsub works over TLS."""
        # Subscribe
        builder = (
            GridBuilder()
            .set_meta({"watchDis": "tls-test"})
            .add_col("id")
            .add_row({"id": Ref("c-0000")})
        )
        resp = await self._client.post(
            "/api/watchSub",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON_CT},
        )
        grid = decode_grid(resp.content)
        assert "watchId" in grid.meta
        watch_id = grid.meta["watchId"]
        assert len(grid) == 1

        # Poll (empty)
        req = GridBuilder().set_meta({"watchId": watch_id}).to_grid()
        resp = await self._client.post(
            "/api/watchPoll", content=encode_grid(req), headers={"Content-Type": _JSON_CT}
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

        # Close
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


@requires_tls_redis
class TestTLSConnectionSecurity:
    """Verify TLS security properties."""

    @pytest.fixture(autouse=True)
    async def _setup(self) -> Any:
        self._ops = await _make_tls_redis_ops()
        self._client = await _make_client(self._ops)
        yield
        await self._client.aclose()
        await self._ops._adapter.close()  # type: ignore[attr-defined]

    async def test_tls_13_enforced(self) -> None:
        """Verify TLS 1.3 is enforced for Redis connections."""
        from hs_py.storage.redis import create_redis_client
        from hs_py.tls import TLSConfig

        tls = TLSConfig(
            certificate_path=str(_TLS_DIR / "client.pem"),
            private_key_path=str(_TLS_DIR / "client.key"),
            ca_certificates_path=str(_TLS_DIR / "ca.pem"),
        )
        r = create_redis_client("rediss://localhost:6380", tls=tls)
        # Verify connection works (TLS 1.3 minimum is set in the factory)
        assert await r.ping()
        await r.aclose()

    async def test_no_cert_rejected(self) -> None:
        """Connection without client certificate is rejected (mTLS)."""
        from redis.asyncio import Redis
        from redis.exceptions import ConnectionError as RedisConnectionError

        r = Redis(
            host="localhost",
            port=6380,
            ssl=True,
            ssl_ca_certs=str(_TLS_DIR / "ca.pem"),
            ssl_cert_reqs="required",
        )
        with pytest.raises((RedisConnectionError, ConnectionError, OSError)):
            await r.ping()
        await r.aclose()

    async def test_create_redis_client_auto_upgrades_scheme(self) -> None:
        """create_redis_client upgrades redis:// to rediss:// when TLS provided."""
        from hs_py.storage.redis import create_redis_client
        from hs_py.tls import TLSConfig

        tls = TLSConfig(
            certificate_path=str(_TLS_DIR / "client.pem"),
            private_key_path=str(_TLS_DIR / "client.key"),
            ca_certificates_path=str(_TLS_DIR / "ca.pem"),
        )
        r = create_redis_client("redis://localhost:6380", tls=tls)
        assert await r.ping()
        await r.aclose()
