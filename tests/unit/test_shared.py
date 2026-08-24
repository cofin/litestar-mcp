"""Unit tests for litestar_mcp.shared foundation primitives."""

from contextlib import AsyncExitStack
from dataclasses import dataclass

import pytest
from litestar import Litestar, Request, get
from litestar.stores.memory import MemoryStore

import litestar_mcp.shared as shared_pkg
from litestar_mcp.shared.executor import (
    CapturedHandlerResponse,
    NotCallableInCLIContextError,
    PathParamCoercionError,
    build_dispatch_scope,
    find_route_path_parameters,
    run_handler_pipeline,
)
from litestar_mcp.shared.introspection import (
    basic_type_to_json_schema,
    collection_type_to_json_schema,
    dataclass_to_json_schema,
    union_type_to_json_schema,
)
from litestar_mcp.shared.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRequest,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.shared.sse import (
    BaseSubscriptionManager,
    StreamLimitExceeded,
)
from litestar_mcp.shared.tasks import (
    BaseTaskStore,
    TaskLookupError,
    TaskRecord,
    _format_datetime,
    _parse_datetime,
    _utc_now,
)


def test_shared_exports() -> None:
    """Verify shared package exports are present."""
    for symbol in shared_pkg.__all__:
        assert hasattr(shared_pkg, symbol)


def test_jsonrpc_primitives() -> None:
    """Test JSON-RPC basic parsing, error response, and routing."""
    req = parse_request({"jsonrpc": "2.0", "method": "test", "id": 1, "params": {"a": "b"}})
    assert req.jsonrpc == "2.0"
    assert req.method == "test"
    assert req.id == 1
    assert req.params == {"a": "b"}
    assert not req.is_notification

    notification = parse_request({"jsonrpc": "2.0", "method": "notify"})
    assert notification.is_notification

    err_resp = error_response(1, JSONRPCError(code=INVALID_PARAMS, message="bad params", data={"x": 1}))
    assert err_resp["error"]["code"] == INVALID_PARAMS
    assert err_resp["error"]["data"] == {"x": 1}

    with pytest.raises(JSONRPCErrorException):
        parse_request(["not", "a", "dict"])

    with pytest.raises(JSONRPCErrorException):
        parse_request({"jsonrpc": "1.0", "method": "foo"})

    with pytest.raises(JSONRPCErrorException):
        parse_request({"jsonrpc": "2.0", "method": 123})


@pytest.mark.asyncio
async def test_jsonrpc_router_dispatch() -> None:
    """Test JSONRPCRouter method registration and execution."""
    router = JSONRPCRouter()

    async def my_handler(params: dict, context: dict) -> dict:
        if "fail" in params:
            raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message="Custom error"))
        if "crash" in params:
            msg = "unexpected"
            raise RuntimeError(msg)
        return {"echo": params.get("value"), "ctx": context.get("user")}

    router.register("echo", my_handler)
    assert "echo" in router.methods

    # Success call
    req = JSONRPCRequest(jsonrpc="2.0", method="echo", id=10, params={"value": 42})
    resp = await router.dispatch(req, {"user": "alice"})
    assert resp is not None
    assert resp["result"] == {"echo": 42, "ctx": "alice"}

    # Not found
    nf_req = JSONRPCRequest(jsonrpc="2.0", method="unknown", id=11)
    nf_resp = await router.dispatch(nf_req, {})
    assert nf_resp is not None
    assert nf_resp["error"]["code"] == METHOD_NOT_FOUND

    # Notification not found
    nf_notif = JSONRPCRequest(jsonrpc="2.0", method="unknown", id=None)
    assert await router.dispatch(nf_notif, {}) is None

    # Handler error
    err_req = JSONRPCRequest(jsonrpc="2.0", method="echo", id=12, params={"fail": True})
    err_resp = await router.dispatch(err_req, {})
    assert err_resp is not None
    assert err_resp["error"]["code"] == INTERNAL_ERROR

    # Handler unhandled exception
    crash_req = JSONRPCRequest(jsonrpc="2.0", method="echo", id=13, params={"crash": True})
    crash_resp = await router.dispatch(crash_req, {})
    assert crash_resp is not None
    assert crash_resp["error"]["code"] == INTERNAL_ERROR


@pytest.mark.asyncio
async def test_sse_base_subscription_manager() -> None:
    """Test BaseSubscriptionManager open, disconnect, and stream limit."""
    mgr = BaseSubscriptionManager(max_streams=2)
    sub_id, stream_gen = await mgr.open_stream("sub-1", initial_message={"hello": "world"})
    assert sub_id in mgr._streams

    first = await anext(stream_gen)
    assert first == {"hello": "world"}

    # Open second
    sub_id_2, _ = await mgr.open_stream("sub-2")
    assert sub_id_2 in mgr._streams

    # Exceed limit
    with pytest.raises(StreamLimitExceeded):
        await mgr.open_stream("sub-3")

    # Disconnect
    await mgr.disconnect(sub_id)
    assert sub_id not in mgr._streams

    # Close all
    await mgr.close_all()
    assert len(mgr._streams) == 0


@pytest.mark.asyncio
async def test_tasks_base_task_store() -> None:
    """Test BaseTaskStore persistence, retrieval, and error paths."""
    store = MemoryStore()
    task_store = BaseTaskStore(store=store, default_ttl_ms=60_000, key_prefix="test_task")

    now = _utc_now()
    record = TaskRecord(
        task_id="t-1",
        owner_id="user-1",
        status="working",
        created_at=now,
        last_updated_at=now,
        ttl_ms=60_000,
        poll_interval_ms=1000,
        status_message="Processing",
        result={"done": False},
        error=None,
    )
    assert not record.is_terminal(frozenset({"completed", "failed"}))

    await task_store._persist(record)

    fetched = await task_store.get("t-1", owner_id="user-1")
    assert fetched.task_id == "t-1"
    assert fetched.status == "working"
    assert fetched.status_message == "Processing"
    assert fetched.result == {"done": False}

    # Wrong owner
    with pytest.raises(TaskLookupError):
        await task_store.get("t-1", owner_id="other-user")

    # Non-existent
    with pytest.raises(TaskLookupError):
        await task_store.get("non-existent")


def test_tasks_date_helpers() -> None:
    """Test datetime formatting and parsing."""
    now = _utc_now()
    formatted = _format_datetime(now)
    assert formatted.endswith("Z")
    parsed = _parse_datetime(formatted)
    assert parsed.year == now.year


@pytest.mark.asyncio
async def test_executor_pipeline() -> None:
    """Test synthetic executor pipeline with Litestar app and handlers."""
    from tests.unit.conftest import create_app_with_handler

    async def get_item(item_id: int, q: str = "default") -> dict:
        return {"item_id": item_id, "q": q}

    app, handler = create_app_with_handler(get_item, route_path="/items/{item_id:int}")

    path_params = find_route_path_parameters(app, handler)
    assert "item_id" in path_params

    # Build scope
    scope, receive = build_dispatch_scope(
        handler,
        {"item_id": 42, "q": "search"},
        base_scope=None,
        scope_overrides=None,
        app=app,
        path_parameters=path_params,
    )
    assert scope["path"] == "/items/42"
    assert scope["path_params"] == {"item_id": 42}

    # Execute pipeline
    async with AsyncExitStack() as stack:
        req = Request(scope, receive=receive)
        response = await run_handler_pipeline(handler, app, path_params, req, stack)
        assert isinstance(response, CapturedHandlerResponse)
        assert response.status_code == 200
        assert response.content == {"item_id": 42, "q": "search"}

    # Coercion failure
    with pytest.raises(PathParamCoercionError):
        build_dispatch_scope(
            handler,
            {"item_id": "not-an-int"},
            base_scope=None,
            scope_overrides=None,
            app=app,
            path_parameters=path_params,
        )


def test_introspection_helpers() -> None:
    """Test shared introspection type converters."""
    assert basic_type_to_json_schema(str) == {"type": "string"}
    assert basic_type_to_json_schema(int) == {"type": "integer"}
    assert basic_type_to_json_schema(float) == {"type": "number"}
    assert basic_type_to_json_schema(bool) == {"type": "boolean"}

    assert collection_type_to_json_schema(list[str]) == {"type": "array", "items": {"type": "string"}}
    assert collection_type_to_json_schema(dict) == {"type": "object"}

    @dataclass
    class SimpleModel:
        name: str
        age: int = 0

    schema = dataclass_to_json_schema(SimpleModel)
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "name" in schema["required"]
    assert "age" not in schema.get("required", [])

    union_schema = union_type_to_json_schema(str | None)
    assert union_schema is not None
    assert "anyOf" in union_schema

    err = NotCallableInCLIContextError("tool1", "requires auth")
    assert "cannot be called" in str(err)
