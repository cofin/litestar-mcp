"""Primitive-aware MCP JSON-RPC error helpers."""

from typing import Any

from litestar_mcp.executor import MCPToolErrorResult
from litestar_mcp.jsonrpc import INTERNAL_ERROR, JSONRPCError

RESOURCE_NOT_FOUND = -32002


def _tool_error_data(err: MCPToolErrorResult) -> dict[str, Any]:
    return {"statusCode": err.status_code, "content": err.content}


def mcp_error_for_prompt_execution(err: MCPToolErrorResult) -> JSONRPCError:
    """Map prompt handler execution failures to an internal JSON-RPC error."""
    return JSONRPCError(
        code=INTERNAL_ERROR,
        message="Prompt execution failed",
        data=_tool_error_data(err),
    )


def mcp_error_for_resource_not_found(uri: str) -> JSONRPCError:
    """Return the MCP resources/read not-found error."""
    return JSONRPCError(
        code=RESOURCE_NOT_FOUND,
        message="Resource not found",
        data={"uri": uri},
    )


def mcp_error_for_resource_read(err: MCPToolErrorResult | Exception) -> JSONRPCError:
    """Map resource read failures to an internal JSON-RPC error."""
    if isinstance(err, MCPToolErrorResult):
        data = _tool_error_data(err)
    else:
        data = {"error": type(err).__name__, "detail": str(err)}
    return JSONRPCError(
        code=INTERNAL_ERROR,
        message="Resource read failed",
        data=data,
    )
