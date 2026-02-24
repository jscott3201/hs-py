Watch and Subscriptions
=======================

The watch module provides tools for real-time data subscriptions.
:class:`~hs_py.watch.WatchState` tracks server-side entity state and computes
deltas, while :class:`~hs_py.watch.WatchAccumulator` merges deltas on the
client side.

.. seealso::

   :doc:`../api/types` for the full watch API reference.

.. _guide-watch-concepts:

Concepts
--------

In the Haystack protocol, a **watch** is a subscription to a set of entity
records.  The server tracks the current state and notifies clients when tags
change.  Instead of sending the full record every time, the server computes
a **delta** — only the tags that have changed, been added, or been removed.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Term
     - Description
   * - **Watch**
     - A named subscription to a set of entity records
   * - **Delta**
     - A grid containing only changed tags since the last poll
   * - **REMOVE**
     - Sentinel value indicating a tag was removed
   * - **_removed**
     - Metadata flag indicating an entity was removed from the watch

.. _guide-watch-server:

Server-Side: WatchState
-----------------------

:class:`~hs_py.watch.WatchState` maintains the last-known state of watched
entities and computes deltas:

.. code-block:: python

   from hs_py.watch import WatchState
   from hs_py import Grid, Col
   from hs_py.kinds import Ref, Number, MARKER, REMOVE

   state = WatchState("w1")

   # First update — everything is new, so the delta is the full record
   current = Grid(
       cols=(Col("id", {}), Col("curVal", {}), Col("point", {})),
       rows=(
           {"id": Ref("p1"), "curVal": Number(72.5, "°F"), "point": MARKER},
           {"id": Ref("p2"), "curVal": Number(68.0, "°F"), "point": MARKER},
       ),
   )
   delta = state.compute_delta(current)
   # delta contains both records in full (first time seen)

   # Second update — only changes are emitted
   current = Grid(
       cols=(Col("id", {}), Col("curVal", {}), Col("point", {})),
       rows=(
           {"id": Ref("p1"), "curVal": Number(73.0, "°F"), "point": MARKER},
           {"id": Ref("p2"), "curVal": Number(68.0, "°F"), "point": MARKER},
       ),
   )
   delta = state.compute_delta(current)
   # delta contains only p1 with updated curVal
   # p2 is omitted (no changes)

Tag Removal
^^^^^^^^^^^

When a tag is removed from an entity, the delta includes a ``REMOVE`` marker:

.. code-block:: python

   # Previously: {"id": Ref("p1"), "curVal": Number(73), "alarm": MARKER}
   # Now:        {"id": Ref("p1"), "curVal": Number(73)}
   # Delta:      {"id": Ref("p1"), "alarm": REMOVE}

Entity Removal
^^^^^^^^^^^^^^

When an entity is no longer in the current set, it appears in the delta
with a ``_removed`` flag:

.. code-block:: python

   # Entity p2 dropped from the watch
   current = Grid(
       cols=(Col("id", {}), Col("curVal", {})),
       rows=({"id": Ref("p1"), "curVal": Number(73.0, "°F")},),
   )
   delta = state.compute_delta(current)
   # delta includes: {"id": Ref("p2"), "_removed": MARKER}

Server-Side Filtering
^^^^^^^^^^^^^^^^^^^^^

Configure a watch with a filter expression, then apply it to the delta before
sending to the client:

.. code-block:: python

   from hs_py.watch import WatchState
   from hs_py.filter import parse

   state = WatchState("w1", filter_ast=parse("curVal > 70"))

   delta = state.compute_delta(current)
   filtered = state.apply_filter(delta)
   # Only includes rows where curVal > 70

Updating the Cache
^^^^^^^^^^^^^^^^^^

After computing and sending a delta, call :meth:`~hs_py.watch.WatchState.update`
to sync the internal cache with the current state.  This ensures the next
``compute_delta`` call produces correct diffs:

.. code-block:: python

   delta = state.compute_delta(current)
   # ... send delta to client ...
   state.update(current)  # Keep cache in sync

.. _guide-watch-client:

Client-Side: WatchAccumulator
-----------------------------

:class:`~hs_py.watch.WatchAccumulator` merges incoming delta grids into a
complete entity state on the client side:

.. code-block:: python

   from hs_py.watch import WatchAccumulator
   from hs_py import Grid, Col
   from hs_py.kinds import Ref, Number, MARKER, REMOVE

   acc = WatchAccumulator()

   # Apply the first delta (initial state)
   delta = Grid(
       cols=(Col("id", {}), Col("curVal", {}), Col("point", {})),
       rows=(
           {"id": Ref("p1"), "curVal": Number(72.5, "°F"), "point": MARKER},
           {"id": Ref("p2"), "curVal": Number(68.0, "°F"), "point": MARKER},
       ),
   )
   acc.apply_delta(delta)

   # Current state
   print(acc.get("p1"))
   # {"id": Ref("p1"), "curVal": Number(72.5, "°F"), "point": MARKER}

   # Apply an update delta
   update = Grid(
       cols=(Col("id", {}), Col("curVal", {})),
       rows=({"id": Ref("p1"), "curVal": Number(73.0, "°F")},),
   )
   acc.apply_delta(update)

   # State is merged — unchanged tags preserved
   print(acc.get("p1"))
   # {"id": Ref("p1"), "curVal": Number(73.0, "°F"), "point": MARKER}

   # Apply a removal delta
   removal = Grid(
       cols=(Col("id", {}), Col("alarm", {})),
       rows=({"id": Ref("p1"), "alarm": REMOVE},),
   )
   acc.apply_delta(removal)
   # "alarm" tag is removed from p1's state

   # Entity removal
   removed = Grid(
       cols=(Col("id", {}), Col("_removed", {})),
       rows=({"id": Ref("p2"), "_removed": MARKER},),
   )
   acc.apply_delta(removed)
   assert acc.get("p2") is None

Accessing Accumulated State
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Beyond :meth:`~hs_py.watch.WatchAccumulator.get` for individual lookups,
the accumulator provides access to all entities and can export the full
state as a grid:

.. code-block:: python

   # All entity IDs and their current tag dicts
   for entity_id, tags in acc.entities.items():
       print(f"{entity_id}: {tags.get('dis')}")

   # Export the accumulated state as a Grid
   grid = acc.to_grid()
   print(f"Tracking {len(grid)} entities")

.. _guide-watch-http:

HTTP Watch Workflow
-------------------

Complete watch lifecycle using the HTTP client:

.. code-block:: python

   from hs_py import Client

   async with Client("http://host/api", "user", "pass") as c:
       # 1. Subscribe to entities (raw=True to access grid metadata)
       watch = await c.watch_sub(
           [Ref("p:demo:r:1"), Ref("p:demo:r:2")],
           watch_dis="My Watch",
           raw=True,
       )
       watch_id = watch.meta["watchId"]

       # 2. Poll for changes
       changes = await c.watch_poll(watch_id)
       for row in changes:
           print(f"{row['id']}: {row.get('curVal')}")

       # 3. Remove entities
       await c.watch_unsub(watch_id, [Ref("p:demo:r:2")])

       # 4. Close the watch
       await c.watch_close(watch_id)

.. _guide-watch-ws:

WebSocket Watch Workflow
------------------------

With WebSocket, watch updates can be polled or handled via application logic:

.. code-block:: python

   from hs_py import Grid
   from hs_py.ws_client import WebSocketClient
   from hs_py.watch import WatchAccumulator

   acc = WatchAccumulator()

   async with WebSocketClient("ws://host/api/ws", auth_token="token") as ws:
       watch = await ws.watch_sub(
           [Ref("p1"), Ref("p2")], watch_dis="WS Watch", raw=True,
       )
       watch_id = watch.meta["watchId"]

       # Poll for changes
       delta = await ws.watch_poll(watch_id)
       acc.apply_delta(delta)
       print(f"State updated: {acc.get('p1')}")
