"""Unit tests for JSON-RPC 2.0 message parsing, routing, and errors."""

from typing import Any

import pytest

from litestar_mcp.core import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRequest,
    JSONRPCRouter,
    error_response,
    parse_request,
)


def test_parse_valid_request() -> None:
    """Verify parsing a valid JSON-RPC 2.0 request dictionary."""
    req = parse_request({"jsonrpc": "2.0", "id": "req-1", "method": "test/method", "params": {"a": 1}})
    assert req.jsonrpc == "2.0"
    assert req.id == "req-1"
    assert req.method == "test/method"
    assert req.params == {"a": 1}
    assert not req.is_notification


def test_parse_notification() -> None:
    """Verify parsing a JSON-RPC 2.0 notification without an id."""
    req = parse_request({"jsonrpc": "2.0", "method": "notify"})
    assert req.id is None
    assert req.is_notification


def test_parse_invalid_requests() -> None:
    """Verify invalid payloads raise JSONRPCErrorException with INVALID_REQUEST."""
    for invalid in ("string", [1, 2], {"method": "foo"}, {"jsonrpc": "1.0", "method": "foo"}, {"jsonrpc": "2.0"}):
        with pytest.raises(JSONRPCErrorException) as exc_info:
            parse_request(invalid)
        assert exc_info.value.error.code == INVALID_REQUEST


def test_error_response_builder() -> None:
    """Verify error_response builds compliant JSON-RPC 2.0 error dictionaries."""
    err = JSONRPCError(code=INVALID_PARAMS, message="Invalid params", data={"field": "x"})
    resp = error_response("req-1", err)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == "req-1"
    assert resp["error"]["code"] == INVALID_PARAMS
    assert resp["error"]["message"] == "Invalid params"
    assert resp["error"]["data"] == {"field": "x"}


@pytest.mark.asyncio
async def test_router_dispatch() -> None:
    """Verify JSONRPCRouter method registration, execution, and error handling."""
    router = JSONRPCRouter()

    async def greet(params: dict[str, Any], context: Any) -> dict[str, Any]:
        if "fail" in params:
            raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message="Boom"))
        return {"greeting": f"Hello {params.get('name')}", "ctx": context}

    router.register("greet", greet)
    assert "greet" in router.methods

    # Successful call
    req = JSONRPCRequest(jsonrpc="2.0", method="greet", id=1, params={"name": "Alice"})
    resp = await router.dispatch(req, "test-context")
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"greeting": "Hello Alice", "ctx": "test-context"}}

    # Method not found
    missing_req = JSONRPCRequest(jsonrpc="2.0", method="unknown", id=2)
    missing_resp = await router.dispatch(missing_req, None)
    assert missing_resp is not None
    assert missing_resp["error"]["code"] == METHOD_NOT_FOUND

    # Notification not found returns None
    missing_notif = JSONRPCRequest(jsonrpc="2.0", method="unknown")
    assert await router.dispatch(missing_notif, None) is None

    # Handler structured error
    err_req = JSONRPCRequest(jsonrpc="2.0", method="greet", id=3, params={"fail": True})
    err_resp = await router.dispatch(err_req, None)
    assert err_resp is not None
    assert err_resp["error"]["code"] == INTERNAL_ERROR
