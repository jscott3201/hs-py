Data Types and Grids
====================

Haystack defines a set of scalar value types and a universal tabular message
format called the :class:`~hs_py.grid.Grid`.  This guide covers both.

.. seealso::

   :doc:`../api/types` for the full type API reference,
   :doc:`../api/core` for the Grid and GridBuilder API reference.

.. _guide-scalar-types:

Scalar Types
------------

All Haystack scalars are frozen dataclasses (immutable, hashable, sortable
where applicable).  Use them directly when building grids or encoding data.

.. list-table:: Haystack Scalar Types
   :header-rows: 1
   :widths: 20 30 50

   * - Type
     - Python Representation
     - Example
   * - Marker
     - :class:`~hs_py.kinds.Marker` / ``MARKER``
     - ``MARKER``
   * - NA
     - :class:`~hs_py.kinds.Na` / ``NA``
     - ``NA``
   * - Remove
     - :class:`~hs_py.kinds.Remove` / ``REMOVE``
     - ``REMOVE``
   * - Bool
     - ``bool``
     - ``True``
   * - Number
     - :class:`~hs_py.kinds.Number`
     - ``Number(72.5, "°F")``
   * - Str
     - ``str``
     - ``"hello"``
   * - Ref
     - :class:`~hs_py.kinds.Ref`
     - ``Ref("p:demo:r:1", "Sensor 1")``
   * - Symbol
     - :class:`~hs_py.kinds.Symbol`
     - ``Symbol("site")``
   * - Uri
     - :class:`~hs_py.kinds.Uri`
     - ``Uri("/api/about")``
   * - Coord
     - :class:`~hs_py.kinds.Coord`
     - ``Coord(37.55, -77.45)``
   * - XStr
     - :class:`~hs_py.kinds.XStr`
     - ``XStr("Hex", "deadbeef")``
   * - Date
     - :class:`~python:datetime.date`
     - ``date(2026, 2, 16)``
   * - Time
     - :class:`~python:datetime.time`
     - ``time(14, 30, 0)``
   * - DateTime
     - :class:`~python:datetime.datetime`
     - ``datetime(2026, 2, 16, 14, 30, tzinfo=...)``
   * - List
     - ``list``
     - ``[Number(1), Number(2)]``
   * - Dict
     - ``dict``
     - ``{"dis": "Hello"}``

Singletons
^^^^^^^^^^

:class:`~hs_py.kinds.Marker`, :class:`~hs_py.kinds.Na`, and
:class:`~hs_py.kinds.Remove` are singleton types — every instance is
identical.  Use the pre-built constants for clarity:

.. code-block:: python

   from hs_py import MARKER, NA, REMOVE

   # These are always true:
   assert MARKER is MARKER
   assert NA is NA
   assert REMOVE is REMOVE

Numbers with Units
^^^^^^^^^^^^^^^^^^

:class:`~hs_py.kinds.Number` carries an optional unit string.  Units are
plain strings — the library does not validate unit symbols, leaving that
to the ontology layer.

.. code-block:: python

   from hs_py import Number

   temp = Number(72.5, "°F")
   power = Number(1500, "W")
   unitless = Number(42)

   print(temp.val)   # 72.5
   print(temp.unit)  # °F

   # Numbers are comparable
   assert Number(1) < Number(2)

   # Unit is part of equality
   assert Number(72, "°F") != Number(72, "°C")

Refs
^^^^

:class:`~hs_py.kinds.Ref` represents a record identifier, optionally carrying
a display string:

.. code-block:: python

   from hs_py import Ref

   r = Ref("p:demo:r:1", "AHU-1")
   print(r.val)  # p:demo:r:1
   print(r.dis)  # AHU-1

   # Refs with same val are equal regardless of dis
   assert Ref("abc") == Ref("abc", "display")

.. _guide-grid:

Grid
----

The :class:`~hs_py.grid.Grid` is the universal message format for all Haystack
operations.  A grid is a table of named columns and typed rows, with optional
metadata on the grid itself and on each column.

Anatomy of a Grid
^^^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py import Grid, Col, Ref, MARKER

   # A grid has:
   #   meta  — dict of grid-level tags
   #   cols  — ordered tuple of Col(name, meta)
   #   rows  — tuple of dicts keyed by column name

   grid = Grid(
       meta={"ver": "3.0"},
       cols=(Col("id", {}), Col("dis", {}), Col("point", {})),
       rows=(
           {"id": Ref("p1"), "dis": "Sensor 1", "point": MARKER},
           {"id": Ref("p2"), "dis": "Sensor 2", "point": MARKER},
       ),
   )

   print(len(grid))          # 2 (number of rows)
   print(grid[0]["dis"])      # Sensor 1
   print(grid.col("id"))      # Col(name='id', meta={})

Building Grids
^^^^^^^^^^^^^^

Use :class:`~hs_py.grid.GridBuilder` for fluent construction:

.. code-block:: python

   from hs_py import GridBuilder, Ref, Number, MARKER

   b = GridBuilder()
   b.set_meta({"ver": "3.0"})
   b.add_meta("projName", "Demo")  # Add a single key to meta
   b.add_col("id")
   b.add_col("dis")
   b.add_col("point")
   b.add_col("curVal")
   b.add_row({"id": Ref("p1"), "dis": "Sensor 1", "point": MARKER, "curVal": Number(72.5, "°F")})
   b.add_row({"id": Ref("p2"), "dis": "Sensor 2", "point": MARKER, "curVal": Number(68.0, "°F")})
   grid = b.to_grid()

Factory Methods
^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py import Grid

   # Empty grid (no columns, no rows)
   empty = Grid.make_empty()

   # Error grid wrapping an exception message
   err = Grid.make_error("something went wrong")
   assert err.is_error

   # Grid from row dicts (columns inferred automatically)
   grid = Grid.make_rows([
       {"id": Ref("p1"), "dis": "Sensor 1", "point": MARKER},
       {"id": Ref("p2"), "dis": "Sensor 2", "point": MARKER},
   ])

Iterating Rows
^^^^^^^^^^^^^^

Grids support standard Python iteration:

.. code-block:: python

   for row in grid:
       print(row["id"], row.get("curVal"))

   # Index to get a specific row
   first_row = grid[0]
   last_row = grid[-1]

Column Lookup
^^^^^^^^^^^^^

Column lookup is O(1) via an internal column map:

.. code-block:: python

   col = grid.col("id")       # Returns Col or raises KeyError
   has_it = grid.has_col("id")  # Returns bool
   names = grid.col_names       # Tuple of column name strings

Grid Properties
^^^^^^^^^^^^^^^

.. code-block:: python

   grid.is_empty   # True if the grid has no rows
   grid.is_error   # True if the grid meta has an "err" marker
   grid.col_names  # Tuple of column names, e.g. ("id", "dis")
