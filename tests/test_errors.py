from hs_py.errors import AuthError, CallError, HaystackError, NetworkError
from hs_py.grid import Grid


class TestHierarchy:
    def test_auth_error_is_haystack_error(self) -> None:
        assert issubclass(AuthError, HaystackError)

    def test_call_error_is_haystack_error(self) -> None:
        assert issubclass(CallError, HaystackError)

    def test_network_error_is_haystack_error(self) -> None:
        assert issubclass(NetworkError, HaystackError)


class TestCallError:
    def test_dis(self) -> None:
        g = Grid.make_error("bad request", trace="line 1")
        err = CallError("bad request", g)
        assert err.dis == "bad request"
        assert str(err) == "bad request"

    def test_grid(self) -> None:
        g = Grid.make_error("fail")
        err = CallError("fail", g)
        assert err.grid is g
        assert err.grid.is_error

    def test_trace(self) -> None:
        g = Grid.make_error("fail", trace="traceback here")
        err = CallError("fail", g)
        assert err.trace == "traceback here"

    def test_trace_none(self) -> None:
        g = Grid.make_error("fail")
        err = CallError("fail", g)
        assert err.trace is None

    def test_catch_as_haystack_error(self) -> None:
        g = Grid.make_error("x")
        try:
            raise CallError("x", g)
        except HaystackError:
            pass


class TestAuthError:
    def test_message(self) -> None:
        err = AuthError("invalid credentials")
        assert str(err) == "invalid credentials"


class TestNetworkError:
    def test_message(self) -> None:
        err = NetworkError("connection refused")
        assert str(err) == "connection refused"
