"""Tests for the MCP Registry."""

import pytest
from litestar.handlers import get
from litestar_mcp.registry import Registry

@pytest.fixture
def registry() -> Registry:
    return Registry()

def test_registry_tool_registration(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"
    
    registry.register_tool("my_tool", my_handler)
    assert "my_tool" in registry.tools
    assert registry.tools["my_tool"] == my_handler

def test_registry_resource_registration(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"
    
    registry.register_resource("my_resource", my_handler)
    assert "my_resource" in registry.resources
    assert registry.resources["my_resource"] == my_handler

def test_registry_metadata_storage(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"
    
    metadata = {"type": "tool", "name": "test"}
    registry.set_metadata(my_handler, metadata)
    assert registry.get_metadata(my_handler) == metadata
