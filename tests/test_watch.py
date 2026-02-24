"""Tests for watch delta encoding and filtering (watch.py)."""

from __future__ import annotations

import asyncio

from hs_py.filter.parser import parse
from hs_py.grid import Grid
from hs_py.kinds import MARKER, REMOVE, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.watch import WatchAccumulator, WatchState
from hs_py.ws_client import WebSocketClient
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# WatchState — delta computation
# ---------------------------------------------------------------------------


class TestWatchStateDelta:
    def test_new_entities_sent_in_full(self) -> None:
        state = WatchState("w-1")
        current = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
            ]
        )
        delta = state.compute_delta(current)
        assert len(delta) == 1
        assert delta[0]["id"] == Ref("p1")
        assert delta[0]["dis"] == "Point 1"

    def test_unchanged_entities_not_in_delta(self) -> None:
        state = WatchState("w-1")
        current = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
            ]
        )
        state.update(current)
        delta = state.compute_delta(current)
        assert delta.is_empty

    def test_changed_tag_in_delta(self) -> None:
        state = WatchState("w-1")
        v1 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
            ]
        )
        state.update(v1)

        v2 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(73.0)},
            ]
        )
        delta = state.compute_delta(v2)
        assert len(delta) == 1
        assert delta[0]["id"] == Ref("p1")
        assert delta[0]["val"] == Number(73.0)
        # Unchanged tag "dis" should not be in the delta
        assert "dis" not in delta[0]

    def test_removed_tag_in_delta(self) -> None:
        state = WatchState("w-1")
        v1 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0), "sensor": MARKER},
            ]
        )
        state.update(v1)

        v2 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
            ]
        )
        delta = state.compute_delta(v2)
        assert len(delta) == 1
        assert delta[0]["sensor"] is REMOVE

    def test_removed_entity(self) -> None:
        state = WatchState("w-1")
        v1 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1"},
                {"id": Ref("p2"), "dis": "Point 2"},
            ]
        )
        state.update(v1)

        v2 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1"},
            ]
        )
        delta = state.compute_delta(v2)
        assert len(delta) == 1
        assert delta[0]["id"] == Ref("p2")
        assert delta[0]["_removed"] is MARKER

    def test_update_clears_removed_entities(self) -> None:
        state = WatchState("w-1")
        v1 = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1"},
                {"id": Ref("p2"), "dis": "Point 2"},
            ]
        )
        state.update(v1)

        v2 = Grid.make_rows([{"id": Ref("p1"), "dis": "Point 1"}])
        state.update(v2)

        # Now p2 is gone from cache; no removal delta
        delta = state.compute_delta(v2)
        assert delta.is_empty


# ---------------------------------------------------------------------------
# WatchAccumulator — client-side delta merging
# ---------------------------------------------------------------------------


class TestWatchAccumulator:
    def test_apply_new_entity(self) -> None:
        acc = WatchAccumulator()
        delta = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
            ]
        )
        acc.apply_delta(delta)
        assert acc.get("p1") is not None
        assert acc.get("p1")["dis"] == "Point 1"

    def test_apply_tag_update(self) -> None:
        acc = WatchAccumulator()
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.0)},
                ]
            )
        )
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "val": Number(73.0)},
                ]
            )
        )
        entity = acc.get("p1")
        assert entity is not None
        assert entity["val"] == Number(73.0)
        assert entity["dis"] == "Point 1"  # Preserved from first push

    def test_apply_tag_removal(self) -> None:
        acc = WatchAccumulator()
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1", "sensor": MARKER},
                ]
            )
        )
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "sensor": REMOVE},
                ]
            )
        )
        entity = acc.get("p1")
        assert entity is not None
        assert "sensor" not in entity
        assert entity["dis"] == "Point 1"

    def test_apply_entity_removal(self) -> None:
        acc = WatchAccumulator()
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1"},
                ]
            )
        )
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "_removed": MARKER},
                ]
            )
        )
        assert acc.get("p1") is None

    def test_to_grid(self) -> None:
        acc = WatchAccumulator()
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1"},
                    {"id": Ref("p2"), "dis": "Point 2"},
                ]
            )
        )
        grid = acc.to_grid()
        assert len(grid) == 2

    def test_to_grid_empty(self) -> None:
        acc = WatchAccumulator()
        assert acc.to_grid().is_empty

    def test_entities_dict(self) -> None:
        acc = WatchAccumulator()
        acc.apply_delta(
            Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1"},
                ]
            )
        )
        assert "p1" in acc.entities


# ---------------------------------------------------------------------------
# WatchState — server-side filtering
# ---------------------------------------------------------------------------


class TestWatchStateFilter:
    def test_no_filter_passes_all(self) -> None:
        state = WatchState("w-1")
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "sensor": MARKER},
                {"id": Ref("p2"), "equip": MARKER},
            ]
        )
        result = state.apply_filter(grid)
        assert len(result) == 2

    def test_filter_matches_subset(self) -> None:
        ast = parse("point")
        state = WatchState("w-1", filter_ast=ast)
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "sensor": MARKER},
                {"id": Ref("p2"), "equip": MARKER},
            ]
        )
        result = state.apply_filter(grid)
        assert len(result) == 1
        assert result[0]["id"] == Ref("p1")

    def test_filter_no_matches(self) -> None:
        ast = parse("site")
        state = WatchState("w-1", filter_ast=ast)
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER},
            ]
        )
        result = state.apply_filter(grid)
        assert result.is_empty

    def test_filter_complex_expression(self) -> None:
        ast = parse("point and sensor")
        state = WatchState("w-1", filter_ast=ast)
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "sensor": MARKER},
                {"id": Ref("p2"), "point": MARKER},
            ]
        )
        result = state.apply_filter(grid)
        assert len(result) == 1
        assert result[0]["id"] == Ref("p1")


# ---------------------------------------------------------------------------
# Delta + filter combined
# ---------------------------------------------------------------------------


class TestDeltaAndFilter:
    def test_filter_then_delta(self) -> None:
        """Server filters full state, then computes delta for push."""
        ast = parse("point")
        state = WatchState("w-1", filter_ast=ast)

        v1 = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "val": Number(72.0)},
                {"id": Ref("e1"), "equip": MARKER, "val": Number(100.0)},
            ]
        )
        # Filter first, then update cache
        filtered_v1 = state.apply_filter(v1)
        state.update(filtered_v1)

        v2 = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "val": Number(73.0)},
                {"id": Ref("e1"), "equip": MARKER, "val": Number(101.0)},
            ]
        )
        # Filter again, then compute delta
        filtered_v2 = state.apply_filter(v2)
        delta = state.compute_delta(filtered_v2)

        # Only the point entity change is tracked
        assert len(delta) == 1
        assert delta[0]["id"] == Ref("p1")
        assert delta[0]["val"] == Number(73.0)


# ---------------------------------------------------------------------------
# Batch operations integration
# ---------------------------------------------------------------------------


class _BatchTestOps(HaystackOps):
    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "BatchTest"}])

    async def read(self, grid: Grid) -> Grid:
        return Grid.make_rows([{"id": Ref("p1"), "dis": "Point 1"}])

    async def nav(self, grid: Grid) -> Grid:
        return Grid.make_rows([{"navId": "site-1", "dis": "Site 1"}])


class TestBatchOperations:
    async def test_batch_two_ops(self) -> None:
        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, pythonic=False) as client:
                results = await client.batch(
                    ("about", Grid.make_empty()),
                    ("read", Grid.make_empty()),
                )
                assert len(results) == 2
                assert results[0][0]["serverName"] == "BatchTest"
                assert results[1][0]["id"] == Ref("p1")
        finally:
            await server.stop()

    async def test_batch_three_ops(self) -> None:
        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, pythonic=False) as client:
                results = await client.batch(
                    ("about", Grid.make_empty()),
                    ("read", Grid.make_empty()),
                    ("nav", Grid.make_empty()),
                )
                assert len(results) == 3
                assert results[0][0]["serverName"] == "BatchTest"
                assert results[1][0]["id"] == Ref("p1")
                assert results[2][0]["navId"] == "site-1"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Compression integration
# ---------------------------------------------------------------------------


class TestCompressionIntegration:
    async def test_compression_about(self) -> None:
        """Compression-enabled client/server can communicate."""
        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, compression=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, compression=True, pythonic=False) as client:
                grid = await client.about()
                assert grid[0]["serverName"] == "BatchTest"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Connection pool integration
# ---------------------------------------------------------------------------


class TestWebSocketPool:
    async def test_pool_channel_about(self) -> None:
        from hs_py.ws_client import WebSocketPool

        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketPool(url) as pool:
                ch1 = pool.channel("tenant-1")
                ch2 = pool.channel("tenant-2")
                g1 = await ch1.about()
                g2 = await ch2.about()
                assert g1[0]["serverName"] == "BatchTest"
                assert g2[0]["serverName"] == "BatchTest"
        finally:
            await server.stop()

    async def test_pool_channel_read(self) -> None:
        from hs_py.ws_client import WebSocketPool

        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketPool(url) as pool:
                ch = pool.channel("t1")
                grid = await ch.read("point")
                assert len(grid) == 1
                assert grid[0]["id"] == Ref("p1")
        finally:
            await server.stop()

    async def test_pool_concurrent(self) -> None:
        from hs_py.ws_client import WebSocketPool

        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketPool(url) as pool:
                ch1 = pool.channel("t1")
                ch2 = pool.channel("t2")
                g1, g2 = await asyncio.gather(ch1.about(), ch2.about())
                assert g1[0]["serverName"] == "BatchTest"
                assert g2[0]["serverName"] == "BatchTest"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Reconnecting client
# ---------------------------------------------------------------------------


class TestReconnectingClient:
    async def test_reconnect_after_server_restart(self) -> None:
        from hs_py.ws_client import ReconnectingWebSocketClient

        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        port = server.port
        url = f"ws://127.0.0.1:{port}"

        connected_count = 0

        async def on_connect() -> None:
            nonlocal connected_count
            connected_count += 1

        client = ReconnectingWebSocketClient(
            url,
            min_reconnect_delay=0.1,
            max_reconnect_delay=0.5,
            on_connect=on_connect,
            pythonic=False,
        )
        try:
            await client.start()
            grid = await client.about()
            assert grid[0]["serverName"] == "BatchTest"
            assert connected_count == 1
        finally:
            await client.stop()
            await server.stop()

    async def test_reconnect_on_watch_push_preserved(self) -> None:
        """Watch push callback is preserved across reconnects."""
        from hs_py.ws_client import ReconnectingWebSocketClient

        ops = _BatchTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        url = f"ws://127.0.0.1:{server.port}"

        pushes: list[str] = []

        def on_push(watch_id: str, grid: Grid) -> None:
            pushes.append(watch_id)

        client = ReconnectingWebSocketClient(
            url,
            min_reconnect_delay=0.1,
            max_reconnect_delay=0.5,
            pythonic=False,
        )
        client.on_watch_push(on_push)
        try:
            await client.start()
            # The callback should be registered on the inner client
            inner = client._require_inner()
            assert inner._watch_callback is not None
        finally:
            await client.stop()
            await server.stop()


# ---------------------------------------------------------------------------
# Mutual TLS cert auth
# ---------------------------------------------------------------------------


class TestCertAuthenticator:
    def test_authorize_matching_cn(self) -> None:
        from hs_py.auth_types import CertAuthenticator

        auth = CertAuthenticator({"Haystack Test Client"})
        peercert: dict[str, object] = {
            "subject": ((("commonName", "Haystack Test Client"),),),
        }
        assert auth.authorize(peercert) == "Haystack Test Client"

    def test_authorize_non_matching_cn(self) -> None:
        from hs_py.auth_types import CertAuthenticator

        auth = CertAuthenticator({"admin"})
        peercert: dict[str, object] = {
            "subject": ((("commonName", "Haystack Test Client"),),),
        }
        assert auth.authorize(peercert) is None

    def test_authorize_no_cert(self) -> None:
        from hs_py.auth_types import CertAuthenticator

        auth = CertAuthenticator({"admin"})
        assert auth.authorize(None) is None


# ---------------------------------------------------------------------------
# TLS peer cert extraction
# ---------------------------------------------------------------------------


class TestPeerCertExtraction:
    def test_extract_cn(self) -> None:
        from hs_py.tls import extract_peer_cn

        cert: dict[str, object] = {
            "subject": ((("commonName", "Test Client"),),),
        }
        assert extract_peer_cn(cert) == "Test Client"

    def test_extract_cn_none(self) -> None:
        from hs_py.tls import extract_peer_cn

        assert extract_peer_cn(None) is None

    def test_extract_cn_no_cn(self) -> None:
        from hs_py.tls import extract_peer_cn

        cert: dict[str, object] = {
            "subject": ((("organizationName", "Test"),),),
        }
        assert extract_peer_cn(cert) is None

    def test_extract_sans(self) -> None:
        from hs_py.tls import extract_peer_sans

        cert: dict[str, object] = {
            "subjectAltName": (("DNS", "localhost"), ("IP Address", "127.0.0.1")),
        }
        sans = extract_peer_sans(cert)
        assert "localhost" in sans
        assert "127.0.0.1" in sans

    def test_extract_sans_none(self) -> None:
        from hs_py.tls import extract_peer_sans

        assert extract_peer_sans(None) == []
