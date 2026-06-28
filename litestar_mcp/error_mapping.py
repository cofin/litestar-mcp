"""Primitive-aware MCP JSON-RPC error helpers.

Error contract. The JSON-RPC ``error.code`` reflects the *primitive-
level* error class defined by the MCP spec, **not** the handler's HTTP status:

* ``resources/read`` unknown URI -> ``-32002`` (spec-mandated "Resource not found").
* ``resources/read`` handler error (any status) -> ``-32603`` Internal error.
* ``prompts/get`` unknown name / missing / invalid args -> ``-32602`` Invalid params
  (raised pre-execution in ``routes.py``).
* ``prompts/get`` handler execution error (any status) -> ``-32603`` Internal error.
* ``tools/call`` handler error -> no JSON-RPC error object; an ``isError=True``
  result envelope per the tools spec.

The handler's real HTTP status is never dropped: it is preserved in
``error.data.statusCode`` so clients can recover the finer signal without the
server minting non-standard JSON-RPC codes. MCP defines no codes for
401/403/409/429, so none are invented here (this deliberately supersedes
status->code mapping proposals).

RESOURCE_NOT_FOUND is the Spec-mandated resources/read "Resource not found" code
(MCP 2025-06-18, Resources §Error Handling). Note: future spec updates may migrate
this to -32602 (Invalid params).
"""

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
