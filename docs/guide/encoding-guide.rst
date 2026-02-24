Wire Formats
=============

haystack-py supports all four Haystack wire formats.  The HTTP client and server
use JSON by default, but you can use any format directly for file I/O,
message serialization, or interoperability with other systems.

.. seealso::

   :doc:`../api/encoding` for the full encoding API reference.

.. list-table:: Format Comparison
   :header-rows: 1
   :widths: 15 15 15 15 40

   * - Format
     - Encode
     - Decode
     - Lossless
     - Use Case
   * - JSON
     - Yes
     - Yes
     - Yes
     - HTTP API, WebSocket, storage
   * - Zinc
     - Yes
     - Yes
     - Yes
     - Compact text, debugging, logs
   * - Trio
     - Yes
     - Yes
     - Yes
     - Ontology defs, config files
   * - CSV
     - Yes
     - No
     - No
     - Spreadsheet export, reporting

.. _guide-encoding-json:

JSON
----

The JSON codec supports both Haystack JSON v3 and v4 via the
:class:`~hs_py.encoding.JsonVersion` enum.  Uses ``orjson`` for fast
serialization.

Encoding Grids
^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py.encoding.json import encode_grid, decode_grid
   from hs_py.encoding import JsonVersion
   from hs_py import Grid, Col, Ref, Number, MARKER

   grid = Grid(
       cols=(Col("id", {}), Col("curVal", {})),
       rows=({"id": Ref("p1"), "curVal": Number(72.5, "°F")},),
   )

   # Encode as JSON v4 bytes
   data = encode_grid(grid, version=JsonVersion.V4)

   # Decode back to a Grid
   decoded = decode_grid(data)

JSON v3 vs v4
^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - v3
     - v4
   * - Type markers
     - ``"_kind": "Number"``
     - ``"_kind": "number"``
   * - Refs
     - ``{"_kind": "Ref", "val": "...", "dis": "..."}``
     - ``"r:p1 Sensor 1"``
   * - Numbers
     - ``{"_kind": "Number", "val": 72.5, "unit": "°F"}``
     - ``"n:72.5 °F"``
   * - Markers
     - ``{"_kind": "Marker"}``
     - ``"m:"``
   * - Compatibility
     - Older servers (SkySpark, etc.)
     - Current spec

Select the version when encoding:

.. code-block:: python

   from hs_py.encoding import JsonVersion

   v3_bytes = encode_grid(grid, version=JsonVersion.V3)
   v4_bytes = encode_grid(grid, version=JsonVersion.V4)

The decoder auto-detects the version.

Pythonic Mode
^^^^^^^^^^^^^

By default, :func:`~hs_py.encoding.json.decode_grid` returns a
:class:`~hs_py.grid.Grid` with Haystack types.  Pass ``pythonic=True`` to
get plain Python types instead:

.. code-block:: python

   grid = decode_grid(data, pythonic=True)
   # Refs become plain strings, Numbers become floats,
   # Markers become True, etc.

Scalar Encoding
^^^^^^^^^^^^^^^

Encode and decode individual Haystack values in JSON format:

.. code-block:: python

   from hs_py.encoding.json import encode_val, decode_val
   from hs_py.encoding import JsonVersion
   from hs_py import Number, Ref, MARKER

   # Encode scalars to JSON-compatible representations
   assert encode_val(MARKER) == "m:"
   assert encode_val(Number(72.5, "°F")) == "n:72.5 °F"
   assert encode_val(Ref("p1", "Sensor")) == "r:p1 Sensor"

   # Decode JSON representations back to Haystack types
   assert decode_val("m:") == MARKER
   assert decode_val("n:72.5 °F") == Number(72.5, "°F")

   # Pythonic mode returns plain Python types
   assert decode_val("m:", pythonic=True) is True
   assert decode_val("n:72.5 °F", pythonic=True) == 72.5

Dict Encoding
^^^^^^^^^^^^^

:func:`~hs_py.encoding.json.encode_grid_dict` returns a Python ``dict``
instead of ``bytes``, useful for embedding grids inside WebSocket JSON
envelopes without a redundant encode/decode round-trip:

.. code-block:: python

   from hs_py.encoding.json import encode_grid_dict

   d = encode_grid_dict(grid, version=JsonVersion.V4)
   # d is a dict, ready to embed in a larger JSON structure

.. _guide-encoding-zinc:

Zinc
----

Zinc is the Haystack text format — a compact, human-readable grid encoding
used for debugging and logging.

Grid Encoding
^^^^^^^^^^^^^

.. code-block:: python

   from hs_py.encoding.zinc import encode_grid, decode_grid
   from hs_py import Grid, Col, Ref, Number, MARKER

   grid = Grid(
       meta={"ver": "3.0"},
       cols=(Col("id", {}), Col("dis", {}), Col("point", {})),
       rows=({"id": Ref("p1"), "dis": "Sensor 1", "point": MARKER},),
   )

   text = encode_grid(grid)
   # ver:"3.0"
   # id,dis,point
   # @p1,"Sensor 1",M

   decoded = decode_grid(text)

Scalar Encoding
^^^^^^^^^^^^^^^

Encode and decode individual Haystack values:

.. code-block:: python

   from hs_py.encoding.zinc import encode_val, decode_val
   from hs_py import Number, Ref, Coord

   assert encode_val(Number(72.5, "°F")) == '72.5°F'
   assert encode_val(Ref("p1", "Sensor")) == '@p1 "Sensor"'
   assert encode_val(Coord(37.55, -77.45)) == 'C(37.55,-77.45)'

   assert decode_val('72.5°F') == Number(72.5, "°F")
   assert decode_val('@p1') == Ref("p1")

.. _guide-encoding-trio:

Trio
----

Trio is the Haystack ontology record format — one record per block of lines,
separated by blank lines.  Used for ontology def files and configuration.
See :doc:`ontology-guide` for working with ontology defs.

Parsing Records
^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py.encoding.trio import parse_trio

   text = """
   def: ^site
   doc: "A geographical site"
   is: ^entity

   def: ^equip
   doc: "A physical equipment asset"
   is: ^entity
   """

   records = parse_trio(text)
   # [
   #   {"def": Symbol("site"), "doc": "A geographical site", "is": Symbol("entity")},
   #   {"def": Symbol("equip"), "doc": "A physical equipment asset", "is": Symbol("entity")},
   # ]

Encoding Records
^^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py.encoding.trio import encode_trio
   from hs_py.kinds import Symbol, MARKER

   records = [
       {"def": Symbol("site"), "doc": "A geographical site", "is": Symbol("entity")},
       {"def": Symbol("equip"), "doc": "Equipment", "is": Symbol("entity")},
   ]

   text = encode_trio(records)

Scalar Parsing
^^^^^^^^^^^^^^

Parse individual Zinc scalar values:

.. code-block:: python

   from hs_py.encoding.trio import parse_zinc_val

   val = parse_zinc_val('"hello"')   # "hello"
   val = parse_zinc_val("72.5°F")    # Number(72.5, "°F")

.. _guide-encoding-csv:

CSV
---

CSV encoding is **lossy** — metadata, column meta, and type information are
discarded.  Use it for spreadsheet export and reporting.

.. code-block:: python

   from hs_py.encoding.csv import encode_grid
   from hs_py import Grid, Col, Ref, Number

   grid = Grid(
       cols=(Col("id", {}), Col("dis", {}), Col("curVal", {})),
       rows=(
           {"id": Ref("p1"), "dis": "Sensor 1", "curVal": Number(72.5, "°F")},
           {"id": Ref("p2"), "dis": "Sensor 2", "curVal": Number(68.0, "°F")},
       ),
   )

   csv_text = encode_grid(grid)
   # id,dis,curVal
   # p1,Sensor 1,72.5°F
   # p2,Sensor 2,68.0°F

.. _guide-encoding-scanner:

Shared Scanner
--------------

The :mod:`hs_py.encoding.scanner` module provides low-level utilities shared
across Zinc, Trio, and the filter lexer.  You rarely use these directly, but
they are available for custom parsing:

- :func:`~hs_py.encoding.scanner.format_num` /
  :func:`~hs_py.encoding.scanner.format_number` — Number to Zinc string
- :func:`~hs_py.encoding.scanner.format_ref` — Ref to Zinc string
- :func:`~hs_py.encoding.scanner.escape_str` — Escape a string for Zinc/Trio
  output
- :func:`~hs_py.encoding.scanner.tz_name` /
  :func:`~hs_py.encoding.scanner.tz_to_city` /
  :func:`~hs_py.encoding.scanner.city_to_tz` — Timezone name conversion
