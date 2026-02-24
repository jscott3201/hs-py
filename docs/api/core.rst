Core
====

Grid, errors, metrics, and the HTTP client — the foundational building blocks
of the hs-py library.

Grid
----

The universal Haystack message format. :class:`~hs_py.grid.Grid` holds
columns, rows, and metadata. :class:`~hs_py.grid.GridBuilder` provides a
fluent API for constructing grids programmatically.

.. automodule:: hs_py.grid
   :members:

Client
------

Async HTTP client implementing all standard Project Haystack operations.
Handles SCRAM authentication, automatic 401 retry, and connection management.

.. automodule:: hs_py.client
   :members:

Errors
------

Exception hierarchy for the hs-py library.

.. automodule:: hs_py.errors
   :members:

Metrics
-------

Observability hooks for monitoring client and server activity.

.. automodule:: hs_py.metrics
   :members:
