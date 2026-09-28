import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import anyio
import pytest
from litestar import Request, get
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException

import litestar_mcp
from litestar_mcp import MCP, MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.stdio import MCPStdioContext, run_stdio_async
from litestar_mcp.utils import mcp_tool
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def _stdio_context(**kwargs: "Any") -> "MCPStdioContext":
    return litestar_mcp.MCPStdioContext(**kwargs)


def _request(
    method: "str",
    *,
    request_id: "int" = 1,
    params: "dict[str, Any] | None" = None,
    tasks_capable: "bool" = False,
) -> "dict[str, Any]":
    request_params = dict(params or {})
    capabilities: dict[str, Any] = {}
    if tasks_capable:
        capabilities["extensions"] = {"io.modelcontextprotocol/tasks": {}}
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": capabilities,
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _discover_request(request_id: int = 1) -> "dict[str, Any]":
    return _request("server/discover", request_id=request_id)


def _write_skill_root(tmp_path: "Path") -> "Path":
    root = tmp_path / "skills"
    skill_dir = root / "demo"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: Demo skill\nversion: 1\n---\n\n# Demo\n")
    (scripts_dir / "run.py").write_text('print("hi")\n')
    return root


def _skills_mcp(tmp_path: "Path") -> "MCP":
    root = _write_skill_root(tmp_path)
    return MCP(name="stdio-skills", config=MCPConfig(skills=MCPSkillsConfig(paths=[root])))


async def _run_stdio_exchange(
    mcp: "MCP",
    requests: "list[dict[str, Any]]",
    *,
    stdio_context: "MCPStdioContext | None" = None,
) -> "list[dict[str, Any]]":
    stdin = BridgeQueuedBytesSource(*(json.dumps(request).encode("utf-8") + b"\n" for request in requests))
    stdout = BridgeBytesSink()
    with anyio.fail_after(10):
        exit_code = await run_stdio_async(
            mcp.app,
            stdio_context=stdio_context or MCPStdioContext(),
            stdin=stdin,
            stdout=stdout,
        )
    assert exit_code == 0
    by_id = {
        message["id"]: message
        for message in (json.loads(line) for line in stdout.buffer.splitlines() if line.strip())
        if "id" in message
    }
    return [by_id[request["id"]] for request in requests if "id" in request]


async def _wait_for_terminal(store: "Any", task_id: "str", owner_id: "str | None") -> "Any":
    for _ in range(100):
        record = await store.get(task_id, owner_id)
        if record.is_terminal():
            return record
        await asyncio.sleep(0.01)
    msg = f"Task {task_id} did not reach a terminal state"
    raise AssertionError(msg)


def test_mcp_stdio_context_is_public_with_defaults() -> "None":
    context = _stdio_context()

    assert context.client_id == "stdio"
    assert context.owner_id is None
    assert context.user is None
    assert context.auth is None
    assert context.session is None
    assert context.state is None


@pytest.mark.anyio
async def test_standalone_stdio_tool_execution() -> "None":
    mcp = MCP(name="stdio-test")

    @mcp.tool(name="greet")
    def greet(name: "str") -> "str":
        return f"Hello {name}"

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "greet", "arguments": {"name": "World"}}),
        ],
    )

    assert "result" in responses[0]
    assert responses[1]["result"]["content"][0]["text"] == "Hello World"


@pytest.mark.anyio
async def test_standalone_stdio_context_propagates_identity_to_tool() -> "None":
    user = SimpleNamespace(id="user-123")
    session = {"tenant": "acme"}
    state = {"feature": "enabled"}

    @get("/whoami", mcp_tool="whoami", sync_to_thread=False)
    def whoami(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        session_scope = cast("dict[str, Any]", request.scope["session"])
        state_scope = request.scope["state"]
        auth_scope = cast("dict[str, Any]", request.scope["auth"])
        session_scope["mutated"] = "yes"
        state_scope["mutated"] = "yes"
        return {
            "user_id": request.user.id,
            "auth_sub": auth_scope["sub"],
            "tenant": session_scope["tenant"],
            "feature": state_scope["feature"],
        }

    mcp = MCP(name="stdio-context-test", route_handlers=[whoami])
    context = _stdio_context(
        user=user,
        auth={"sub": "auth-subject", "role": "admin"},
        session=session,
        state=state,
    )

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "whoami", "arguments": {}}),
        ],
        stdio_context=context,
    )

    payload = json.loads(responses[1]["result"]["content"][0]["text"])
    assert payload == {
        "user_id": "user-123",
        "auth_sub": "auth-subject",
        "tenant": "acme",
        "feature": "enabled",
    }
    assert session == {"tenant": "acme"}
    assert state == {"feature": "enabled"}


@pytest.mark.anyio
async def test_standalone_stdio_context_isolates_auth_mutations_between_calls() -> "None":
    auth = {"sub": "auth-subject"}

    @get("/probe", mcp_tool="probe", sync_to_thread=False)
    def probe(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        auth_scope = cast("dict[str, Any]", request.scope["auth"])
        seen_mutation = auth_scope.get("mutation")
        auth_scope["mutation"] = "leaked"
        return {"seen_mutation": seen_mutation}

    mcp = MCP(name="stdio-auth-isolation-test", route_handlers=[probe])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "probe", "arguments": {}}),
            _request("tools/call", request_id=3, params={"name": "probe", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth=auth),
    )

    first = json.loads(responses[1]["result"]["content"][0]["text"])
    second = json.loads(responses[2]["result"]["content"][0]["text"])
    assert first["seen_mutation"] is None
    # A per-call copy means the first call's write must not leak into the second.
    assert second["seen_mutation"] is None
    # The caller's original auth dict is likewise untouched.
    assert auth == {"sub": "auth-subject"}


@pytest.mark.anyio
async def test_standalone_stdio_context_authorizes_guards_from_scope() -> "None":
    def require_admin(connection: "Any", _handler: "Any") -> "None":
        auth = connection.scope.get("auth") or {}
        if auth.get("role") != "admin":
            msg = "admin role required"
            raise NotAuthorizedException(msg)

    @get("/guarded", guards=[require_admin], mcp_tool="guarded", sync_to_thread=False)
    def guarded() -> "dict[str, bool]":
        return {"ok": True}

    mcp = MCP(name="stdio-guard-test", route_handlers=[guarded])

    allowed = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "guarded", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth={"role": "admin"}),
    )
    assert allowed[1]["result"]["isError"] is False

    denied = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "guarded", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth={"role": "viewer"}),
    )
    assert denied[1]["result"]["isError"] is True


@pytest.mark.anyio
async def test_standalone_stdio_context_propagates_identity_to_resources() -> "None":
    @get("/profile", mcp_resource="profile", sync_to_thread=False)
    def profile(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        return {
            "user_id": request.user.id,
            "auth_sub": request.scope["auth"]["sub"],
        }

    mcp = MCP(name="stdio-resource-test", route_handlers=[profile])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("resources/read", request_id=2, params={"uri": "litestar://profile"}),
        ],
        stdio_context=_stdio_context(user=SimpleNamespace(id="resource-user"), auth={"sub": "resource-sub"}),
    )

    payload = json.loads(responses[1]["result"]["contents"][0]["text"])
    assert payload == {"user_id": "resource-user", "auth_sub": "resource-sub"}


@pytest.mark.anyio
async def test_standalone_stdio_task_owner_defaults_to_auth_subject() -> "None":
    @get("/optional-task", sync_to_thread=False)
    @mcp_tool(name="owner_task", task_support="optional")
    async def owner_task(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        return {"auth_sub": request.scope["auth"]["sub"]}

    mcp = MCP(name="stdio-task-owner-test", config=MCPConfig(tasks=True), route_handlers=[owner_task])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request(
                "tools/call",
                request_id=2,
                params={"name": "owner_task", "arguments": {}},
                tasks_capable=True,
            ),
        ],
        stdio_context=_stdio_context(auth={"sub": "owner-from-auth"}),
    )

    assert responses[1]["result"]["resultType"] == "task"
    task_id = responses[1]["result"]["taskId"]
    assert mcp.plugin.task_store is not None
    record = await _wait_for_terminal(mcp.plugin.task_store, task_id, "user:owner-from-auth")
    assert record.owner_id == "user:owner-from-auth"
    assert record.result is not None
    payload = json.loads(record.result["content"][0]["text"])
    assert payload == {"auth_sub": "owner-from-auth"}


@pytest.mark.anyio
async def test_standalone_stdio_task_owner_prefers_explicit_owner_id() -> "None":
    @get("/optional-task", sync_to_thread=False)
    @mcp_tool(name="explicit_owner_task", task_support="optional")
    async def explicit_owner_task() -> "dict[str, bool]":
        return {"ok": True}

    mcp = MCP(name="stdio-explicit-owner-test", config=MCPConfig(tasks=True), route_handlers=[explicit_owner_task])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request(
                "tools/call",
                request_id=2,
                params={"name": "explicit_owner_task", "arguments": {}},
                tasks_capable=True,
            ),
        ],
        stdio_context=_stdio_context(owner_id="explicit-owner", auth={"sub": "auth-owner"}),
    )

    assert responses[1]["result"]["resultType"] == "task"
    task_id = responses[1]["result"]["taskId"]
    assert mcp.plugin.task_store is not None
    record = await mcp.plugin.task_store.get(task_id, "explicit-owner")
    assert record.owner_id == "explicit-owner"


@pytest.mark.anyio
async def test_standalone_stdio_litestar_dependency_resolution() -> "None":
    def provide_suffix() -> "str":
        return "!"

    @get(
        "/greet",
        mcp_tool="greet",
        dependencies={"suffix": Provide(provide_suffix, sync_to_thread=False)},
    )
    async def greet(name: "str", suffix: "str") -> "dict[str, str]":
        return {"message": f"Hello {name}{suffix}"}

    mcp = MCP(name="stdio-di-test", route_handlers=[greet])
    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "greet", "arguments": {"name": "World"}}),
        ],
    )

    assert "result" in responses[0]
    assert json.loads(responses[1]["result"]["content"][0]["text"]) == {"message": "Hello World!"}


@pytest.mark.anyio
async def test_standalone_stdio_lifespan_hooks() -> "None":
    startup_called = False
    shutdown_called = False

    async def on_startup() -> "None":
        nonlocal startup_called
        startup_called = True

    async def on_shutdown() -> "None":
        nonlocal shutdown_called
        shutdown_called = True

    @get("/probe", mcp_tool="probe", sync_to_thread=False)
    def probe() -> "dict[str, bool]":
        return {"startup": startup_called, "shutdown": shutdown_called}

    mcp = MCP(name="lifespan-test", route_handlers=[probe], on_startup=[on_startup], on_shutdown=[on_shutdown])
    responses = await _run_stdio_exchange(
        mcp,
        [_request("tools/call", params={"name": "probe", "arguments": {}})],
    )

    assert json.loads(responses[0]["result"]["content"][0]["text"]) == {"startup": True, "shutdown": False}
    assert shutdown_called is True


@pytest.mark.anyio
async def test_standalone_stdio_startup_failure_raises() -> "None":
    original = RuntimeError("database unavailable")

    async def failing_startup() -> "None":
        raise original

    mcp = MCP(name="startup-failure", on_startup=[failing_startup])

    with pytest.raises(Exception) as caught:
        await run_stdio_async(
            mcp.app,
            stdio_context=MCPStdioContext(),
            stdin=BridgeQueuedBytesSource(),
            stdout=BridgeBytesSink(),
        )

    assert original in getattr(caught.value, "exceptions", (caught.value,))


@pytest.mark.anyio
async def test_standalone_stdio_forwards_jsonrpc_errors_and_keeps_serving() -> "None":
    mcp = MCP(name="stdio-errors")

    @mcp.tool(name="greet")
    def greet(name: "str") -> "str":
        return f"Hello {name}"

    responses = await _run_stdio_exchange(
        mcp,
        [
            _request("no/such-method", request_id=1),
            _request("tools/call", request_id=2, params={"name": "missing", "arguments": {}}),
            _request("tools/call", request_id=3, params={"name": "greet", "arguments": {"name": "World"}}),
        ],
    )

    assert responses[0]["error"]["code"] == -32601
    assert responses[1]["error"]["code"] == -32602
    assert responses[2]["result"]["content"][0]["text"] == "Hello World"


@pytest.mark.anyio
async def test_standalone_stdio_serves_skills_end_to_end(tmp_path: "Path") -> "None":
    mcp = _skills_mcp(tmp_path)

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(1),
            _request("skills/list", request_id=2),
            _request("skills/get", request_id=3, params={"uri": "skill://demo/SKILL.md"}),
            _request("resources/directory/read", request_id=4, params={"uri": "skill://demo"}),
            _request("resources/read", request_id=5, params={"uri": "skill://demo/SKILL.md"}),
            _request("resources/read", request_id=6, params={"uri": "skill://demo/scripts/run.py"}),
        ],
    )
    by_id = {response["id"]: response for response in responses}

    discover_result = by_id[1]["result"]
    assert discover_result["capabilities"]["extensions"] == {"io.modelcontextprotocol/skills": {"directoryRead": True}}
    assert "resources" in discover_result["capabilities"]

    list_result = by_id[2]["result"]
    assert len(list_result["skills"]) == 1
    skill_entry = list_result["skills"][0]
    assert skill_entry["uri"] == "skill://demo/SKILL.md"
    assert len(skill_entry["resources"]) == 2
    assert list_result["ttlMs"] == 0
    assert list_result["cacheScope"] == "private"

    get_result = by_id[3]["result"]
    assert get_result["skill"] == skill_entry
    assert get_result["ttlMs"] == 0
    assert get_result["cacheScope"] == "private"

    directory_result = by_id[4]["result"]
    assert "ttlMs" not in directory_result
    assert "cacheScope" not in directory_result
    directory_entries = {entry["uri"]: entry for entry in directory_result["resources"]}
    assert directory_entries["skill://demo/SKILL.md"]["mimeType"] == "text/markdown"
    assert directory_entries["skill://demo/scripts"]["mimeType"] == "inode/directory"

    read_by_uri = {"skill://demo/SKILL.md": by_id[5], "skill://demo/scripts/run.py": by_id[6]}
    for manifest_entry in skill_entry["resources"]:
        content = read_by_uri[manifest_entry["uri"]]["result"]["contents"][0]
        data = content["text"].encode() if "text" in content else base64.b64decode(content["blob"])
        assert len(data) == manifest_entry["size"]
        assert f"sha256:{hashlib.sha256(data).hexdigest()}" == manifest_entry["digest"]


@pytest.mark.anyio
async def test_standalone_stdio_skill_errors(tmp_path: "Path") -> "None":
    mcp = _skills_mcp(tmp_path)

    responses = await _run_stdio_exchange(
        mcp,
        [
            _request("skills/get", request_id=1, params={"uri": "skill://missing/SKILL.md"}),
            _request("resources/read", request_id=2, params={"uri": "skill://demo/nope.md"}),
            _request("resources/directory/read", request_id=3, params={"uri": "skill://demo/"}),
        ],
    )
    by_id = {response["id"]: response for response in responses}

    assert by_id[1]["error"]["code"] == -32602
    assert by_id[2]["error"]["code"] == -32602
    assert by_id[3]["error"]["code"] == -32602


@pytest.mark.anyio
async def test_standalone_stdio_skills_disabled_is_method_not_found() -> "None":
    mcp = MCP(name="no-skills")

    responses = await _run_stdio_exchange(
        mcp,
        [
            _request("skills/list", request_id=1),
            _request("resources/directory/read", request_id=2, params={"uri": "skill://demo"}),
        ],
    )
    by_id = {response["id"]: response for response in responses}

    assert by_id[1]["error"]["code"] == -32601
    assert by_id[2]["error"]["code"] == -32601
