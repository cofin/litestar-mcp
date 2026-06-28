from __future__ import annotations

import json

import pytest
from litestar.testing import TestClient

from litestar_mcp import MCP
from tests.integration.conftest import rpc

pytestmark = pytest.mark.integration


def test_standalone_mcp_tool_integration() -> None:
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="echo", description="Echo message")
    def echo_fn(message: str) -> str:
        return message

    with TestClient(app=mcp.app) as client:
        # Check tool list
        response = rpc(client, "tools/list")
        assert "result" in response
        tools = response["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert tools[0]["description"] == "Echo message"

        # Call the tool
        response = rpc(client, "tools/call", {"name": "echo", "arguments": {"message": "hello"}})
        assert response["result"]["isError"] is False
        content = response["result"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        # If the return type is simple string, our plugin wraps it as JSON string or raw text depending on representation.
        # Let's load the text to verify if it's JSON encoded.
        assert content[0]["text"] == "hello"


def test_standalone_mcp_resource_integration() -> None:
    mcp = MCP(name="test-mcp")

    @mcp.resource(uri="file://foo/bar", name="foo", description="Foo file")
    def read_foo() -> str:
        return "foo-content"

    with TestClient(app=mcp.app) as client:
        response = rpc(client, "resources/list")
        assert "result" in response
        resources = response["result"]["resources"]
        assert len(resources) == 2
        foo_resource = next(r for r in resources if r["name"] == "foo")
        assert foo_resource["uri"] == "litestar://foo"

        response = rpc(client, "resources/read", {"uri": "file://foo/bar"})
        assert "result" in response
        contents = response["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "file://foo/bar"
        assert json.loads(contents[0]["text"]) == "foo-content"


def test_standalone_mcp_prompt_integration() -> None:
    mcp = MCP(name="test-mcp")

    @mcp.prompt(name="greet", description="Greet prompt")
    def greet_prompt(name: str) -> str:
        return f"Hello {name}!"

    with TestClient(app=mcp.app) as client:
        response = rpc(client, "prompts/list")
        assert "result" in response
        prompts = response["result"]["prompts"]
        assert len(prompts) == 1
        assert prompts[0]["name"] == "greet"
        assert prompts[0]["description"] == "Greet prompt"

        response = rpc(client, "prompts/get", {"name": "greet", "arguments": {"name": "Alice"}})
        assert "result" in response
        messages = response["result"]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert content["type"] == "text"
        assert content["text"] == "Hello Alice!"
