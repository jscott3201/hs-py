from typing import Any

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hs_py.client import Client
from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.errors import CallError
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref


def _json_response(grid: Grid) -> web.Response:
    return web.Response(body=encode_grid(grid), content_type="application/json")


class TestClientOps(AioHTTPTestCase):
    """Test client operations against a mock Haystack server."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_get("/api/ops", self._handle_ops)
        app.router.add_get("/api/formats", self._handle_formats)
        app.router.add_get("/api/close", self._handle_close)
        app.router.add_post("/api/read", self._handle_read)
        app.router.add_post("/api/nav", self._handle_nav)
        app.router.add_post("/api/hisRead", self._handle_his_read)
        app.router.add_post("/api/hisWrite", self._handle_his_write)
        app.router.add_post("/api/pointWrite", self._handle_point_write)
        app.router.add_post("/api/watchSub", self._handle_watch_sub)
        app.router.add_post("/api/watchUnsub", self._handle_watch_unsub)
        app.router.add_post("/api/watchPoll", self._handle_watch_poll)
        app.router.add_post("/api/invokeAction", self._handle_invoke_action)
        app.router.add_post("/api/errorOp", self._handle_error_op)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "serverName": "TestServer",
                    "productName": "hs-py-test",
                }
            ]
        )
        return _json_response(grid)

    async def _handle_ops(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows(
            [
                {"name": "about", "summary": "About"},
                {"name": "read", "summary": "Read"},
            ]
        )
        return _json_response(grid)

    async def _handle_formats(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows(
            [
                {"mime": "application/json", "receive": MARKER, "send": MARKER},
            ]
        )
        return _json_response(grid)

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_read(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        if req_grid.rows and "filter" in req_grid[0]:
            grid = Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1", "point": MARKER},
                    {"id": Ref("p2"), "dis": "Point 2", "point": MARKER},
                ]
            )
        elif req_grid.rows and "id" in req_grid[0]:
            rows = [{"id": row["id"], "dis": f"Entity {row['id'].val}"} for row in req_grid]
            grid = Grid.make_rows(rows)
        else:
            grid = Grid.make_empty()
        return _json_response(grid)

    async def _handle_nav(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows(
            [
                {"navId": "site-1", "dis": "Site 1"},
                {"navId": "site-2", "dis": "Site 2"},
            ]
        )
        return _json_response(grid)

    async def _handle_his_read(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        meta: dict[str, Any] = {"id": req_grid[0]["id"], "hisStart": "start", "hisEnd": "end"}
        grid = (
            GridBuilder()
            .set_meta(meta)
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0, "°F")})
            .to_grid()
        )
        return _json_response(grid)

    async def _handle_his_write(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_point_write(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        if "level" in req_grid[0]:
            return _json_response(Grid.make_empty())
        grid = Grid.make_rows(
            [
                {"level": Number(1), "levelDis": "Emergency", "val": None, "who": ""},
            ]
        )
        return _json_response(grid)

    async def _handle_watch_sub(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        rows = [{"id": row["id"], "dis": f"Watched {row['id'].val}"} for row in req_grid]
        meta: dict[str, Any] = {"watchId": "w-001", "lease": Number(60, "s")}
        builder = GridBuilder().set_meta(meta).add_col("id").add_col("dis")
        for row in rows:
            builder.add_row(row)
        return _json_response(builder.to_grid())

    async def _handle_watch_unsub(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_watch_poll(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows([{"id": Ref("p1"), "val": Number(73.0, "°F")}])
        return _json_response(grid)

    async def _handle_invoke_action(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        action = req_grid.meta.get("action", "unknown")
        grid = Grid.make_rows([{"result": f"Invoked {action}"}])
        return _json_response(grid)

    async def _handle_error_op(self, _request: web.Request) -> web.Response:
        grid = Grid.make_error("Something went wrong", trace="at line 42")
        return _json_response(grid)

    def _make_client(self) -> Client:
        base_url = f"http://localhost:{self.server.port}/api"
        client = Client(base_url, pythonic=False)
        client._session = self.client.session
        client._auth_token = ""
        return client

    # ---- Tests -------------------------------------------------------------

    async def test_about(self) -> None:
        c = self._make_client()
        grid = await c.about()
        assert grid[0]["serverName"] == "TestServer"

    async def test_ops(self) -> None:
        c = self._make_client()
        grid = await c.ops()
        assert len(grid) == 2
        assert grid[0]["name"] == "about"

    async def test_formats(self) -> None:
        c = self._make_client()
        grid = await c.formats()
        assert grid[0]["mime"] == "application/json"

    async def test_read_filter(self) -> None:
        c = self._make_client()
        grid = await c.read("point and sensor")
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("p1")

    async def test_read_by_ids(self) -> None:
        c = self._make_client()
        grid = await c.read_by_ids([Ref("a"), Ref("b")])
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("a")
        assert grid[1]["id"] == Ref("b")

    async def test_nav(self) -> None:
        c = self._make_client()
        grid = await c.nav()
        assert len(grid) == 2
        assert grid[0]["navId"] == "site-1"

    async def test_his_read(self) -> None:
        c = self._make_client()
        grid = await c.his_read(Ref("p1"), "today")
        assert len(grid) == 1
        assert grid.meta["id"] == Ref("p1")

    async def test_his_write(self) -> None:
        c = self._make_client()
        await c.his_write(Ref("p1"), [{"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)}])

    async def test_point_write_array(self) -> None:
        c = self._make_client()
        grid = await c.point_write_array(Ref("p1"))
        assert len(grid) == 1

    async def test_point_write(self) -> None:
        c = self._make_client()
        await c.point_write(Ref("p1"), 8, Number(72.0, "°F"), who="test")

    async def test_watch_sub(self) -> None:
        c = self._make_client()
        grid = await c.watch_sub([Ref("p1"), Ref("p2")], "Test Watch")
        assert grid.meta["watchId"] == "w-001"
        assert len(grid) == 2

    async def test_watch_unsub(self) -> None:
        c = self._make_client()
        await c.watch_unsub("w-001", [Ref("p1")])

    async def test_watch_close(self) -> None:
        c = self._make_client()
        await c.watch_close("w-001")

    async def test_watch_poll(self) -> None:
        c = self._make_client()
        grid = await c.watch_poll("w-001")
        assert len(grid) == 1

    async def test_invoke_action(self) -> None:
        c = self._make_client()
        grid = await c.invoke_action(Ref("p1"), "doSomething", {"arg1": "val1"})
        assert grid[0]["result"] == "Invoked doSomething"

    async def test_error_grid_raises_call_error(self) -> None:
        c = self._make_client()
        try:
            grid = GridBuilder().to_grid()
            await c._call("errorOp", grid)
            raise AssertionError("should raise CallError")
        except CallError as e:
            assert e.dis == "Something went wrong"
            assert e.trace == "at line 42"


class TestClientContextManager(AioHTTPTestCase):
    """Test client lifecycle."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_rows([{"serverName": "Test"}]))

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def test_session_not_open_raises(self) -> None:
        c = Client("http://localhost/api", pythonic=False)
        try:
            await c.about()
            raise AssertionError("should raise")
        except RuntimeError:
            pass
