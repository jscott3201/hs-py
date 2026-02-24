"""Async Haystack HTTP API client.

Provides a high-level async client implementing the standard Project Haystack
operations over HTTP using JSON encoding.

See: https://project-haystack.org/doc/docHaystack/HttpApi
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal, overload

import aiohttp

from hs_py.auth import authenticate
from hs_py.convert import grid_to_pythonic
from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.errors import AuthError, CallError, NetworkError
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.tls import TLSConfig, build_client_ssl_context

__all__ = [
    "Client",
]

_log = logging.getLogger(__name__)

_JSON_CT = "application/json"


class Client:
    """Async Haystack HTTP API client.

    Usage::

        async with Client("http://host/api", "user", "pass") as c:
            about = await c.about()  # returns list[dict] by default
            points = await c.read("point and sensor")
            raw_grid = await c.about(raw=True)  # returns Grid
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: aiohttp.ClientTimeout | None = None,
        connector: aiohttp.BaseConnector | None = None,
        tls: TLSConfig | None = None,
        pythonic: bool = True,
    ) -> None:
        """Initialise the client.

        :param base_url: Haystack server base URL (e.g. ``http://host/api``).
        :param username: Username for SCRAM authentication (empty to skip auth).
        :param password: Password for SCRAM authentication.
        :param timeout: HTTP request timeout configuration.
        :param connector: Custom :class:`aiohttp.BaseConnector` for connection pooling.
        :param tls: Optional :class:`~hs_py.tls.TLSConfig` for TLS 1.3 connections.
        :param pythonic: When ``True`` (default) Grid-returning methods return
            ``list[dict[str, Any]]`` with Haystack kinds converted to plain Python
            values.  Pass ``False`` to always return raw :class:`~hs_py.grid.Grid`.
        """
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._connector = connector
        self._tls = tls
        self._pythonic = pythonic
        self._session: aiohttp.ClientSession | None = None
        self._auth_token: str | None = None

    async def __aenter__(self) -> Client:
        connector = self._connector
        if connector is None and self._tls is not None:
            ssl_ctx = build_client_ssl_context(self._tls)
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(
            timeout=self._timeout or aiohttp.ClientTimeout(total=30),
            connector=connector,
            connector_owner=self._connector is None,
        )
        _log.info("Client session opened for %s", self._base_url)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._call_get("close")
            await self._session.close()
            # Allow underlying connections to close gracefully (aiohttp best practice)
            await asyncio.sleep(0)
            self._session = None
            self._auth_token = None
            _log.info("Client session closed for %s", self._base_url)

    # ---- Standard ops ------------------------------------------------------

    @overload
    async def about(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def about(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def about(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def about(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query server information.

        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`
            regardless of the *pythonic* constructor setting.
        :returns: Server metadata rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        grid = await self._call_get("about")
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def ops(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def ops(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def ops(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def ops(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query available operations.

        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Supported ops rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        grid = await self._call_get("ops")
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def formats(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def formats(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def formats(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def formats(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query supported data formats.

        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Supported MIME type rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        grid = await self._call_get("formats")
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read(self, filter: str, limit: int | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def read(
        self, filter: str, limit: int | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities matching a filter expression.

        :param filter: Haystack filter string.
        :param limit: Maximum number of entities to return.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Matching entity rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        row: dict[str, Any] = {"filter": filter}
        if limit is not None:
            row["limit"] = Number(float(limit))
        grid_req = GridBuilder().add_col("filter").add_col("limit").add_row(row).to_grid()
        grid = await self._call("read", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read_by_ids(
        self, ids: list[Ref], *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def read_by_ids(
        self, ids: list[Ref], *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities by their identifiers.

        :param ids: List of entity Refs to read.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Matching entity rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        builder = GridBuilder().add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("read", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def nav(
        self, nav_id: str | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def nav(
        self, nav_id: str | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Navigate the entity tree.

        :param nav_id: Navigation ID for child lookup, or ``None`` for root.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Navigation children as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        row: dict[str, Any] = {"navId": nav_id}
        grid_req = GridBuilder().add_col("navId").add_row(row).to_grid()
        grid = await self._call("nav", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- History ops -------------------------------------------------------

    @overload
    async def his_read(self, id: Ref, range: str, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def his_read(
        self, id: Ref, range: str, *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def his_read(self, id: Ref, range: str, *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def his_read(
        self, id: Ref, range: str, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read time-series data for a single point.

        :param id: Ref of the historized point.
        :param range: Time range string (e.g. ``"today"``, ``"yesterday"``,
            ``"2024-01-01,2024-01-31"``).
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Time-series rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        grid_req = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": id, "range": range})
            .to_grid()
        )
        grid = await self._call("hisRead", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def his_read_batch(self, ids: list[Ref], range: str, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read time-series data for multiple points.

        :param ids: List of point Refs.
        :param range: Time range string.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Time-series rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        builder = GridBuilder().set_meta({"range": range}).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("hisRead", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def his_write(self, id: Ref, items: list[dict[str, Any]]) -> None:
        """Write time-series data to a single point.

        :param id: Ref of the historized point.
        :param items: List of dicts with ``ts`` and ``val`` keys.
        """
        builder = GridBuilder().set_meta({"id": id}).add_col("ts").add_col("val")
        for item in items:
            builder.add_row(item)
        await self._call("hisWrite", builder.to_grid())

    async def his_write_batch(self, grid: Grid) -> None:
        """Write time-series data for multiple points.

        :param grid: Pre-built grid with ``ts`` and ``v0``/``v1``/... columns.
        """
        await self._call("hisWrite", grid)

    # ---- Point write ops ---------------------------------------------------

    @overload
    async def point_write_array(self, id: Ref, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def point_write_array(self, id: Ref, *, raw: Literal[False]) -> list[dict[str, Any]]: ...
    @overload
    async def point_write_array(self, id: Ref, *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def point_write_array(
        self, id: Ref, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read the priority array of a writable point.

        :param id: Ref of the writable point.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Priority array rows as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        grid_req = GridBuilder().add_col("id").add_row({"id": id}).to_grid()
        grid = await self._call("pointWrite", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def point_write(
        self,
        id: Ref,
        level: int,
        val: Any,
        who: str = "",
        duration: Number | None = None,
    ) -> None:
        """Write to a priority array level.

        :param id: Ref of the writable point.
        :param level: Priority level 1-17.
        :param val: Value to write, or None to release.
        :param who: Identifier of who is making the write.
        :param duration: Optional duration for level 8 timed overrides.
        """
        row: dict[str, Any] = {
            "id": id,
            "level": Number(float(level)),
            "val": val,
            "who": who,
        }
        if duration is not None:
            row["duration"] = duration
        cols = ["id", "level", "val", "who", "duration"]
        builder = GridBuilder()
        for col in cols:
            builder.add_col(col)
        builder.add_row(row)
        await self._call("pointWrite", builder.to_grid())

    # ---- Watch ops ---------------------------------------------------------

    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = None,
        *,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Create a new watch or add entities to an existing one.

        :param ids: Entity Refs to watch.
        :param watch_dis: Display name for the watch.
        :param lease: Optional lease duration.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Current entity state as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        meta: dict[str, Any] = {"watchDis": watch_dis}
        if lease is not None:
            meta["lease"] = lease
        builder = GridBuilder().set_meta(meta).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("watchSub", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def watch_unsub(self, watch_id: str, ids: list[Ref]) -> None:
        """Remove entities from a watch.

        :param watch_id: Watch identifier.
        :param ids: Entity Refs to unwatch.
        """
        builder = GridBuilder().set_meta({"watchId": watch_id}).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        await self._call("watchUnsub", builder.to_grid())

    async def watch_close(self, watch_id: str) -> None:
        """Close a watch entirely.

        :param watch_id: Watch identifier to close.
        """
        builder = GridBuilder().set_meta({"watchId": watch_id, "close": MARKER}).add_col("id")
        await self._call("watchUnsub", builder.to_grid())

    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[True]
    ) -> Grid: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_poll(
        self, watch_id: str, refresh: bool = False, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Poll a watch for changes.

        :param watch_id: Watch identifier.
        :param refresh: If ``True``, return full refresh of all watched entities.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Changed (or all) entities as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        meta: dict[str, Any] = {"watchId": watch_id}
        if refresh:
            meta["refresh"] = MARKER
        grid_req = GridBuilder().set_meta(meta).to_grid()
        grid = await self._call("watchPoll", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- Action ops --------------------------------------------------------

    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Invoke an action on an entity.

        :param id: Ref of the target entity.
        :param action: Action name.
        :param args: Optional action arguments.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        :returns: Action results as ``list[dict]`` or :class:`~hs_py.grid.Grid`.
        """
        meta: dict[str, Any] = {"id": id, "action": action}
        builder = GridBuilder().set_meta(meta)
        if args:
            for key in args:
                builder.add_col(key)
            builder.add_row(args)
        grid = await self._call("invokeAction", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- Internal plumbing -------------------------------------------------

    async def _ensure_auth(self) -> None:
        """Authenticate if not already authenticated."""
        if self._auth_token is not None:
            return
        session = self._require_session()
        if self._username:
            _log.debug("Authenticating user '%s' against %s", self._username, self._base_url)
            self._auth_token = await authenticate(
                session, self._base_url, self._username, self._password
            )
            _log.debug("Authentication successful for '%s'", self._username)
        else:
            self._auth_token = ""

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers."""
        headers: dict[str, str] = {"Content-Type": _JSON_CT, "Accept": _JSON_CT}
        if self._auth_token:
            headers["Authorization"] = f"BEARER authToken={self._auth_token}"
        return headers

    async def _request(self, method: str, op: str, **kwargs: Any) -> Grid:
        """Send an HTTP request with automatic re-auth on 401."""
        await self._ensure_auth()
        session = self._require_session()
        url = f"{self._base_url}/{op}"
        kwargs["headers"] = self._auth_headers()
        _log.debug("%s %s", method, url)
        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 401:
                    _log.warning("Auth expired for %s, re-authenticating", url)
                    # Consume body so the connection can be reused
                    await resp.read()
                    self._auth_token = None
                    await self._ensure_auth()
                    kwargs["headers"] = self._auth_headers()
                    async with session.request(method, url, **kwargs) as retry:
                        return await self._handle_response(retry)
                return await self._handle_response(resp)
        except aiohttp.ClientError as exc:
            _log.warning("Request failed: %s %s — %s", method, url, exc)
            raise NetworkError(str(exc)) from exc

    async def _call(self, op: str, grid: Grid) -> Grid:
        """POST a grid to an operation endpoint and return the response grid."""
        return await self._request("POST", op, data=encode_grid(grid))

    async def _call_get(self, op: str, params: dict[str, str] | None = None) -> Grid:
        """GET an operation endpoint and return the response grid."""
        return await self._request("GET", op, params=params)

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Grid:
        """Decode a response and check for error grids."""
        if resp.status == 401:
            raise AuthError(f"Authentication failed: {resp.status}")
        data = await resp.read()
        if not data:
            return Grid.make_empty()
        grid = decode_grid(data)
        if grid.is_error:
            raise CallError(grid.meta.get("dis", "Unknown error"), grid)
        return grid

    def _require_session(self) -> aiohttp.ClientSession:
        """Return the active session or raise."""
        if self._session is None:
            msg = "Client is not open. Use 'async with Client(...) as c:'"
            raise RuntimeError(msg)
        return self._session
