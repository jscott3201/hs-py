"""Tests for hs_py.ops to cover uncovered lines."""

from __future__ import annotations

from typing import Any

from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref, Symbol
from hs_py.ontology.defs import Def, Lib
from hs_py.ops import HaystackOps, dispatch_op
from hs_py.storage.memory import InMemoryAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeNamespace:
    """Minimal namespace mock for defs/libs ops."""

    def all_defs(self) -> list[Def]:
        return [
            Def(
                symbol=Symbol("site"),
                tags={"doc": "Site marker", "is": Symbol("marker"), "special": MARKER},
            ),
            Def(symbol=Symbol("equip"), tags={"doc": "Equipment marker", "is": Symbol("marker")}),
        ]

    def all_libs(self) -> list[Lib]:
        return [
            Lib(symbol=Symbol("ph"), version="4.0"),
            Lib(symbol=Symbol("phIoT"), version="3.0"),
        ]


class RaisingWatchAdapter:
    """Adapter whose watch_unsub/watch_poll always raise ValueError."""

    async def watch_unsub(self, watch_id: str, ids: list[Ref], *, close: bool = False) -> None:
        raise ValueError("no such watch")

    async def watch_poll(self, watch_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        raise ValueError("no such watch")


def _make_ops(
    entities: list[dict[str, Any]] | None = None,
    namespace: Any = None,
) -> HaystackOps:
    adapter = InMemoryAdapter(entities=entities)
    return HaystackOps(storage=adapter, namespace=namespace)


SITE = {"id": Ref("s1"), "dis": "Site 1", "site": MARKER}
EQUIP = {"id": Ref("e1"), "dis": "Equip 1", "equip": MARKER, "siteRef": Ref("s1")}
POINT = {"id": Ref("p1"), "dis": "Point 1", "point": MARKER, "equipRef": Ref("e1")}


# ---------------------------------------------------------------------------
# about (line 53)
# ---------------------------------------------------------------------------


class TestAbout:
    async def test_about_returns_haystack_version(self) -> None:
        ops = _make_ops()
        g = await ops.about()
        assert len(g) == 1
        assert g[0]["haystackVersion"] == "4.0"


# ---------------------------------------------------------------------------
# read (lines 114, 125)
# ---------------------------------------------------------------------------


class TestRead:
    async def test_read_empty_grid(self) -> None:
        ops = _make_ops([SITE])
        g = await ops.read(Grid.make_empty())
        assert len(g) == 0

    async def test_read_by_filter_with_number_limit(self) -> None:
        ops = _make_ops([SITE, EQUIP, POINT])
        req = GridBuilder().add_col("filter").add_col("limit")
        req.add_row({"filter": "point or site or equip", "limit": Number(1.0)})
        g = await ops.read(req.to_grid())
        assert len(g) == 1

    async def test_read_by_id(self) -> None:
        ops = _make_ops([SITE, POINT])
        req = GridBuilder().add_col("id")
        req.add_row({"id": Ref("s1")})
        req.add_row({"id": Ref("p1")})
        g = await ops.read(req.to_grid())
        assert len(g) == 2


# ---------------------------------------------------------------------------
# nav (lines 136, 149, 151, 154)
# ---------------------------------------------------------------------------


class TestNav:
    async def test_nav_not_supported_without_storage(self) -> None:
        ops = HaystackOps()
        g = await ops.nav(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_nav_empty_grid_returns_sites(self) -> None:
        ops = _make_ops([SITE, EQUIP, POINT])
        g = await ops.nav(Grid.make_empty())
        assert any(r.get("site") for r in g)

    async def test_nav_with_nav_id(self) -> None:
        ops = _make_ops([SITE, EQUIP, POINT])
        req = GridBuilder().add_col("navId").add_row({"navId": "s1"}).to_grid()
        g = await ops.nav(req)
        assert len(g) == 1
        assert g[0]["id"] == Ref("e1")


# ---------------------------------------------------------------------------
# his_read (lines 149, 151, 154)
# ---------------------------------------------------------------------------


class TestHisRead:
    async def test_his_read_not_supported_without_storage(self) -> None:
        ops = HaystackOps()
        g = await ops.his_read(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_his_read_empty_grid(self) -> None:
        ops = _make_ops([POINT])
        g = await ops.his_read(Grid.make_empty())
        assert len(g) == 0

    async def test_his_read_non_ref_id(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().add_col("id").add_row({"id": "not-a-ref"}).to_grid()
        g = await ops.his_read(req)
        assert len(g) == 0

    async def test_his_read_with_data(self) -> None:
        adapter = InMemoryAdapter(entities=[POINT])
        await adapter.start()
        await adapter.his_write(Ref("p1"), [{"ts": "2024-01-01", "val": Number(72.0)}])
        ops = HaystackOps(storage=adapter)
        req = GridBuilder().add_col("id").add_row({"id": Ref("p1")}).to_grid()
        g = await ops.his_read(req)
        assert len(g) == 1
        assert g[0]["val"] == Number(72.0)


# ---------------------------------------------------------------------------
# his_write (lines 177, 180)
# ---------------------------------------------------------------------------


class TestHisWrite:
    async def test_his_write_not_supported_without_storage(self) -> None:
        ops = HaystackOps()
        g = await ops.his_write(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_his_write_non_ref_meta(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"id": "not-a-ref"}).add_col("ts").add_col("val")
        req.add_row({"ts": "2024-01-01", "val": Number(1.0)})
        g = await ops.his_write(req.to_grid())
        assert len(g) == 0

    async def test_his_write_valid(self) -> None:
        adapter = InMemoryAdapter(entities=[POINT])
        ops = HaystackOps(storage=adapter)
        req = GridBuilder().set_meta({"id": Ref("p1")}).add_col("ts").add_col("val")
        req.add_row({"ts": "2024-01-01", "val": Number(42.0)})
        g = await ops.his_write(req.to_grid())
        assert len(g) == 0
        # Verify data was written
        items = await adapter.his_read(Ref("p1"))
        assert len(items) == 1


# ---------------------------------------------------------------------------
# point_write (lines 191, 195, 220)
# ---------------------------------------------------------------------------


class TestPointWrite:
    async def test_point_write_empty_grid(self) -> None:
        ops = _make_ops([POINT])
        g = await ops.point_write(Grid.make_empty())
        assert len(g) == 0

    async def test_point_write_non_ref_id(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().add_col("id").add_row({"id": "not-a-ref"}).to_grid()
        g = await ops.point_write(req)
        assert len(g) == 0

    async def test_point_write_with_level(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().add_col("id").add_col("level").add_col("val")
        req.add_row({"id": Ref("p1"), "level": Number(8.0), "val": Number(72.0)})
        g = await ops.point_write(req.to_grid())
        assert len(g) == 0

    async def test_point_write_read_array(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().add_col("id").add_row({"id": Ref("p1")}).to_grid()
        g = await ops.point_write(req)
        assert len(g) == 17


# ---------------------------------------------------------------------------
# watch_sub (lines 220, 236, 239)
# ---------------------------------------------------------------------------


class TestWatchSub:
    async def test_watch_sub_entities_returned(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"watchDis": "test"}).add_col("id")
        req.add_row({"id": Ref("p1")})
        g = await ops.watch_sub(req.to_grid())
        assert g.meta.get("watchId") is not None
        assert len(g) == 1

    async def test_watch_sub_empty_result(self) -> None:
        ops = _make_ops()
        req = GridBuilder().set_meta({"watchDis": "test"}).add_col("id")
        req.add_row({"id": Ref("nonexistent")})
        g = await ops.watch_sub(req.to_grid())
        assert g.meta.get("watchId") is not None
        assert len(g) == 0


# ---------------------------------------------------------------------------
# watch_unsub (lines 236, 239, 244-245)
# ---------------------------------------------------------------------------


class TestWatchUnsub:
    async def test_watch_unsub_not_supported_without_storage(self) -> None:
        ops = HaystackOps()
        g = await ops.watch_unsub(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_watch_unsub_non_string_watch_id(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"watchId": 123}).add_col("id").to_grid()
        g = await ops.watch_unsub(req)
        assert g.meta.get("err") is not None

    async def test_watch_unsub_unknown_watch(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"watchId": "bad-id"}).add_col("id")
        req.add_row({"id": Ref("p1")})
        g = await ops.watch_unsub(req.to_grid())
        # InMemoryAdapter.watch_unsub silently returns for unknown watches
        assert len(g) == 0

    async def test_watch_unsub_raises_value_error(self) -> None:
        ops = HaystackOps(storage=RaisingWatchAdapter())  # type: ignore[arg-type]
        req = GridBuilder().set_meta({"watchId": "w-1"}).add_col("id")
        req.add_row({"id": Ref("p1")})
        g = await ops.watch_unsub(req.to_grid())
        assert g.meta.get("err") is not None

    async def test_watch_unsub_valid(self) -> None:
        adapter = InMemoryAdapter(entities=[POINT])
        ops = HaystackOps(storage=adapter)
        # First subscribe
        sub_req = GridBuilder().set_meta({"watchDis": "test"}).add_col("id")
        sub_req.add_row({"id": Ref("p1")})
        sub_g = await ops.watch_sub(sub_req.to_grid())
        wid = sub_g.meta["watchId"]
        # Now unsub
        unsub_req = GridBuilder().set_meta({"watchId": wid}).add_col("id")
        unsub_req.add_row({"id": Ref("p1")})
        g = await ops.watch_unsub(unsub_req.to_grid())
        assert len(g) == 0


# ---------------------------------------------------------------------------
# watch_poll (lines 252, 255, 259-260)
# ---------------------------------------------------------------------------


class TestWatchPoll:
    async def test_watch_poll_not_supported_without_storage(self) -> None:
        ops = HaystackOps()
        g = await ops.watch_poll(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_watch_poll_non_string_watch_id(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"watchId": 42}).to_grid()
        g = await ops.watch_poll(req)
        assert g.meta.get("err") is not None

    async def test_watch_poll_unknown_watch(self) -> None:
        ops = _make_ops([POINT])
        req = GridBuilder().set_meta({"watchId": "bad-id"}).to_grid()
        g = await ops.watch_poll(req)
        # InMemoryAdapter.watch_poll returns [] for unknown, no ValueError
        assert len(g) == 0

    async def test_watch_poll_raises_value_error(self) -> None:
        ops = HaystackOps(storage=RaisingWatchAdapter())  # type: ignore[arg-type]
        req = GridBuilder().set_meta({"watchId": "w-1"}).to_grid()
        g = await ops.watch_poll(req)
        assert g.meta.get("err") is not None

    async def test_watch_poll_valid(self) -> None:
        adapter = InMemoryAdapter(entities=[POINT])
        ops = HaystackOps(storage=adapter)
        # Subscribe
        sub_req = GridBuilder().set_meta({"watchDis": "test"}).add_col("id")
        sub_req.add_row({"id": Ref("p1")})
        sub_g = await ops.watch_sub(sub_req.to_grid())
        wid = sub_g.meta["watchId"]
        # Poll with refresh to get all entities
        poll_req = GridBuilder().set_meta({"watchId": wid, "refresh": MARKER}).to_grid()
        g = await ops.watch_poll(poll_req)
        assert len(g) == 1


# ---------------------------------------------------------------------------
# invoke_action (line 265)
# ---------------------------------------------------------------------------


class TestInvokeAction:
    async def test_invoke_action_not_supported(self) -> None:
        ops = _make_ops()
        g = await ops.invoke_action(Grid.make_empty())
        assert g.meta.get("err") is not None


# ---------------------------------------------------------------------------
# filetypes (line 322)
# ---------------------------------------------------------------------------


class TestFiletypes:
    async def test_filetypes_not_supported(self) -> None:
        ops = _make_ops()
        g = await ops.filetypes(Grid.make_empty())
        assert g.meta.get("err") is not None


# ---------------------------------------------------------------------------
# defs / libs (lines 271, 279, 287, 290, 298, 303-306, 313, 316)
# ---------------------------------------------------------------------------


class TestDefs:
    async def test_defs_no_namespace(self) -> None:
        ops = _make_ops()
        g = await ops.defs(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_defs_with_namespace(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        g = await ops.defs(Grid.make_empty())
        assert len(g) == 2

    async def test_defs_with_filter_and_limit(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        req = GridBuilder().add_col("filter").add_col("limit")
        req.add_row({"filter": "doc", "limit": Number(1.0)})
        g = await ops.defs(req.to_grid())
        assert len(g) == 1

    async def test_defs_filter_skips_non_matching(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        req = GridBuilder().add_col("filter")
        req.add_row({"filter": "special"})
        g = await ops.defs(req.to_grid())
        # Only "site" has the "special" tag
        assert len(g) == 1


class TestLibs:
    async def test_libs_no_namespace(self) -> None:
        ops = _make_ops()
        g = await ops.libs(Grid.make_empty())
        assert g.meta.get("err") is not None

    async def test_libs_with_namespace(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        g = await ops.libs(Grid.make_empty())
        assert len(g) == 2

    async def test_libs_with_filter_and_limit(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        req = GridBuilder().add_col("filter").add_col("limit")
        req.add_row({"filter": "version", "limit": Number(1.0)})
        g = await ops.libs(req.to_grid())
        assert len(g) == 1

    async def test_libs_filter_skips_non_matching(self) -> None:
        ops = _make_ops(namespace=FakeNamespace())
        req = GridBuilder().add_col("filter")
        req.add_row({"filter": 'version == "4.0"'})
        g = await ops.libs(req.to_grid())
        # Only "ph" has version "4.0"
        assert len(g) == 1


# ---------------------------------------------------------------------------
# dispatch_op — "close" (lines 374-375)
# ---------------------------------------------------------------------------


class TestDispatchOp:
    async def test_dispatch_close(self) -> None:
        ops = _make_ops()
        g = await dispatch_op(ops, "close", {})
        assert len(g) == 0


# ---------------------------------------------------------------------------
# push_watch / set_push_handler
# ---------------------------------------------------------------------------


class TestPushWatch:
    async def test_push_watch_no_handler(self) -> None:
        ops = _make_ops()
        # Should be a no-op without error
        await ops.push_watch("w1", Grid.make_empty())

    async def test_push_watch_with_handler(self) -> None:
        ops = _make_ops()
        captured: list[tuple[str, Grid]] = []

        async def handler(wid: str, g: Grid) -> None:
            captured.append((wid, g))

        ops.set_push_handler(handler)
        g = Grid.make_rows([{"id": Ref("p1"), "val": Number(1.0)}])
        await ops.push_watch("w1", g)
        assert len(captured) == 1
        assert captured[0][0] == "w1"
