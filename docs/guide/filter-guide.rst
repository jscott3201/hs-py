Filter Expressions
==================

The :mod:`hs_py.filter` module parses and evaluates Haystack filter strings —
the query language used to search for records by their tags.

.. seealso::

   :doc:`../api/filter` for the full filter API reference (AST nodes, lexer,
   parser, evaluator).

.. _guide-filter-syntax:

Filter Syntax
-------------

Haystack filters support tag presence checks, comparisons, boolean logic,
and dotted path traversal:

.. list-table:: Filter Syntax
   :header-rows: 1
   :widths: 35 65

   * - Expression
     - Meaning
   * - ``site``
     - Records that have the ``site`` marker tag
   * - ``not site``
     - Records that do not have the ``site`` marker tag
   * - ``curVal == 72``
     - Records where ``curVal`` equals 72
   * - ``curVal > 72``
     - Records where ``curVal`` is greater than 72
   * - ``curVal >= 72``
     - Greater than or equal
   * - ``curVal < 72``
     - Less than
   * - ``curVal <= 72``
     - Less than or equal
   * - ``curVal != 72``
     - Not equal
   * - ``point and sensor``
     - Logical AND
   * - ``site or equip``
     - Logical OR
   * - ``point and not hidden``
     - Combined logic
   * - ``equipRef->dis``
     - Path traversal through a Ref
   * - ``equipRef->siteRef->dis == "HQ"``
     - Multi-hop path traversal

Operator precedence: ``not`` > ``and`` > ``or``.  Use parentheses to override.

.. _guide-filter-parsing:

Parsing
-------

:func:`~hs_py.filter.parser.parse` converts a filter string into an AST.
Results are cached (LRU, 256 entries) for repeated filter expressions.
Invalid filter strings raise :class:`~hs_py.filter.parser.ParseError`:

.. code-block:: python

   from hs_py import parse
   from hs_py.filter.parser import ParseError

   ast = parse("point and sensor and curVal > 72")

   # Invalid filters raise ParseError
   try:
       parse("and or not ===")
   except ParseError as e:
       print(f"Bad filter: {e}")

   # Successful parse:
   print(ast)
   # And(And(Has(Path('point')), Has(Path('sensor'))),
   #     Cmp(Path('curVal'), CmpOp.GT, 72))

AST Node Types
^^^^^^^^^^^^^^

The :mod:`hs_py.filter.ast` module defines the AST:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Node
     - Description
   * - :class:`~hs_py.filter.ast.Has`
     - Tag exists (marker check)
   * - :class:`~hs_py.filter.ast.Missing`
     - Tag does not exist
   * - :class:`~hs_py.filter.ast.Cmp`
     - Comparison (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``)
   * - :class:`~hs_py.filter.ast.And`
     - Logical AND
   * - :class:`~hs_py.filter.ast.Or`
     - Logical OR
   * - :class:`~hs_py.filter.ast.Path`
     - Dotted path (e.g. ``equipRef->siteRef->dis``)

.. _guide-filter-eval:

Evaluating Against Dicts
------------------------

:func:`~hs_py.filter.eval.evaluate` tests a single dict against a parsed
filter:

.. code-block:: python

   from hs_py import parse, evaluate, MARKER, Number

   f = parse("point and sensor and curVal > 72")

   rec1 = {"point": MARKER, "sensor": MARKER, "curVal": Number(75)}
   rec2 = {"point": MARKER, "sensor": MARKER, "curVal": Number(68)}
   rec3 = {"point": MARKER, "equip": MARKER}

   assert evaluate(f, rec1) is True
   assert evaluate(f, rec2) is False
   assert evaluate(f, rec3) is False

.. _guide-filter-grid:

Filtering Grids
---------------

:func:`~hs_py.filter.eval.evaluate_grid` returns a new grid containing only
the matching rows:

.. code-block:: python

   from hs_py import parse, evaluate_grid

   filtered = evaluate_grid(parse("point and curVal > 70"), grid)
   print(f"Matched {len(filtered)} of {len(grid)} rows")

.. _guide-filter-paths:

Path Traversal
--------------

Haystack filters support ``->`` path traversal for following
:class:`~hs_py.kinds.Ref`-valued tags to related records.  For example,
``equipRef->dis`` means "follow the ``equipRef`` tag to the referenced record,
then read its ``dis`` tag."

When evaluating against a grid, the evaluator automatically builds a resolver
from the grid's ``id`` column:

.. code-block:: python

   from hs_py import Grid, Col, Ref, MARKER, parse, evaluate_grid

   grid = Grid(
       cols=(Col("id", {}), Col("dis", {}), Col("site", {}),
             Col("equipRef", {}), Col("point", {})),
       rows=(
           {"id": Ref("s1"), "dis": "HQ", "site": MARKER},
           {"id": Ref("e1"), "dis": "AHU-1", "equipRef": Ref("s1")},
           {"id": Ref("p1"), "dis": "Temp", "point": MARKER, "equipRef": Ref("e1")},
       ),
   )

   # Find points whose equip's dis is "AHU-1"
   result = evaluate_grid(parse('equipRef->dis == "AHU-1"'), grid)

When evaluating against a plain dict, provide a custom resolver function:

.. code-block:: python

   from hs_py import parse, evaluate, Ref

   db = {
       "s1": {"id": Ref("s1"), "dis": "HQ"},
       "e1": {"id": Ref("e1"), "dis": "AHU-1", "siteRef": Ref("s1")},
   }

   def resolver(ref_val: str) -> dict | None:
       return db.get(ref_val)

   f = parse('siteRef->dis == "HQ"')
   assert evaluate(f, db["e1"], resolver=resolver)

.. _guide-filter-literals:

Supported Literals
------------------

Filters support these literal types in comparisons:

.. code-block:: text

   Strings:     "hello"
   Numbers:     72.5  or  72.5°F  or  -3.14
   Booleans:    true  false
   Refs:        @p:demo:r:1
   URIs:        `http://example.com`
   Dates:       2026-02-16
   Times:       14:30:00
   DateTimes:   2026-02-16T14:30:00-05:00 New_York
   Symbols:     ^site

.. _guide-filter-caching:

Performance
-----------

The parser caches up to 256 parsed ASTs.  If your application uses a fixed
set of filters, they will be parsed only once:

.. code-block:: python

   # These two calls return the same cached AST:
   f1 = parse("point and sensor")
   f2 = parse("point and sensor")
   assert f1 is f2
