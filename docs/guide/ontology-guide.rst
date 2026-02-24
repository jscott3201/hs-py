Ontology
========

The ontology module implements the Project Haystack definition model —
a structured type system for describing building equipment, sensors, and
their relationships.  It supports parsing ontology definitions from Trio
files, compiling them into a resolved namespace, querying the subtype
hierarchy, and reflecting entity dicts against the ontology.

.. seealso::

   :doc:`../api/ontology` for the full ontology API reference (defs, namespace,
   taxonomy, normalization, reflection).

.. _guide-ontology-concepts:

Concepts
--------

The Haystack ontology is built on a few core concepts:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Concept
     - Description
   * - **Def**
     - A named definition (term) in the ontology, carrying metadata tags.
       Examples: ``site``, ``equip``, ``point``, ``sensor``, ``°F``.
   * - **Lib**
     - A versioned package grouping related defs.
       Examples: ``lib:ph`` (core), ``lib:phIoT`` (IoT extensions).
   * - **Namespace**
     - A container that indexes all defs from one or more libs, providing
       symbol resolution and taxonomy queries.
   * - **Taxonomy**
     - The ``is`` tag hierarchy defining subtype relationships.
       ``sensor`` is a subtype of ``point``, which is a subtype of ``entity``.
   * - **Conjunct**
     - A compound term like ``hot-water`` composed from dash-separated parts.
   * - **Reflection**
     - Determining which defs apply to an entity based on its marker tags.

.. _guide-ontology-defs:

Defs and Libs
-------------

A :class:`~hs_py.ontology.defs.Def` represents a single ontology term:

.. code-block:: python

   from hs_py.ontology.defs import Def
   from hs_py.kinds import Symbol, MARKER

   site_def = Def(
       symbol=Symbol("site"),
       tags={
           "def": Symbol("site"),
           "is": Symbol("entity"),
           "doc": "A geographic site such as a campus or building",
           "marker": MARKER,
       },
   )

   print(site_def.name)        # "site"
   print(site_def.doc)         # "A geographic site..."
   print(site_def.is_list)     # [Symbol("entity")]

A :class:`~hs_py.ontology.defs.Lib` groups defs into a distributable package:

.. code-block:: python

   from hs_py.ontology.defs import Lib
   from hs_py.kinds import Symbol

   lib = Lib(
       symbol=Symbol("lib:ph"),
       version="4.0",
       defs=(site_def, equip_def, point_def),
   )

.. _guide-ontology-trio:

Loading from Trio Files
-----------------------

Ontology definitions are typically distributed as Trio text files.  Use
the loading helpers to parse them:

.. code-block:: python

   from hs_py.ontology.namespace import load_defs_from_trio, load_lib_from_trio

   # Parse individual defs from Trio text
   defs = load_defs_from_trio("""
   def: ^site
   is: ^entity
   doc: "A geographic site"

   def: ^equip
   is: ^entity
   doc: "A physical equipment asset"

   def: ^point
   is: ^entity
   doc: "A data point"
   """)

   # Load a complete lib (lib metadata + defs)
   lib = load_lib_from_trio(
       lib_trio='def: ^lib:myLib\nversion: "1.0"',
       def_trios=[open("defs.trio").read()],
   )

See :doc:`encoding-guide` for Trio format details.

.. _guide-ontology-namespace:

Namespace
---------

The :class:`~hs_py.ontology.namespace.Namespace` indexes defs from one or
more libs, providing fast lookup and taxonomy queries:

.. code-block:: python

   from hs_py.ontology.namespace import Namespace

   ns = Namespace(libs=[lib_ph, lib_phIoT])

   # Lookup by name
   site = ns.get("site")
   assert site is not None
   print(site.doc)

   # Check existence
   assert ns.has("point")
   assert not ns.has("nonexistent")

   # Count
   print(f"{ns.def_count} defs loaded")

Qualified vs Unqualified Names
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Defs can be looked up by both qualified (``ph::site``) and unqualified
(``site``) names.  When multiple libs define the same unqualified name,
the first registered lib wins:

.. code-block:: python

   site_q = ns.get("ph::site")   # Qualified lookup
   site_u = ns.get("site")       # Unqualified lookup
   assert site_q is site_u       # Same Def object

Iterating
^^^^^^^^^

.. code-block:: python

   # All unique defs
   for d in ns.all_defs():
       print(d.symbol.val, d.doc)

   # All libs
   for lib in ns.all_libs():
       print(lib.symbol.val, lib.version)

.. _guide-ontology-taxonomy:

Taxonomy
--------

The taxonomy is the ``is`` tag hierarchy.  Every def declares its supertypes
via the ``is`` tag, forming a tree:

.. code-block:: text

   entity
   ├── site
   ├── equip
   └── point
       ├── sensor
       ├── cmd
       ├── sp
       └── weather

Querying the Hierarchy
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # Direct subtypes
   point_subs = ns.subtypes("point")
   for d in point_subs:
       print(d.name)  # sensor, cmd, sp, weather, ...

   # Direct supertypes
   parents = ns.supertypes("sensor")
   for d in parents:
       print(d.name)  # point

   # All transitive supertypes (cached)
   all_parents = ns.all_supertypes("sensor")
   for d in all_parents:
       print(d.name)  # point, entity, ...

   # Transitive subtype check
   assert ns.is_subtype("sensor", "point")
   assert ns.is_subtype("sensor", "entity")
   assert not ns.is_subtype("site", "point")

   # Identity — a def is a subtype of itself
   assert ns.is_subtype("sensor", "sensor")

Conjuncts
^^^^^^^^^

Conjuncts are compound terms like ``hot-water`` or ``chilled-water-plant``.
They are composed from dash-separated parts, each of which must be a valid
def:

.. code-block:: python

   from hs_py.ontology.taxonomy import is_conjunct, resolve_conjunct_parts

   assert is_conjunct("hot-water")
   assert not is_conjunct("sensor")

   parts = resolve_conjunct_parts("hot-water-plant")
   # ["hot", "water", "plant"]

Effective Tags
^^^^^^^^^^^^^^

Compute the full tag set for a def, inheriting from all supertypes via
:func:`~hs_py.ontology.taxonomy.effective_tags`:

.. code-block:: python

   from hs_py.ontology.taxonomy import effective_tags

   tags = effective_tags(ns, "sensor")
   # Includes tags from sensor, point, entity, and all other ancestors
   # Own tags take precedence over inherited tags

Marker Tags
^^^^^^^^^^^

Get the set of all marker tag names for a def and its supertypes via
:func:`~hs_py.ontology.taxonomy.marker_tags`:

.. code-block:: python

   from hs_py.ontology.taxonomy import marker_tags

   markers = marker_tags(ns, "sensor")
   # {"sensor", "point", "entity", ...}

Tag-On Mapping
^^^^^^^^^^^^^^

Find which entity defs a tag is declared ``tagOn`` via
:func:`~hs_py.ontology.taxonomy.tag_on_defs`:

.. code-block:: python

   from hs_py.ontology.taxonomy import tag_on_defs

   entities = tag_on_defs(ns, "curVal")
   # ["point"] — curVal is tagOn point

.. _guide-ontology-normalize:

Normalization
-------------

The normalization pipeline :func:`~hs_py.ontology.normalize.compile_namespace`
compiles raw libs into a fully resolved namespace.  It handles conjunct
supertype generation, validation of missing references, and cycle detection.

.. code-block:: python

   from hs_py.ontology.normalize import compile_namespace, NormalizeError

   ns = compile_namespace([lib_ph, lib_phIoT])
   # Returns a fully validated Namespace

   # If there are errors, NormalizeError is raised:
   try:
       ns = compile_namespace([broken_lib])
   except NormalizeError as e:
       print(f"Normalization failed: {e}")
       # Causes include: missing supertypes, cycles in the is-hierarchy

The pipeline steps are:

1. **Collect** — gather all defs across all libs.
2. **Taxonify** — for conjuncts like ``hot-water``, add individual parts
   (``hot``, ``water``) as supertypes if they exist as defs.
3. **Rebuild** — reconstruct libs with updated defs.
4. **Build namespace** — index all defs by name.
5. **Validate** — check for missing supertypes and cycles.

.. _guide-ontology-reflect:

Reflection
----------

Reflection determines which defs apply to an entity based on its marker tags.
This is how you answer "what kind of thing is this record?"

.. code-block:: python

   from hs_py.ontology.reflect import reflect, fits
   from hs_py.kinds import MARKER, Number, Ref

   entity = {
       "id": Ref("p1"),
       "dis": "Zone Temp",
       "point": MARKER,
       "sensor": MARKER,
       "temp": MARKER,
       "zone": MARKER,
       "curVal": Number(72.5, "°F"),
   }

   # Get all applicable defs (most-specific first)
   defs = reflect(ns, entity)
   for d in defs:
       print(d.name)
   # sensor, temp, zone, point, entity, ...
   # (includes conjuncts and all transitive supertypes)

   # Check if an entity fits a specific def
   assert fits(ns, entity, "sensor")
   assert fits(ns, entity, "point")
   assert fits(ns, entity, "entity")
   assert not fits(ns, entity, "equip")

Reflection Algorithm
^^^^^^^^^^^^^^^^^^^^

The :func:`~hs_py.ontology.reflect.reflect` algorithm:

1. **Scan** — find all marker-valued tags in the entity dict.
2. **Match** — look up each marker name as a def in the namespace.
3. **Conjuncts** — check for conjunct defs whose parts are all present
   (e.g., if both ``hot`` and ``water`` markers exist, ``hot-water`` matches).
4. **Supertypes** — collect all transitive supertypes of matched defs.

The result is ordered most-specific first: direct marker matches appear before
their ancestors in the hierarchy.
