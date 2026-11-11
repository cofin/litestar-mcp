"""Async Streamable HTTP client and request-header helpers for Litestar MCP endpoints."""

import base64
import contextlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx2
from litestar.exceptions import SerializationException
from typing_extensions import Self

from litestar_mcp.__metadata__ import __version__
from litestar_mcp.core.exceptions import LitestarMCPError
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.routes import (
    MCP_METHOD_HEADER,
    MCP_NAME_FIELDS,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    MCP_SESSION_ID_HEADER,
    SUPPORTED_PROTOCOL_VERSIONS,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

__all__ = (
    "MCPClientError",
    "MCPStreamableHTTPClient",
    "mcp_request_headers",
    "prepare_mcp_request",
)

_DEFAULT_CLIENT_INFO = {"name": "litestar-mcp-client", "version": __version__}
_MIN_VISIBLE_ASCII = 0x20
_MAX_VISIBLE_ASCII = 0x7E


def _encode_header_value(value: str) -> str:
    """Encode header value with the MCP `=?base64?...` sentinel when non-visible-ASCII or colliding."""
    if (
        value
        and all(_MIN_VISIBLE_ASCII <= ord(char) <= _MAX_VISIBLE_ASCII for char in value)
        and not value.startswith("=?base64?")
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="


def prepare_mcp_request(
    message: "dict[str, Any]",
    *,
    client_info: "Mapping[str, str] | None" = None,
    protocol_version: "str" = MCP_PROTOCOL_VERSION,
    capabilities: "Mapping[str, Any] | None" = None,
    log_level: "str | None" = None,
) -> "dict[str, Any]":
    """Return a copy of message enriched with `2026-07-28` `_meta` when targeting modern transport."""
    prepared = dict(message)
    if protocol_version != MCP_PROTOCOL_VERSION:
        return prepared
    params = dict(prepared.get("params") or {})
    meta = dict(params.get("_meta") or {})
    meta.setdefault("io.modelcontextprotocol/protocolVersion", protocol_version)
    default_capabilities = dict(capabilities) if capabilities is not None else {}
    meta.setdefault("io.modelcontextprotocol/clientCapabilities", default_capabilities)
    info = dict(client_info) if client_info is not None else dict(_DEFAULT_CLIENT_INFO)
    meta.setdefault("io.modelcontextprotocol/clientInfo", info)
    if log_level is not None:
        meta.setdefault("io.modelcontextprotocol/logLevel", log_level)
    params["_meta"] = meta
    prepared["params"] = params
    return prepared


def mcp_request_headers(
    message: "dict[str, Any]",
    *,
    protocol_version: "str" = MCP_PROTOCOL_VERSION,
    session_id: "str | None" = None,
) -> "dict[str, str]":
    """Return Streamable HTTP headers for `message` under `protocol_version`."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        MCP_PROTOCOL_VERSION_HEADER: protocol_version,
    }
    if protocol_version != MCP_PROTOCOL_VERSION:
        if session_id:
            headers[MCP_SESSION_ID_HEADER] = session_id
        return headers
    method = str(message.get("method", ""))
    headers[MCP_METHOD_HEADER] = method
    params = message.get("params")
    if isinstance(params, dict):
        name_field = MCP_NAME_FIELDS.get(method)
        if name_field is not None and isinstance(params.get(name_field), str):
            headers[MCP_NAME_HEADER] = _encode_header_value(params[name_field])
    return headers


class MCPClientError(LitestarMCPError):
    """A JSON-RPC error or isError tool result returned by an MCP server."""

    code: "int | None"
    """Optional JSON-RPC 2.0 numeric error code returned by the server."""
    data: Any
    """Optional structured error payload or raw tool result data."""

    def __init__(self, message: "str", *, code: "int | None" = None, data: "Any" = None) -> "None":
        """Initialize MCPClientError with message, code, and optional data."""
        super().__init__(message)
        self.code = code
        self.data = data


class MCPStreamableHTTPClient:
    """Request/response and SSE MCP client over Streamable HTTP using httpx2."""

    endpoint: str
    """Target MCP Streamable HTTP endpoint URL."""
    protocol_version: str
    """Active MCP protocol version negotiated or requested by this client."""
    server_capabilities: dict[str, Any]
    """Server capabilities returned by the most recent discovery or initialization handshake."""
    server_info: dict[str, Any]
    """Server implementation metadata returned by discovery or initialization."""

    def __init__(
        self,
        endpoint: "str",
        *,
        client: "httpx2.AsyncClient | None" = None,
        transport: "httpx2.AsyncBaseTransport | None" = None,
        headers: "Mapping[str, str] | None" = None,
        client_info: "Mapping[str, str] | None" = None,
        max_event_size: "int | None" = 1_048_576,
        protocol_version: "str" = MCP_PROTOCOL_VERSION,
        capabilities: "Mapping[str, Any] | None" = None,
    ) -> "None":
        """Initialize MCPStreamableHTTPClient with endpoint, transport, and protocol version."""
        if not isinstance(protocol_version, str) or protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            msg = f"Unsupported MCP protocol version: {protocol_version}"
            raise ValueError(msg)
        self.endpoint = endpoint
        self.protocol_version = protocol_version
        self.server_capabilities = {}
        self.server_info = {}
        self._custom_client = client
        self._transport = transport
        self._headers = dict(headers or {})
        self._client_info = dict(client_info) if client_info is not None else dict(_DEFAULT_CLIENT_INFO)
        self._capabilities = dict(capabilities) if capabilities is not None else {}
        self._max_event_size = max_event_size
        self._client: httpx2.AsyncClient | None = client
        self._session_id: str | None = None
        self._discovered_tools: list[dict[str, Any]] = []

    async def __aenter__(self) -> "Self":
        """Enter client context manager, initializing HTTP client if needed."""
        if self._client is None:
            self._client = httpx2.AsyncClient(headers=self._headers, transport=self._transport)
        return self

    async def __aexit__(self, *exc: object) -> "None":
        """Exit client context manager, terminating any active session and closing locally owned client."""
        try:
            if self._session_id is not None:
                with contextlib.suppress(httpx2.HTTPError, MCPClientError, RuntimeError):
                    await self.terminate_session()
        finally:
            if self._client is not None and self._custom_client is None:
                await self._client.aclose()
                self._client = None

    @property
    def session_id(self) -> "str | None":
        """Return the active `Mcp-Session-Id` assigned by a `2025-*` server, if any."""
        return self._session_id

    @property
    def capabilities(self) -> "dict[str, Any]":
        """Return a shallow copy of configured client capabilities."""
        return dict(self._capabilities)

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return cached list of discovered tools."""
        return self._discovered_tools

    async def initialize(self, *, capabilities: "Mapping[str, Any] | None" = None) -> "dict[str, Any]":
        """Perform `server/discover` (`2026-07-28`) or `initialize` + `notifications/initialized` (`2025-*`)."""
        if capabilities is not None:
            self._capabilities = dict(capabilities)
        if self.protocol_version == MCP_PROTOCOL_VERSION:
            result = await self.request("server/discover", {})
            caps = result.get("capabilities")
            self.server_capabilities = dict(caps) if isinstance(caps, dict) else {}
            meta = result.get("_meta")
            raw_info = meta.get("io.modelcontextprotocol/serverInfo") if isinstance(meta, dict) else None
            self.server_info = dict(raw_info) if isinstance(raw_info, dict) else {}
            return result

        result = await self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": dict(self._capabilities),
                "clientInfo": dict(self._client_info),
            },
        )
        negotiated = result.get("protocolVersion")
        if isinstance(negotiated, str) and negotiated in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = negotiated
        caps = result.get("capabilities")
        self.server_capabilities = dict(caps) if isinstance(caps, dict) else {}
        raw_info = result.get("serverInfo")
        self.server_info = dict(raw_info) if isinstance(raw_info, dict) else {}
        await self.notify("notifications/initialized")
        return result

    async def notify(self, method: "str", params: "dict[str, Any] | None" = None) -> "None":
        """Send a JSON-RPC 2.0 client notification without a request correlation `id`."""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        prepared = prepare_mcp_request(
            message,
            client_info=self._client_info,
            protocol_version=self.protocol_version,
            capabilities=self._capabilities,
        )
        req_headers = mcp_request_headers(
            prepared,
            protocol_version=self.protocol_version,
            session_id=self._session_id,
        )
        req_headers.update(self._headers)
        response = await self._get_client().post(self.endpoint, json=prepared, headers=req_headers)
        if response.status_code >= httpx2.codes.BAD_REQUEST:
            self._raise_for_error_response(response)

    async def request(self, method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
        """Send JSON-RPC request to MCP endpoint and parse response."""
        req_id = uuid4().hex
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": dict(params) if params is not None else {},
        }
        prepared = prepare_mcp_request(
            message,
            client_info=self._client_info,
            protocol_version=self.protocol_version,
            capabilities=self._capabilities,
        )
        active_session = None if method == "initialize" else self._session_id
        req_headers = mcp_request_headers(
            prepared,
            protocol_version=self.protocol_version,
            session_id=active_session,
        )
        req_headers.update(self._headers)

        response = await self._get_client().post(
            self.endpoint,
            json=prepared,
            headers=req_headers,
        )
        if response.status_code == httpx2.codes.ACCEPTED:
            return {}

        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            result = await self._parse_sse_result(response, req_id)
        else:
            if response.status_code >= httpx2.codes.BAD_REQUEST:
                self._raise_for_error_response(response)
            result = self._extract_jsonrpc_result(from_json(response.content))

        if method == "initialize" and self.protocol_version != MCP_PROTOCOL_VERSION:
            raw_sid = response.headers.get(MCP_SESSION_ID_HEADER)
            self._session_id = raw_sid.strip() if isinstance(raw_sid, str) and raw_sid.strip() else None
        return result

    async def open_event_stream(self) -> "AsyncIterator[dict[str, Any]]":
        """Open a `GET` SSE server-notification stream for an active `2025-*` protocol mode."""
        if self.protocol_version == MCP_PROTOCOL_VERSION:
            msg = f"GET SSE event streams are not supported for protocol version {MCP_PROTOCOL_VERSION}"
            raise MCPClientError(msg)
        req_headers: dict[str, str] = {
            **self._headers,
            "Accept": "text/event-stream",
            MCP_PROTOCOL_VERSION_HEADER: self.protocol_version,
        }
        if self._session_id:
            req_headers[MCP_SESSION_ID_HEADER] = self._session_id
        async with self._get_client().stream("GET", self.endpoint, headers=req_headers) as response:
            if response.status_code >= httpx2.codes.BAD_REQUEST:
                raw_body = await response.aread()
                self._raise_for_sse_error(response.status_code, raw_body)
            event_source = httpx2.EventSource(response, max_event_size=self._max_event_size)
            async for sse_event in event_source:
                if not sse_event.data:
                    continue
                payload = from_json(sse_event.data)
                if isinstance(payload, dict):
                    yield payload

    async def subscribe_resource(self, uri: "str") -> "dict[str, Any]":
        """Subscribe the active `2025-*` session to updates for `uri`."""
        return await self.request("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: "str") -> "dict[str, Any]":
        """Unsubscribe the active `2025-*` session from updates for `uri`."""
        return await self.request("resources/unsubscribe", {"uri": uri})

    async def set_logging_level(self, level: "str") -> "dict[str, Any]":
        """Set the server's minimum RFC 5424 log severity via `logging/setLevel`."""
        return await self.request("logging/setLevel", {"level": level})

    async def terminate_session(self) -> "None":
        """Terminate the active `Mcp-Session-Id` session via HTTP `DELETE`."""
        session_id = self._session_id
        if session_id is None:
            return
        self._session_id = None
        req_headers: dict[str, str] = {
            **self._headers,
            MCP_PROTOCOL_VERSION_HEADER: self.protocol_version,
            MCP_SESSION_ID_HEADER: session_id,
        }
        response = await self._get_client().delete(self.endpoint, headers=req_headers)
        if response.status_code in {httpx2.codes.NOT_FOUND, httpx2.codes.METHOD_NOT_ALLOWED}:
            return
        if response.status_code >= httpx2.codes.BAD_REQUEST:
            self._raise_for_error_response(response)

    async def list_tools(self) -> "list[dict[str, Any]]":
        """List all remote tools, following pagination cursors."""
        all_tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            res = await self.request("tools/list", params)
            tools = res.get("tools", [])
            if isinstance(tools, list):
                all_tools.extend(tools)
            next_cursor = res.get("nextCursor")
            if isinstance(next_cursor, str) and next_cursor:
                cursor = next_cursor
            else:
                break
        self._discovered_tools = all_tools
        return all_tools

    async def call_tool(self, name: "str", arguments: "Mapping[str, Any] | None" = None) -> "Any":
        """Call a remote tool by name with arguments and extract structured or text content."""
        params = {"name": name, "arguments": dict(arguments or {})}
        res = await self.request("tools/call", params)
        if res.get("isError"):
            content_items = res.get("content", [])
            text_parts = [
                item.get("text", "") for item in content_items if isinstance(item, dict) and item.get("type") == "text"
            ]
            error_message = "\n".join(text_parts) if text_parts else "Tool execution failed"
            raise MCPClientError(error_message, data=res)

        if "structuredContent" in res:
            return res["structuredContent"]

        content = res.get("content")
        if isinstance(content, list):
            text_blocks = [item for item in content if isinstance(item, dict) and item.get("type") == "text"]
            if len(text_blocks) == 1 and len(content) == 1:
                return text_blocks[0].get("text", "")
            return content
        return res

    async def list_skills(
        self,
        *,
        tags: "Sequence[str] | None" = None,
        query: "str | None" = None,
    ) -> "list[dict[str, Any]]":
        """List all remote skills matching optional filters, following pagination cursors."""
        all_skills: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if tags is not None:
                params["tags"] = list(tags)
            if query is not None:
                params["query"] = query
            if cursor:
                params["cursor"] = cursor
            res = await self.request("skills/list", params)
            skills = res.get("skills", [])
            if isinstance(skills, list):
                all_skills.extend(skills)
            next_cursor = res.get("nextCursor")
            if isinstance(next_cursor, str) and next_cursor:
                cursor = next_cursor
            else:
                break
        return all_skills

    async def get_skill(self, uri: "str") -> "dict[str, Any]":
        """Retrieve a remote skill manifest by its ``skill://`` URI."""
        res = await self.request("skills/get", {"uri": uri})
        skill = res.get("skill")
        return skill if isinstance(skill, dict) else res

    async def read_skill_directory(self, uri: "str") -> "list[dict[str, Any]]":
        """List files and subdirectories under a ``skill://`` directory URI, following pagination cursors."""
        all_resources: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"uri": uri}
            if cursor:
                params["cursor"] = cursor
            res = await self.request("resources/directory/read", params)
            resources = res.get("resources", [])
            if isinstance(resources, list):
                all_resources.extend(resources)
            next_cursor = res.get("nextCursor")
            if isinstance(next_cursor, str) and next_cursor:
                cursor = next_cursor
            else:
                break
        return all_resources

    def _get_client(self) -> "httpx2.AsyncClient":
        """Get or create active HTTP client."""
        if self._client is None:
            self._client = httpx2.AsyncClient(headers=self._headers, transport=self._transport)
        return self._client

    async def _parse_sse_result(self, response: "httpx2.Response", req_id: "str") -> "dict[str, Any]":
        event_source = httpx2.EventSource(response, max_event_size=self._max_event_size)
        async for sse_event in event_source:
            if not sse_event.data:
                continue
            data = from_json(sse_event.data)
            if isinstance(data, dict) and data.get("id") == req_id:
                return self._extract_jsonrpc_result(data)
        msg = f"No matching SSE response received for request ID {req_id}"
        raise MCPClientError(msg)

    @staticmethod
    def _extract_jsonrpc_result(body: "Any") -> "dict[str, Any]":
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            if isinstance(err, dict):
                raise MCPClientError(
                    str(err.get("message", "MCP error")),
                    code=err.get("code"),
                    data=err.get("data"),
                )
            raise MCPClientError(str(err))
        if isinstance(body, dict):
            result_val = body.get("result", {})
            return result_val if isinstance(result_val, dict) else {}
        return {}

    @staticmethod
    def _raise_for_error_response(response: "httpx2.Response") -> "None":
        err_body: Any = None
        with contextlib.suppress(SerializationException, ValueError, TypeError):
            err_body = from_json(response.content)
        if isinstance(err_body, dict) and isinstance(err_body.get("error"), dict):
            err = err_body["error"]
            raise MCPClientError(
                str(err.get("message", "MCP error")),
                code=err.get("code"),
                data=err.get("data"),
            )
        response.raise_for_status()

    @staticmethod
    def _raise_for_sse_error(status_code: "int", raw_body: "bytes") -> "None":
        err_body: Any = None
        if raw_body:
            with contextlib.suppress(SerializationException, ValueError, TypeError):
                err_body = from_json(raw_body)
        if isinstance(err_body, dict) and isinstance(err_body.get("error"), dict):
            err = err_body["error"]
            detail = str(err.get("message", "MCP error"))
            msg = f"HTTP {status_code}: {detail}"
            raise MCPClientError(msg, code=err.get("code"), data=err.get("data"))
        msg = f"HTTP {status_code} opening MCP GET SSE stream"
        raise MCPClientError(msg)
