Authentication, Users & Permissions
====================================

haystack-py includes a complete authentication and authorization system with
user management, SCRAM-SHA-256 credentials, and role-based access control.

.. seealso::

   :doc:`server-guide` for general server setup,
   :doc:`../api/security` for the full API reference.

.. _guide-auth-roles:

Roles
-----

Every user is assigned a :class:`~hs_py.user.Role` that determines what they
can do.  Roles form a strict hierarchy: **Admin > Operator > Viewer**.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Role
     - Capabilities
   * - **Admin**
     - Full access: user management CRUD, all read and write Haystack ops
   * - **Operator**
     - Read + write Haystack ops (hisWrite, pointWrite, invokeAction,
       watchSub/Unsub/Poll) but **no** user management
   * - **Viewer**
     - Read-only Haystack ops (read, nav, hisRead, defs, libs, filetypes)
       and informational GET ops (about, ops, formats)

.. code-block:: python

   from hs_py.user import Role

   Role.ADMIN > Role.OPERATOR   # True
   Role.OPERATOR > Role.VIEWER  # True

.. _guide-auth-users:

User Model
----------

:class:`~hs_py.user.User` is a frozen dataclass that stores SCRAM-SHA-256
credentials — the plaintext password is **never** retained.

.. code-block:: python

   from hs_py.user import Role, create_user

   admin = create_user("admin", "s3cret", role=Role.ADMIN)
   op = create_user("operator", "pass", role=Role.OPERATOR,
                     first_name="Jane", email="jane@example.com")
   viewer = create_user("viewer", "readonly", role=Role.VIEWER)

Fields:

- ``username`` (required) — unique login identifier
- ``password`` (required, used only during creation) — plaintext, discarded after credential derivation
- ``first_name``, ``last_name``, ``email`` — optional profile fields
- ``role`` — :class:`~hs_py.user.Role` enum (default ``VIEWER``)
- ``enabled`` — boolean, disabled users cannot authenticate
- ``credentials`` — :class:`~hs_py.auth_types.ScramCredentials` (derived from password)

.. _guide-auth-user-store:

UserStore Protocol
------------------

The :class:`~hs_py.storage.protocol.UserStore` protocol defines five async
methods for user persistence.  All three storage backends implement it:

.. code-block:: python

   class UserStore(Protocol):
       async def get_user(self, username: str) -> User | None: ...
       async def list_users(self) -> list[User]: ...
       async def create_user(self, user: User) -> None: ...
       async def update_user(self, username: str, **fields) -> User: ...
       async def delete_user(self, username: str) -> bool: ...

Each backend (:class:`~hs_py.storage.memory.InMemoryAdapter`,
:class:`~hs_py.storage.redis.RedisAdapter`,
:class:`~hs_py.storage.timescale.TimescaleAdapter`) implements both
``StorageAdapter`` and ``UserStore``, so a single instance serves as a unified
storage layer.

.. _guide-auth-storage-auth:

StorageAuthenticator
--------------------

:class:`~hs_py.auth_types.StorageAuthenticator` bridges the
:class:`~hs_py.auth_types.Authenticator` protocol to a ``UserStore``.  It
reads SCRAM credentials from the store and returns ``None`` for disabled or
missing users (blocking authentication).

.. code-block:: python

   from hs_py.auth_types import StorageAuthenticator
   from hs_py.storage.memory import InMemoryAdapter

   storage = InMemoryAdapter()
   auth = StorageAuthenticator(storage)

Wire this into :func:`~hs_py.fastapi_server.create_fastapi_app`:

.. code-block:: python

   from hs_py.fastapi_server import create_fastapi_app

   app = create_fastapi_app(
       storage=storage,
       authenticator=auth,
       user_store=storage,
   )

.. _guide-auth-bootstrap:

Admin Bootstrap
---------------

On startup, the server calls :func:`~hs_py.bootstrap.ensure_superuser` to
verify that at least one enabled Admin user exists.  If none is found, it
attempts to seed one from environment variables:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Description
   * - ``HS_SUPERUSER_USERNAME``
     - Username for the seeded admin account
   * - ``HS_SUPERUSER_PASSWORD``
     - Password for the seeded admin account

If neither an Admin user nor environment variables are present, the server
exits with a clear error message.

.. code-block:: bash

   export HS_SUPERUSER_USERNAME=admin
   export HS_SUPERUSER_PASSWORD=supersecret
   uvicorn myapp:app --host 0.0.0.0 --port 8080

.. _guide-auth-api:

User Management API
-------------------

Admin users can manage users via REST JSON endpoints under ``/api/users/``:

.. list-table::
   :header-rows: 1
   :widths: 12 25 63

   * - Method
     - Endpoint
     - Description
   * - ``POST``
     - ``/api/users/``
     - Create a user (``username``, ``password``, ``role`` required)
   * - ``GET``
     - ``/api/users/``
     - List all users
   * - ``GET``
     - ``/api/users/{username}``
     - Get a single user
   * - ``PUT``
     - ``/api/users/{username}``
     - Update fields (password, role, enabled, email, etc.)
   * - ``DELETE``
     - ``/api/users/{username}``
     - Delete a user (self-delete prevented)

Example — create an operator user:

.. code-block:: bash

   curl -X POST http://localhost:8080/api/users/ \
     -H "Authorization: BEARER authToken=<token>" \
     -H "Content-Type: application/json" \
     -d '{
       "username": "operator1",
       "password": "pass123",
       "role": "operator",
       "first_name": "Jane",
       "email": "jane@example.com"
     }'

Example — promote a user to admin:

.. code-block:: bash

   curl -X PUT http://localhost:8080/api/users/operator1 \
     -H "Authorization: BEARER authToken=<token>" \
     -H "Content-Type: application/json" \
     -d '{"role": "admin"}'

Responses are plain JSON (not Haystack grids).  Passwords are write-only and
never included in API responses.

.. _guide-auth-enforcement:

Permission Enforcement
----------------------

Roles are enforced on every Haystack operation, on both HTTP and WebSocket
transports.

**Write ops** — require **Operator** or **Admin** role:

- ``hisWrite``, ``pointWrite``, ``invokeAction``
- ``watchSub``, ``watchUnsub``, ``watchPoll``

**Read ops** — require any authenticated role (Viewer+):

- ``read``, ``nav``, ``hisRead``
- ``defs``, ``libs``, ``filetypes``

**GET ops** (about, ops, formats, close) — accessible to any authenticated user.

**User management** — requires **Admin** role only.

When a user lacks sufficient permissions, the server returns a Haystack error
grid with a descriptive message:

.. code-block:: text

   Insufficient permissions: hisWrite requires operator or admin role

.. _guide-auth-complete-example:

Complete Example
----------------

.. code-block:: python

   import asyncio
   import uvicorn
   from hs_py import MARKER, Ref
   from hs_py.user import Role, create_user
   from hs_py.auth_types import StorageAuthenticator
   from hs_py.fastapi_server import create_fastapi_app
   from hs_py.storage.memory import InMemoryAdapter


   async def main():
       storage = InMemoryAdapter()
       await storage.start()

       # Seed entities
       await storage.load_entities([
           {"id": Ref("s1"), "site": MARKER, "dis": "Main Office"},
       ])

       # Create users with different roles
       await storage.create_user(
           create_user("admin", "admin-pass", role=Role.ADMIN)
       )
       await storage.create_user(
           create_user("operator", "op-pass", role=Role.OPERATOR)
       )
       await storage.create_user(
           create_user("viewer", "view-pass", role=Role.VIEWER)
       )

       # Wire auth and create the app
       auth = StorageAuthenticator(storage)
       app = create_fastapi_app(
           storage=storage,
           authenticator=auth,
           user_store=storage,
       )

       uvicorn.run(app, host="0.0.0.0", port=8080)


   asyncio.run(main())
