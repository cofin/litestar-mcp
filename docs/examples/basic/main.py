"""Basic Litestar MCP Plugin Example.

This example demonstrates the simplest possible integration of the Litestar
MCP Plugin. It shows how to mark one route as an MCP *tool*, one as an MCP
*resource*, and leave the rest of the API untouched.

MCP is served as JSON-RPC 2.0 over a single ``POST /mcp`` endpoint. Tool
execution and resource reads are JSON-RPC method calls, not REST paths.
"""

import math
from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP


@get("/")
async def root() -> dict[str, str]:
    """Root endpoint — not exposed to MCP."""
    return {"message": "Welcome to the Basic Litestar MCP Plugin Example!"}


@get("/add/{a:int}/{b:int}", mcp_tool="add")
async def add(a: int, b: int) -> dict[str, int]:
    """Add two integers — exposed as MCP tool.

    MCP tools are *executable* operations. Path and query parameters become
    the tool's input schema automatically.
    """
    return {"a": a, "b": b, "result": a + b}


@get("/pi", mcp_resource="pi")
async def pi() -> dict[str, Any]:
    """Return the value of π — exposed as MCP resource.

    MCP resources are *read-only* data that clients can cache. This handler
    has no parameters, which is typical for resources.
    """
    return {"name": "pi", "value": math.pi, "description": "The ratio of a circle's circumference to its diameter."}


@get("/status")
async def status() -> dict[str, str]:
    """Plain health endpoint — not exposed to MCP."""
    return {"status": "healthy", "version": "1.0.0"}


app = Litestar(
    route_handlers=[root, add, pi, status],
    plugins=[LitestarMCP()],  # This enables MCP integration!
)
