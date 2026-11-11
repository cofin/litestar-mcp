"""Unit tests for MCPStreamableHTTPClient and request helpers."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, cast

import httpx2
import pytest
from litestar import Litestar, get

from litestar_mcp.core.serialization import from_json
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import (
    MCPClientError,
    MCPStreamableHTTPClient,
    _encode_header_value,
    mcp_request_headers,
    prepare_mcp_request,
)
from litestar_mcp.mcp.config import MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.routes import (
    MCP_METHOD_HEADER,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    MCP_SESSION_ID_HEADER,
    decode_mcp_header_value,
)
from litestar_mcp.mcp.service import get_session_store
from litestar_mcp.mcp.skills import Skill
from litestar_mcp.mcp.stdio import ASGIStreamingTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litestar import Request

    from litestar_mcp.mcp.skills import SkillFile


def test_prepare_mcp_request_adds_meta() -> None:
    """prepare_mcp_request adds client meta information to request params."""
    msg = {"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
    prepared = prepare_mcp_request(msg, client_info={"name": "test-client", "version": "1.0"})
    assert "params" in prepared
    meta = prepared["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == MCP_PROTOCOL_VERSION
    assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "test-client"


def test_mcp_request_headers_generation() -> None:
    """mcp_request_headers sets standard headers and encodes tool name."""
    msg = {"jsonrpc": "2.0", "id": "2", "method": "tools/call", "params": {"name": "calculate"}}
    headers = httpx2.Headers(mcp_request_headers(msg))
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["Content-Type"] == "application/json"
    assert headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "calculate"


@tool(description="Multiply two numbers.")
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the product."""
    return a * b


@pytest.mark.anyio
async def test_mcp_client_against_litestar_server() -> None:
    """MCPStreamableHTTPClient lists and executes tools on a LitestarMCP server."""
    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    plugin.register_tool(multiply)
    app = Litestar(plugins=[plugin])

    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(
        endpoint="http://testserver/mcp",
        transport=transport,
    )

    tools = await client.list_tools()
    assert len(tools) >= 1
    tool_names = [t["name"] for t in tools]
    assert "multiply" in tool_names

    result = await client.call_tool("multiply", {"a": 6, "b": 7})
    assert result in (42, "42")


@pytest.mark.anyio
async def test_mcp_client_error_handling() -> None:
    """MCPStreamableHTTPClient raises MCPClientError on unknown tool or error."""
    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    app = Litestar(plugins=[plugin])

    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(
        endpoint="http://testserver/mcp",
        transport=transport,
    )

    with pytest.raises(MCPClientError):
        await client.call_tool("nonexistent_tool", {})


@pytest.mark.parametrize(
    ("raw_value", "expected_encoded"),
    [
        ("calculate", "calculate"),
        ("données_créer", "=?base64?ZG9ubsOpZXNfY3LDqWVy?="),
        ("=?base64?foo?=", "=?base64?PT9iYXNlNjQ/Zm9vPz0=?="),
        ("", "=?base64??="),
        ("bad\x7fname", "=?base64?YmFkf25hbWU=?="),
    ],
)
def test_encode_header_value_round_trips_with_decode_mcp_header_value(
    raw_value: str,
    expected_encoded: str,
) -> None:
    """_encode_header_value uses the =?base64?...?= sentinel and round-trips through decode_mcp_header_value."""
    encoded = _encode_header_value(raw_value)
    assert encoded == expected_encoded
    assert decode_mcp_header_value(encoded) == raw_value


@pytest.mark.anyio
async def test_streamable_http_client_encodes_non_ascii_mcp_name_header_with_base64_sentinel() -> None:
    """MCPStreamableHTTPClient encodes non-ASCII Mcp-Name headers with =?base64?...?= accepted by MCPController."""

    @get("/unicode-tool", mcp_tool="données_créer", sync_to_thread=False)
    def unicode_tool() -> dict[str, str]:
        return {"status": "créé"}

    app = Litestar(route_handlers=[unicode_tool], plugins=[LitestarMCP()])
    headers = mcp_request_headers({"method": "tools/call", "params": {"name": "données_créer"}})
    assert headers["Mcp-Name"] == "=?base64?ZG9ubsOpZXNfY3LDqWVy?="
    assert decode_mcp_header_value(headers["Mcp-Name"]) == "données_créer"

    transport = httpx2.ASGITransport(app=cast("Any", app))
    async with MCPStreamableHTTPClient("http://testserver/mcp", transport=transport) as client:
        result = await client.call_tool("données_créer", {})
    assert from_json(result) == {"status": "créé"}


class _RecordingStreamingTransport(httpx2.AsyncBaseTransport):
    """Wrap `ASGIStreamingTransport` to record outbound method, headers, and body while signaling `GET` readiness."""

    def __init__(self, app: Litestar) -> None:
        self._inner = ASGIStreamingTransport(app)
        self.requests: list[tuple[str, httpx2.Headers, bytes]] = []
        self.get_opened = asyncio.Event()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        self.requests.append((request.method, httpx2.Headers(request.headers), body))
        response = await self._inner.handle_async_request(request)
        if request.method == "GET":
            self.get_opened.set()
        return response


@pytest.mark.parametrize(
    ("protocol_version", "capabilities", "expect_meta"),
    [
        (MCP_PROTOCOL_VERSION, {"extensions": {"io.modelcontextprotocol/tasks": {}}}, True),
        ("2025-11-25", {"roots": {"listChanged": True}}, False),
        ("2025-06-18", None, False),
    ],
)
def test_prepare_mcp_request_respects_protocol_version(
    protocol_version: str,
    capabilities: dict[str, Any] | None,
    expect_meta: bool,
) -> None:
    """prepare_mcp_request enriches 2026-07-28 requests and leaves 2025-* payloads untouched."""
    original = {"jsonrpc": "2.0", "id": "req-1", "method": "tools/list", "params": {"cursor": "c1"}}
    prepared = prepare_mcp_request(
        original,
        client_info={"name": "unit", "version": "2.0"},
        protocol_version=protocol_version,
        capabilities=capabilities,
    )
    assert prepared is not original
    if expect_meta:
        meta = prepared["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == MCP_PROTOCOL_VERSION
        assert meta["io.modelcontextprotocol/clientCapabilities"] == capabilities
        assert meta["io.modelcontextprotocol/clientInfo"] == {"name": "unit", "version": "2.0"}
        assert prepared["params"]["cursor"] == "c1"
    else:
        assert prepared == original
        assert "_meta" not in prepared["params"]


@pytest.mark.parametrize(
    ("protocol_version", "session_id", "expect_modern_headers", "expect_session_id"),
    [
        (MCP_PROTOCOL_VERSION, "ignored-session", True, False),
        ("2025-11-25", "sess-abc", False, True),
        ("2025-11-25", None, False, False),
        ("2025-06-18", "", False, False),
    ],
)
def test_mcp_request_headers_respect_protocol_version_and_session(
    protocol_version: str,
    session_id: str | None,
    expect_modern_headers: bool,
    expect_session_id: bool,
) -> None:
    """mcp_request_headers switches cleanly between 2026 SEP-2575 headers and 2025 Mcp-Session-Id headers."""
    message = {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": "multiply"}}
    headers = mcp_request_headers(message, protocol_version=protocol_version, session_id=session_id)
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["Content-Type"] == "application/json"
    assert headers[MCP_PROTOCOL_VERSION_HEADER] == protocol_version
    if expect_modern_headers:
        assert headers[MCP_METHOD_HEADER] == "tools/call"
        assert headers[MCP_NAME_HEADER] == "multiply"
        assert MCP_SESSION_ID_HEADER not in headers
    else:
        assert MCP_METHOD_HEADER not in headers
        assert MCP_NAME_HEADER not in headers
        if expect_session_id:
            assert headers[MCP_SESSION_ID_HEADER] == session_id
        else:
            assert MCP_SESSION_ID_HEADER not in headers


@pytest.mark.parametrize("unsupported_version", ["1999-01-01", "", "2024-11-05"])
def test_mcp_client_rejects_unsupported_protocol_version(unsupported_version: str) -> None:
    """MCPStreamableHTTPClient raises ValueError on unsupported protocol_version."""
    with pytest.raises(ValueError, match="Unsupported MCP protocol version"):
        MCPStreamableHTTPClient("http://testserver/mcp", protocol_version=unsupported_version)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("protocol_version", "expect_session_id", "expect_delete_on_exit"),
    [
        ("2025-11-25", True, True),
        ("2025-06-18", True, True),
        ("2026-07-28", False, False),
    ],
)
async def test_mcp_client_lifecycle_across_versions(
    protocol_version: str,
    expect_session_id: bool,
    expect_delete_on_exit: bool,
) -> None:
    """MCPStreamableHTTPClient handles initialize, session propagation, and DELETE cleanup per protocol version."""
    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    plugin.register_tool(multiply)
    app = Litestar(plugins=[plugin])
    transport = _RecordingStreamingTransport(app)

    captured_sid: str | None = None
    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=transport,
            protocol_version=protocol_version,
            capabilities={"roots": {"listChanged": True}},
        ) as client,
    ):
        assert client.capabilities == {"roots": {"listChanged": True}}
        init_result = await client.initialize()
        captured_sid = client.session_id
        assert client.server_capabilities
        assert client.server_info.get("name")

        tools = await client.list_tools()
        assert any(entry["name"] == "multiply" for entry in tools)
        assert client.tools == tools

        product = await client.call_tool("multiply", {"a": 3, "b": 9})
        assert product in (27, "27")

        if expect_session_id:
            assert isinstance(captured_sid, str) and bool(captured_sid)
            assert init_result["protocolVersion"] == protocol_version
            assert init_result["capabilities"]["resources"]["subscribe"] is True
        else:
            assert captured_sid is None
            assert "subscribe" not in init_result["capabilities"]["resources"]

    assert client.session_id is None
    methods = [method for method, _hdrs, _body in transport.requests]
    if expect_delete_on_exit:
        assert "GET" not in methods
        assert methods.count("DELETE") == 1
        delete_method, delete_headers, _ = transport.requests[-1]
        assert delete_method == "DELETE"
        assert delete_headers["mcp-session-id"] == captured_sid
        assert delete_headers["mcp-protocol-version"] == protocol_version
        post_bodies = [from_json(body) for method, _, body in transport.requests if method == "POST"]
        assert [entry["method"] for entry in post_bodies[:3]] == [
            "initialize",
            "notifications/initialized",
            "tools/list",
        ]
        assert "id" not in post_bodies[1]
        _, tools_list_headers, tools_list_raw = transport.requests[2]
        assert tools_list_headers["mcp-session-id"] == captured_sid
        assert "mcp-method" not in tools_list_headers
        assert "_meta" not in from_json(tools_list_raw).get("params", {})
        assert captured_sid is not None
        assert await get_session_store(app, plugin.config).get(captured_sid, None) is None
    else:
        assert all(method == "POST" for method in methods)
        first_body = from_json(transport.requests[0][2])
        assert first_body["method"] == "server/discover"


@pytest.mark.anyio
async def test_mcp_client_2025_open_event_stream_subscribe_unsubscribe_and_terminate() -> None:
    """MCPStreamableHTTPClient consumes 2025-11-25 GET SSE notifications over ASGIStreamingTransport."""

    @get("/items/current", mcp_resource="item", sync_to_thread=False)
    def current_item() -> dict[str, str]:
        return {"text": "v1"}

    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    app = Litestar(route_handlers=[current_item], plugins=[plugin])
    transport = _RecordingStreamingTransport(app)

    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=transport,
            protocol_version="2025-11-25",
        ) as client,
    ):
        await client.initialize()
        assert client.session_id is not None

        received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _consume() -> None:
            async for frame in client.open_event_stream():
                await received.put(frame)

        stream_task = asyncio.create_task(_consume())
        try:
            await asyncio.wait_for(transport.get_opened.wait(), timeout=2.0)

            sub_res = await client.subscribe_resource("litestar://item")
            assert isinstance(sub_res, dict)

            await plugin.registry.notify_resource_updated("litestar://other")
            await plugin.registry.notify_resource_updated("litestar://item")

            first = await asyncio.wait_for(received.get(), timeout=1.0)
            assert first == {
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {"uri": "litestar://item"},
            }
            assert "_meta" not in first["params"]

            unsub_res = await client.unsubscribe_resource("litestar://item")
            assert unsub_res.get("resultType", "complete") == "complete"

            await plugin.registry.notify_resource_updated("litestar://item")
            await plugin.registry.notify_resources_list_changed()

            after_unsub = await asyncio.wait_for(received.get(), timeout=1.0)
            assert after_unsub["method"] == "notifications/resources/list_changed"

            await client.terminate_session()
            assert cast("Any", client).session_id is None
        finally:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task

    delete_calls = [m for m, _, _ in transport.requests if m == "DELETE"]
    assert len(delete_calls) == 1


@pytest.mark.anyio
async def test_mcp_client_open_event_stream_rejects_modern_2026_and_405_disabled_sse() -> None:
    """open_event_stream raises MCPClientError without HTTP GET on 2026-07-28 and surfaces 405 on disabled GET SSE."""
    modern_plugin = LitestarMCP(MCPConfig(base_path="/mcp"))
    modern_app = Litestar(plugins=[modern_plugin])
    modern_transport = _RecordingStreamingTransport(modern_app)

    async with MCPStreamableHTTPClient("http://testserver/mcp", transport=modern_transport) as modern_client:
        with pytest.raises(MCPClientError, match="2026-07-28"):
            async for _ in modern_client.open_event_stream():
                pass
    assert modern_transport.requests == []

    no_sse_plugin = LitestarMCP(MCPConfig(base_path="/mcp", enable_get_sse=False))
    no_sse_app = Litestar(plugins=[no_sse_plugin])
    async with (
        no_sse_app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=ASGIStreamingTransport(no_sse_app),
            protocol_version="2025-11-25",
        ) as legacy_client,
    ):
        await legacy_client.initialize()
        with pytest.raises(MCPClientError, match="405"):
            async for _ in legacy_client.open_event_stream():
                pass


@pytest.mark.parametrize(
    ("protocol_version", "log_level", "preset_meta", "expect_meta", "expected_log_level"),
    [
        (MCP_PROTOCOL_VERSION, "warning", None, True, "warning"),
        (MCP_PROTOCOL_VERSION, "warning", {"io.modelcontextprotocol/logLevel": "error"}, True, "error"),
        (MCP_PROTOCOL_VERSION, None, None, True, None),
        ("2025-11-25", "warning", None, False, None),
    ],
)
def test_prepare_mcp_request_log_level_and_capabilities(
    protocol_version: str,
    log_level: str | None,
    preset_meta: dict[str, Any] | None,
    expect_meta: bool,
    expected_log_level: str | None,
) -> None:
    """prepare_mcp_request preserves clientCapabilities and injects logLevel only on 2026-07-28."""
    params: dict[str, Any] = {"name": "multiply", "arguments": {"a": 1, "b": 2}}
    if preset_meta is not None:
        params["_meta"] = dict(preset_meta)
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": "req-log", "method": "tools/call", "params": params}
    prepared = prepare_mcp_request(
        message,
        client_info={"name": "log-client", "version": "1.0"},
        protocol_version=protocol_version,
        capabilities={"roots": {}},
        log_level=log_level,
    )
    if not expect_meta:
        assert prepared == message
        assert "_meta" not in prepared["params"]
        return

    meta = prepared["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == MCP_PROTOCOL_VERSION
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {"roots": {}}
    assert meta["io.modelcontextprotocol/clientInfo"] == {"name": "log-client", "version": "1.0"}
    if expected_log_level is None:
        assert "io.modelcontextprotocol/logLevel" not in meta
    else:
        assert meta["io.modelcontextprotocol/logLevel"] == expected_log_level


@pytest.mark.anyio
@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2025-06-18", "2025-03-26"])
async def test_mcp_client_set_logging_level_legacy_sessions(protocol_version: str) -> None:
    """MCPStreamableHTTPClient.set_logging_level configures RFC 5424 severity on 2025-* sessions."""
    plugin = LitestarMCP(MCPConfig(base_path="/mcp", default_log_level="info"))
    app = Litestar(plugins=[plugin])
    transport = _RecordingStreamingTransport(app)

    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=transport,
            protocol_version=protocol_version,
        ) as client,
    ):
        init_res = await client.initialize()
        assert init_res["capabilities"]["logging"] == {}
        assert client.server_capabilities["logging"] == {}

        res = await client.set_logging_level("notice")
        assert isinstance(res, dict)
        assert res.get("resultType", "complete") == "complete"

        assert client.session_id is not None
        stored = await get_session_store(app, plugin.config).get(client.session_id, None)
        assert stored is not None
        assert stored.log_level == "notice"

        with pytest.raises(MCPClientError) as exc_info:
            await client.set_logging_level("invalid")
        assert exc_info.value.code == -32602

    disabled_app = Litestar(plugins=[LitestarMCP(MCPConfig(base_path="/mcp", logging=False))])
    async with (
        disabled_app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=_RecordingStreamingTransport(disabled_app),
            protocol_version=protocol_version,
        ) as disabled_client,
    ):
        disabled_init = await disabled_client.initialize()
        assert "logging" not in disabled_init["capabilities"]
        with pytest.raises(MCPClientError) as disabled_exc:
            await disabled_client.set_logging_level("info")
        assert disabled_exc.value.code == -32601


@pytest.mark.anyio
async def test_mcp_client_set_logging_level_rejected_in_modern_2026_mode() -> None:
    """MCPStreamableHTTPClient.set_logging_level receives -32601 on 2026-07-28 stateless servers."""
    plugin = LitestarMCP(MCPConfig(base_path="/mcp", default_log_level="info"))
    app = Litestar(plugins=[plugin])
    transport = _RecordingStreamingTransport(app)

    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver/mcp",
            transport=transport,
            protocol_version=MCP_PROTOCOL_VERSION,
        ) as client,
    ):
        init_res = await client.initialize()
        assert init_res["capabilities"]["logging"] == {}
        assert client.server_capabilities["logging"] == {}

        with pytest.raises(MCPClientError, match="Method not found: logging/setLevel") as exc_info:
            await client.set_logging_level("notice")
        assert exc_info.value.code == -32601


class _StaticSkillSource:
    """In-memory SkillSource serving pre-built Skill definitions."""

    def __init__(self, skills: "Sequence[Skill]") -> None:
        self._skills = list(skills)
        self._by_name = {skill.name: skill for skill in skills}

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        del request
        return self._skills

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        del request
        return self._by_name.get(name)

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        del request, skill
        if file.content is None:
            msg = f"Missing inline content for {file.uri}"
            raise LookupError(msg)
        return file.content


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tags", "query", "expected_names"),
    [
        (None, None, ["alpha", "beta"]),
        (["ops"], None, ["alpha"]),
        (None, "bet", ["beta"]),
    ],
)
async def test_mcp_client_list_skills_paginated_and_filtered(
    tags: "Sequence[str] | None",
    query: "str | None",
    expected_names: "list[str]",
) -> None:
    """MCPStreamableHTTPClient.list_skills paginates across cursors and applies tags/query filters."""
    skills = [
        Skill.build("alpha", "Alpha operations skill", tags=["ops"]),
        Skill.build("beta", "Beta documentation skill", tags=["docs"]),
    ]
    plugin = LitestarMCP(
        config=MCPConfig(
            base_path="/mcp",
            list_page_size=1,
            skills=MCPSkillsConfig(sources=[_StaticSkillSource(skills)]),
        )
    )
    app = Litestar(plugins=[plugin])
    transport = httpx2.ASGITransport(app=cast("Any", app))

    async with app.lifespan(), MCPStreamableHTTPClient("http://testserver/mcp", transport=transport) as client:
        entries = await client.list_skills(tags=tags, query=query)

    assert [entry["frontmatter"]["name"] for entry in entries] == expected_names


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("uri", "expected_name", "expected_code"),
    [
        ("skill://alpha/SKILL.md", "alpha", None),
        ("skill://alpha", "alpha", None),
        ("skill://nope/SKILL.md", None, -32602),
    ],
)
async def test_mcp_client_get_skill_and_errors(
    uri: str,
    expected_name: str | None,
    expected_code: int | None,
) -> None:
    """MCPStreamableHTTPClient.get_skill unwraps skill entries and raises MCPClientError on missing skills."""
    skills = [Skill.build("alpha", "Alpha operations skill", tags=["ops"])]
    plugin = LitestarMCP(
        config=MCPConfig(
            base_path="/mcp",
            skills=MCPSkillsConfig(sources=[_StaticSkillSource(skills)]),
        )
    )
    app = Litestar(plugins=[plugin])
    transport = httpx2.ASGITransport(app=cast("Any", app))

    async with app.lifespan(), MCPStreamableHTTPClient("http://testserver/mcp", transport=transport) as client:
        if expected_code is None:
            skill = await client.get_skill(uri)
            assert skill["frontmatter"]["name"] == expected_name
        else:
            with pytest.raises(MCPClientError) as exc_info:
                await client.get_skill(uri)
            assert exc_info.value.code == expected_code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("directory_read", "uri", "expected_names", "expected_code"),
    [
        (True, "skill://alpha", ["SKILL.md", "references", "scripts"], None),
        (True, "skill://alpha/missing", None, -32602),
        (False, "skill://alpha", None, -32601),
    ],
)
async def test_mcp_client_read_skill_directory(
    directory_read: bool,
    uri: str,
    expected_names: list[str] | None,
    expected_code: int | None,
) -> None:
    """MCPStreamableHTTPClient.read_skill_directory paginates directory entries and surfaces protocol errors."""
    skills = [
        Skill.build(
            "alpha",
            "Alpha operations skill",
            files={
                "references/guide.md": "# Guide",
                "scripts/run.sh": "echo ok",
            },
        )
    ]
    plugin = LitestarMCP(
        config=MCPConfig(
            base_path="/mcp",
            list_page_size=1,
            skills=MCPSkillsConfig(sources=[_StaticSkillSource(skills)], directory_read=directory_read),
        )
    )
    app = Litestar(plugins=[plugin])
    transport = httpx2.ASGITransport(app=cast("Any", app))

    async with app.lifespan(), MCPStreamableHTTPClient("http://testserver/mcp", transport=transport) as client:
        if expected_code is None:
            entries = await client.read_skill_directory(uri)
            assert [entry["name"] for entry in entries] == expected_names
        else:
            with pytest.raises(MCPClientError) as exc_info:
                await client.read_skill_directory(uri)
            assert exc_info.value.code == expected_code
