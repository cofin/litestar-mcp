"""MCP schema definitions for Litestar MCP Plugin."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPResource:
    """An MCP resource that can be read by clients.

    Attributes:
        uri: The URI of this resource.
        name: The programmatic name of the resource.
        description: A description of what this resource represents.
        mime_type: The MIME type of this resource.
    """

    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


@dataclass
class MCPTool:
    """An MCP tool that can be called by clients.

    Attributes:
        name: The programmatic name of the tool.
        description: A human-readable description of the tool.
        input_schema: A JSON Schema object defining the expected parameters for the tool.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerCapabilities:
    """MCP server capabilities.

    Attributes:
        resources: Resource capabilities.
        tools: Tool capabilities.
    """

    resources: dict[str, bool] = field(default_factory=dict)
    tools: dict[str, bool] = field(default_factory=dict)
