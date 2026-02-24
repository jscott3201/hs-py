"""Tests for the in-memory storage adapter."""

from __future__ import annotations

import datetime

import pytest

from hs_py.filter import parse
from hs_py.kinds import MARKER, Number, Ref
from hs_py.storage.memory import InMemoryAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> InMemoryAdapter:
    """Create an adapter preloaded with test entities."""
    entities = [
        {"id": Ref("s1"), "dis": "Site 1", "site": MARKER, "geoCity": "Richmond"},
        {"id": Ref("s2"), "dis": "Site 2", "site": MARKER, "geoCity": "Norfolk"},
        {"id": Ref("e1"), "dis": "AHU-1", "equip": MARKER, "siteRef": Ref("s1")},
        {"id": Ref("e2"), "dis": "AHU-2", "equip": MARKER, "siteRef": Ref("s2")},
        {
            "id": Ref("p1"),
            "dis": "ZAT",
            "point": MARKER,
            "equipRef": Ref("e1"),
            "kind": "Number",
            "unit": "°F",
        },
        {"id": Ref("p2"), "dis": "DAT", "point": MARKER, "equipRef": Ref("e1"), "kind": "Number"},
        {"id": Ref("p3"), "dis": "Fan", "point": MARKER, "equipRef": Ref("e2"), "kind": "Bool"},
    ]
    return InMemoryAdapter(entities)


# ---------------------------------------------------------------------------
# TestReadByFilter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_filter(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("site"))
    assert len(results) == 2
    ids = {e["id"].val for e in results}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_has_filter_with_limit(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("site"), limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_missing_filter(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("not point"))
    ids = {e["id"].val for e in results}
    # sites and equips should be included; no points
    assert "p1" not in ids
    assert "p2" not in ids
    assert "p3" not in ids
    assert "s1" in ids
    assert "e1" in ids


@pytest.mark.asyncio
async def test_cmp_filter(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse('geoCity == "Richmond"'))
    assert len(results) == 1
    assert results[0]["id"] == Ref("s1")
    assert results[0]["geoCity"] == "Richmond"


@pytest.mark.asyncio
async def test_and_filter(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("equip and siteRef"))
    ids = {e["id"].val for e in results}
    assert ids == {"e1", "e2"}


@pytest.mark.asyncio
async def test_no_matches(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("meter"))
    assert results == []


@pytest.mark.asyncio
async def test_filter_limit_none(adapter: InMemoryAdapter) -> None:
    # limit=None means no limit — all matching entities are returned
    results = await adapter.read_by_filter(parse("point"), limit=None)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_point_filter_all(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_filter(parse("point"))
    ids = {e["id"].val for e in results}
    assert ids == {"p1", "p2", "p3"}


# ---------------------------------------------------------------------------
# TestReadByIds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_existing_ids(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_ids([Ref("s1"), Ref("e1"), Ref("p1")])
    assert len(results) == 3
    assert results[0] is not None
    assert results[0]["id"] == Ref("s1")
    assert results[1] is not None
    assert results[1]["id"] == Ref("e1")
    assert results[2] is not None
    assert results[2]["id"] == Ref("p1")


@pytest.mark.asyncio
async def test_read_missing_ids(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_ids([Ref("missing1"), Ref("missing2")])
    assert results == [None, None]


@pytest.mark.asyncio
async def test_read_mixed_ids(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_ids([Ref("s1"), Ref("missing"), Ref("p3")])
    assert len(results) == 3
    assert results[0] is not None
    assert results[0]["id"] == Ref("s1")
    assert results[1] is None
    assert results[2] is not None
    assert results[2]["id"] == Ref("p3")


@pytest.mark.asyncio
async def test_read_by_ids_order_preserved(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_ids([Ref("p3"), Ref("s1"), Ref("e2")])
    assert len(results) == 3
    assert results[0]["id"] == Ref("p3")  # type: ignore[index]
    assert results[1]["id"] == Ref("s1")  # type: ignore[index]
    assert results[2]["id"] == Ref("e2")  # type: ignore[index]


@pytest.mark.asyncio
async def test_read_by_ids_empty_list(adapter: InMemoryAdapter) -> None:
    results = await adapter.read_by_ids([])
    assert results == []


# ---------------------------------------------------------------------------
# TestNav
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_nav(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav(None)
    ids = {e["id"].val for e in results}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_site_nav(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav("s1")
    ids = {e["id"].val for e in results}
    # Only equips with siteRef == s1
    assert ids == {"e1"}


@pytest.mark.asyncio
async def test_site_nav_second_site(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav("s2")
    ids = {e["id"].val for e in results}
    assert ids == {"e2"}


@pytest.mark.asyncio
async def test_equip_nav(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav("e1")
    ids = {e["id"].val for e in results}
    # Points with equipRef == e1
    assert ids == {"p1", "p2"}


@pytest.mark.asyncio
async def test_equip_nav_second_equip(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav("e2")
    ids = {e["id"].val for e in results}
    assert ids == {"p3"}


@pytest.mark.asyncio
async def test_unknown_nav(adapter: InMemoryAdapter) -> None:
    results = await adapter.nav("nonexistent")
    assert results == []


@pytest.mark.asyncio
async def test_point_nav(adapter: InMemoryAdapter) -> None:
    # Points are leaf nodes — navigating into one returns []
    results = await adapter.nav("p1")
    assert results == []


# ---------------------------------------------------------------------------
# TestHisReadWrite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_his_write_and_read(adapter: InMemoryAdapter) -> None:
    ts1 = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
    ts2 = datetime.datetime(2024, 1, 1, 1, 0, 0, tzinfo=datetime.UTC)
    items = [
        {"ts": ts1, "val": Number(72.0, "°F")},
        {"ts": ts2, "val": Number(74.5, "°F")},
    ]
    await adapter.his_write(Ref("p1"), items)
    result = await adapter.his_read(Ref("p1"))
    assert len(result) == 2
    assert result[0]["ts"] == ts1
    assert result[0]["val"] == Number(72.0, "°F")
    assert result[1]["ts"] == ts2
    assert result[1]["val"] == Number(74.5, "°F")


@pytest.mark.asyncio
async def test_his_read_empty(adapter: InMemoryAdapter) -> None:
    result = await adapter.his_read(Ref("p1"))
    assert result == []


@pytest.mark.asyncio
async def test_his_write_appends(adapter: InMemoryAdapter) -> None:
    ts1 = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
    ts2 = datetime.datetime(2024, 1, 1, 1, 0, 0, tzinfo=datetime.UTC)
    await adapter.his_write(Ref("p1"), [{"ts": ts1, "val": Number(72.0)}])
    await adapter.his_write(Ref("p1"), [{"ts": ts2, "val": Number(73.0)}])
    result = await adapter.his_read(Ref("p1"))
    assert len(result) == 2


@pytest.mark.asyncio
async def test_his_read_returns_copy(adapter: InMemoryAdapter) -> None:
    ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    await adapter.his_write(Ref("p1"), [{"ts": ts, "val": Number(72.0)}])
    result1 = await adapter.his_read(Ref("p1"))
    result2 = await adapter.his_read(Ref("p1"))
    # Should be different list objects (copy), but equal in content
    assert result1 == result2
    assert result1 is not result2


@pytest.mark.asyncio
async def test_his_write_unknown_ref(adapter: InMemoryAdapter) -> None:
    # Writing to an entity not in the store should still work (no error)
    ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    await adapter.his_write(Ref("unknown"), [{"ts": ts, "val": Number(0.0)}])
    result = await adapter.his_read(Ref("unknown"))
    assert len(result) == 1


@pytest.mark.asyncio
async def test_his_read_with_range_str(adapter: InMemoryAdapter) -> None:
    # range_str is accepted but currently ignored — all data is returned
    ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    await adapter.his_write(Ref("p1"), [{"ts": ts, "val": Number(72.0)}])
    result = await adapter.his_read(Ref("p1"), range_str="today")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# TestPointWrite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_and_read_array(adapter: InMemoryAdapter) -> None:
    await adapter.point_write(Ref("p1"), level=8, val=Number(72.0, "°F"))
    rows = await adapter.point_read_array(Ref("p1"))
    assert len(rows) == 17
    for row in rows:
        lvl = int(row["level"].val)
        if lvl == 8:
            assert "val" in row
            assert row["val"] == Number(72.0, "°F")
        else:
            assert "val" not in row


@pytest.mark.asyncio
async def test_clear_level(adapter: InMemoryAdapter) -> None:
    await adapter.point_write(Ref("p1"), level=8, val=Number(72.0))
    await adapter.point_write(Ref("p1"), level=8, val=None)
    rows = await adapter.point_read_array(Ref("p1"))
    row8 = next(r for r in rows if int(r["level"].val) == 8)
    assert "val" not in row8


@pytest.mark.asyncio
async def test_empty_array(adapter: InMemoryAdapter) -> None:
    rows = await adapter.point_read_array(Ref("p1"))
    assert len(rows) == 17
    for row in rows:
        assert "val" not in row


@pytest.mark.asyncio
async def test_priority_array_levels_are_numbers(adapter: InMemoryAdapter) -> None:
    rows = await adapter.point_read_array(Ref("p1"))
    for i, row in enumerate(rows):
        assert isinstance(row["level"], Number)
        assert int(row["level"].val) == i + 1


@pytest.mark.asyncio
async def test_write_multiple_levels(adapter: InMemoryAdapter) -> None:
    await adapter.point_write(Ref("p1"), level=1, val=Number(100.0))
    await adapter.point_write(Ref("p1"), level=17, val=Number(50.0))
    rows = await adapter.point_read_array(Ref("p1"))
    row1 = next(r for r in rows if int(r["level"].val) == 1)
    row17 = next(r for r in rows if int(r["level"].val) == 17)
    assert row1["val"] == Number(100.0)
    assert row17["val"] == Number(50.0)
    # All other levels have no val
    for row in rows:
        lvl = int(row["level"].val)
        if lvl not in (1, 17):
            assert "val" not in row


@pytest.mark.asyncio
async def test_clear_nonexistent_level_is_noop(adapter: InMemoryAdapter) -> None:
    # Clearing a level that was never set should not raise
    await adapter.point_write(Ref("p1"), level=5, val=None)
    rows = await adapter.point_read_array(Ref("p1"))
    row5 = next(r for r in rows if int(r["level"].val) == 5)
    assert "val" not in row5


@pytest.mark.asyncio
async def test_point_write_who_and_duration_ignored(adapter: InMemoryAdapter) -> None:
    # who and duration are accepted without error
    await adapter.point_write(Ref("p1"), level=8, val=Number(72.0), who="operator", duration=60)
    rows = await adapter.point_read_array(Ref("p1"))
    row8 = next(r for r in rows if int(r["level"].val) == 8)
    assert row8["val"] == Number(72.0)


# ---------------------------------------------------------------------------
# TestWatchOps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_sub_creates(adapter: InMemoryAdapter) -> None:
    watch_id, entities = await adapter.watch_sub(None, [Ref("s1")], dis="My Watch")
    assert watch_id is not None
    assert watch_id.startswith("w-")
    assert len(entities) == 1
    assert entities[0]["id"] == Ref("s1")


@pytest.mark.asyncio
async def test_watch_sub_returns_entities(adapter: InMemoryAdapter) -> None:
    _watch_id, entities = await adapter.watch_sub(None, [Ref("s1"), Ref("e1")])
    assert len(entities) == 2
    ids = {e["id"].val for e in entities}
    assert ids == {"s1", "e1"}


@pytest.mark.asyncio
async def test_watch_poll_empty(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1")])
    result = await adapter.watch_poll(watch_id)
    assert result == []


@pytest.mark.asyncio
async def test_watch_poll_dirty(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1"), Ref("s2")])
    adapter.mark_dirty("s1")
    result = await adapter.watch_poll(watch_id)
    assert len(result) == 1
    assert result[0]["id"] == Ref("s1")


@pytest.mark.asyncio
async def test_watch_poll_clears_dirty(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1")])
    adapter.mark_dirty("s1")
    # First poll returns the dirty entity
    result1 = await adapter.watch_poll(watch_id)
    assert len(result1) == 1
    # Second poll returns nothing (dirty cleared)
    result2 = await adapter.watch_poll(watch_id)
    assert result2 == []


@pytest.mark.asyncio
async def test_watch_poll_refresh(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1"), Ref("e1")])
    # No dirty entities, but refresh=True should return all watched entities
    result = await adapter.watch_poll(watch_id, refresh=True)
    ids = {e["id"].val for e in result}
    assert ids == {"s1", "e1"}


@pytest.mark.asyncio
async def test_watch_unsub_ids(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1"), Ref("s2")])
    await adapter.watch_unsub(watch_id, [Ref("s2")])
    # After unsubbing s2, refresh should only return s1
    result = await adapter.watch_poll(watch_id, refresh=True)
    ids = {e["id"].val for e in result}
    assert ids == {"s1"}
    assert "s2" not in ids


@pytest.mark.asyncio
async def test_watch_unsub_close(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1")])
    await adapter.watch_unsub(watch_id, [], close=True)
    # After closing, poll returns [] (watch no longer exists)
    result = await adapter.watch_poll(watch_id)
    assert result == []


@pytest.mark.asyncio
async def test_watch_sub_extend_existing(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1")])
    watch_id2, entities = await adapter.watch_sub(watch_id, [Ref("e1")])
    # Same watch ID returned
    assert watch_id2 == watch_id
    # All subscribed entities returned
    ids = {e["id"].val for e in entities}
    assert "s1" in ids
    assert "e1" in ids


@pytest.mark.asyncio
async def test_watch_sub_unknown_id_creates_new(adapter: InMemoryAdapter) -> None:
    # Providing a watch_id that doesn't exist should create a new watch
    watch_id, _ = await adapter.watch_sub("nonexistent-watch", [Ref("s1")])
    assert watch_id != "nonexistent-watch"
    assert watch_id.startswith("w-")


@pytest.mark.asyncio
async def test_watch_unsub_unknown_watch_is_noop(adapter: InMemoryAdapter) -> None:
    # Unsubscribing from a watch that doesn't exist should not raise
    await adapter.watch_unsub("nonexistent", [Ref("s1")])


@pytest.mark.asyncio
async def test_watch_poll_unknown_watch(adapter: InMemoryAdapter) -> None:
    result = await adapter.watch_poll("nonexistent")
    assert result == []


@pytest.mark.asyncio
async def test_mark_dirty_not_in_watch(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1")])
    # Marking an entity that is NOT in the watch does nothing
    adapter.mark_dirty("s2")
    result = await adapter.watch_poll(watch_id)
    assert result == []


@pytest.mark.asyncio
async def test_mark_dirty_multiple_watches(adapter: InMemoryAdapter) -> None:
    watch_id_a, _ = await adapter.watch_sub(None, [Ref("s1")])
    watch_id_b, _ = await adapter.watch_sub(None, [Ref("s1"), Ref("e1")])
    adapter.mark_dirty("s1")
    result_a = await adapter.watch_poll(watch_id_a)
    result_b = await adapter.watch_poll(watch_id_b)
    assert len(result_a) == 1
    assert result_a[0]["id"] == Ref("s1")
    assert len(result_b) == 1
    assert result_b[0]["id"] == Ref("s1")


@pytest.mark.asyncio
async def test_watch_unsub_also_clears_dirty(adapter: InMemoryAdapter) -> None:
    watch_id, _ = await adapter.watch_sub(None, [Ref("s1"), Ref("s2")])
    adapter.mark_dirty("s2")
    await adapter.watch_unsub(watch_id, [Ref("s2")])
    result = await adapter.watch_poll(watch_id, refresh=False)
    # s2 was marked dirty then unsubbed; should not appear in poll
    ids = {e["id"].val for e in result}
    assert "s2" not in ids


# ---------------------------------------------------------------------------
# TestLifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_close() -> None:
    adapter = InMemoryAdapter()
    await adapter.start()
    await adapter.close()
    # No errors raised; adapter still usable
    results = await adapter.read_by_filter(parse("site"))
    assert results == []


@pytest.mark.asyncio
async def test_close_then_start_again() -> None:
    adapter = InMemoryAdapter()
    await adapter.start()
    await adapter.close()
    await adapter.start()
    # Should still work after re-start
    await adapter.close()


# ---------------------------------------------------------------------------
# TestLoadEntities
# ---------------------------------------------------------------------------


def test_load_entities() -> None:
    adapter = InMemoryAdapter()
    count = adapter.load_entities(
        [
            {"id": Ref("a"), "dis": "A"},
            {"id": Ref("b"), "dis": "B"},
        ]
    )
    assert count == 2


def test_load_skips_no_id() -> None:
    adapter = InMemoryAdapter()
    count = adapter.load_entities(
        [
            {"id": Ref("a"), "dis": "A"},
            {"dis": "No ID here"},
            {"id": "not-a-ref", "dis": "String ID"},
        ]
    )
    # Only entities with a proper Ref id are stored
    assert count == 1


def test_load_entities_overwrites_duplicate_id() -> None:
    adapter = InMemoryAdapter()
    adapter.load_entities([{"id": Ref("a"), "dis": "First"}])
    adapter.load_entities([{"id": Ref("a"), "dis": "Second"}])
    # Most recent load wins
    entity = adapter._entities["a"]
    assert entity["dis"] == "Second"


def test_constructor_loads_entities() -> None:
    adapter = InMemoryAdapter(
        [
            {"id": Ref("x"), "dis": "X", "site": MARKER},
        ]
    )
    assert "x" in adapter._entities


def test_constructor_no_entities() -> None:
    adapter = InMemoryAdapter()
    assert adapter._entities == {}


@pytest.mark.asyncio
async def test_load_entities_then_filter() -> None:
    adapter = InMemoryAdapter()
    adapter.load_entities(
        [
            {"id": Ref("s1"), "dis": "Site 1", "site": MARKER},
            {"id": Ref("e1"), "dis": "Equip 1", "equip": MARKER},
        ]
    )
    results = await adapter.read_by_filter(parse("site"))
    assert len(results) == 1
    assert results[0]["id"] == Ref("s1")


# ---------------------------------------------------------------------------
# TestProtocolCompliance
# ---------------------------------------------------------------------------


def test_implements_storage_adapter_protocol() -> None:
    from hs_py.storage.protocol import StorageAdapter

    adapter = InMemoryAdapter()
    assert isinstance(adapter, StorageAdapter)
