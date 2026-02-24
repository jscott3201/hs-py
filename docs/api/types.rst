Types
=====

Haystack value types and the server operations layer.

Kinds
-----

Haystack value types as frozen dataclasses. Includes :class:`~hs_py.kinds.Marker`,
:class:`~hs_py.kinds.Number`, :class:`~hs_py.kinds.Ref`,
:class:`~hs_py.kinds.Coord`, :class:`~hs_py.kinds.Symbol`,
:class:`~hs_py.kinds.Uri`, :class:`~hs_py.kinds.XStr`,
:class:`~hs_py.kinds.Na`, and :class:`~hs_py.kinds.Remove`
with singleton instances ``MARKER``, ``NA``, and ``REMOVE``.

.. automodule:: hs_py.kinds
   :members:

Watch
-----

Watch state tracking and delta encoding for subscription-based data updates.

.. automodule:: hs_py.watch
   :members:

Ops
---

Server-side Haystack operation dispatch layer.

.. automodule:: hs_py.ops
   :members:
