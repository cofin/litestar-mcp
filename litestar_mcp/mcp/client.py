from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx2
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
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    "MCPClientError",
    "MCPStreamableHTTPClient",
    "mcp_request_headers",
    "prepare_mcp_request",
)

_DEFAULT_CLIENT_INFO = {"name": "litestar-mcp-client", "version": __version__}


def _encode_header_value(value: str) -> str:
    """Encode header value if it contains non-ASCII characters."""
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        import base64

        return f"?utf-8?B?{base64.b64encode(value.encode('utf-8')).decode('ascii')}?="
    else:
        return value


def prepare_mcp_request(
    message: "dict[str, Any]",
    *,
    client_info: "Mapping[str, str] | None" = None,
) -> "dict[str, Any]":
    """Return a copy of message whose params._meta carries protocol version, capabilities, and client info."""
    prepared = dict(message)
    params = dict(prepared.get("params") or {})
    meta = dict(params.get("_meta") or {})
    meta.setdefault("io.modelcontextprotocol/protocolVersion", MCP_PROTOCOL_VERSION)
    meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
    info = dict(client_info) if client_info is not None else dict(_DEFAULT_CLIENT_INFO)
    meta.setdefault("io.modelcontextprotocol/clientInfo", info)
    params["_meta"] = meta
    prepared["params"] = params
    return prepared


def mcp_request_headers(message: "dict[str, Any]") -> "dict[str, str]":
    """Return Accept, Content-Type, MCP-Protocol-Version, Mcp-Method, and Mcp-Name headers for message."""
    method = str(message.get("method", ""))
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        MCP_PROTOCOL_VERSION_HEADER: MCP_PROTOCOL_VERSION,
        MCP_METHOD_HEADER: method,
    }
    params = message.get("params")
    if isinstance(params, dict):
        name_field = MCP_NAME_FIELDS.get(method)
        if name_field is not None and isinstance(params.get(name_field), str):
            headers[MCP_NAME_HEADER] = _encode_header_value(params[name_field])
    return headers


class MCPClientError(LitestarMCPError):
    """A JSON-RPC error or isError tool result returned by an MCP server."""

    def __init__(self, message: "str", *, code: "int | None" = None, data: "Any" = None) -> "None":
        """Initialize MCPClientError with message, code, and optional data."""
        super().__init__(message)
        self.code = code
        self.data = data


class MCPStreamableHTTPClient:
    """Request/response MCP client over Streamable HTTP using httpx2."""

    def __init__(
        self,
        endpoint: "str",
        *,
        client: "httpx2.AsyncClient | None" = None,
        transport: "httpx2.AsyncBaseTransport | None" = None,
        headers: "Mapping[str, str] | None" = None,
        client_info: "Mapping[str, str] | None" = None,
        max_event_size: "int | None" = 1_048_576,
    ) -> "None":
        """Initialize MCPStreamableHTTPClient with endpoint and client configuration."""
        self.endpoint = endpoint
        self._custom_client = client
        self._transport = transport
        self._headers = dict(headers or {})
        self._client_info = dict(client_info) if client_info is not None else dict(_DEFAULT_CLIENT_INFO)
        self._max_event_size = max_event_size
        self._client: httpx2.AsyncClient | None = client

    async def __aenter__(self) -> "Self":
        """Enter client context manager, initializing HTTP client if needed."""
        if self._client is None:
            self._client = httpx2.AsyncClient(headers=self._headers, transport=self._transport)
        return self

    async def __aexit__(self, *exc: object) -> "None":
        """Exit client context manager, closing client only if created locally."""
        if self._client is not None and self._custom_client is None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> "httpx2.AsyncClient":
        """Get or create active HTTP client."""
        if self._client is None:
            self._client = httpx2.AsyncClient(headers=self._headers, transport=self._transport)
        return self._client

    async def request(self, method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
        """Send JSON-RPC request to MCP endpoint and parse response."""
        req_id = uuid4().hex
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        prepared = prepare_mcp_request(message, client_info=self._client_info)
        req_headers = mcp_request_headers(prepared)
        req_headers.update(self._headers)

        http_client = self._get_client()
        response = await http_client.post(
            self.endpoint,
            json=prepared,
            headers=req_headers,
        )

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            event_source = httpx2.EventSource(response, max_event_size=self._max_event_size)
            async for sse_event in event_source:
                if sse_event.data:
                    data = from_json(sse_event.data)
                    if isinstance(data, dict) and data.get("id") == req_id:
                        if "error" in data:
                            err = data["error"]
                            raise MCPClientError(
                                err.get("message", "MCP error"),
                                code=err.get("code"),
                                data=err.get("data"),
                            )
                        result_val = data.get("result", {})
                        return result_val if isinstance(result_val, dict) else {}
            msg = f"No matching SSE response received for request ID {req_id}"
            raise MCPClientError(msg)

        if response.status_code >= httpx2.codes.BAD_REQUEST:
            err_body = None
            try:
                err_body = from_json(response.content)
            except (ValueError, TypeError):
                err_body = None
            if isinstance(err_body, dict) and "error" in err_body:
                err = err_body["error"]
                err_msg = err.get("message", "MCP error")
                raise MCPClientError(
                    err_msg,
                    code=err.get("code"),
                    data=err.get("data"),
                )
            response.raise_for_status()

        body = from_json(response.content)
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            raise MCPClientError(
                err.get("message", "MCP error"),
                code=err.get("code"),
                data=err.get("data"),
            )
        return body.get("result", {}) if isinstance(body, dict) else {}

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return cached list of discovered tools."""
        return getattr(self, "_discovered_tools", [])

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
