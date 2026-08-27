"""Unit test verifying 100% backward-compatible top-level re-exports from litestar_mcp."""

import litestar_mcp


def test_top_level_mcp_exports() -> None:
    """Verify all public MCP symbols are directly importable from litestar_mcp."""
    from litestar_mcp import (
        MCP,
        AfterToolCallHook,
        BeforeToolCallHook,
        BridgeConnectionError,
        BridgeMessageTooLargeError,
        DefaultJWKSCache,
        DescriptionSources,
        InMemoryTaskStore,
        JWKSCache,
        LitestarMCP,
        LitestarMCPError,
        MCPAuthBackend,
        MCPAuthConfig,
        MCPBlobResource,
        MCPConfig,
        MCPController,
        MCPHandlerResponse,
        MCPHandlerService,
        MCPInputRequiredResult,
        MCPOptKeys,
        MCPPlugin,
        MCPRequestContext,
        MCPResourceLink,
        MCPStdioContext,
        MCPTaskConfig,
        MCPTaskStore,
        MCPToolResult,
        MetadataRegistry,
        MissingDependencyError,
        OIDCProviderConfig,
        TokenValidator,
        bridge,
        create_oidc_validator,
        get_handler_function,
        get_mcp_metadata,
        get_mcp_request_context,
        mcp_prompt,
        mcp_resource,
        mcp_tool,
        prompt,
        resource,
        run_stdio_streamable_http_bridge,
        tool,
    )

    assert MCP is not None
    assert LitestarMCP is not None
    assert MCPPlugin is LitestarMCP
    assert tool is mcp_tool
    assert resource is mcp_resource
    assert prompt is mcp_prompt
    assert MCPConfig is not None
    assert MCPTaskConfig is not None
    assert AfterToolCallHook is not None
    assert BeforeToolCallHook is not None
    assert MCPHandlerResponse is not None
    assert TokenValidator is not None
    assert MCPOptKeys is not None
    assert MCPBlobResource is not None
    assert MCPInputRequiredResult is not None
    assert MCPResourceLink is not None
    assert MCPToolResult is not None
    assert MCPController is not None
    assert MCPHandlerService is not None
    assert MCPRequestContext is not None
    assert get_mcp_request_context is not None
    assert MetadataRegistry is not None
    assert DescriptionSources is not None
    assert get_handler_function is not None
    assert get_mcp_metadata is not None
    assert bridge is not None
    assert BridgeConnectionError is not None
    assert BridgeMessageTooLargeError is not None
    assert run_stdio_streamable_http_bridge is not None
    assert LitestarMCPError is not None
    assert MissingDependencyError is not None
    assert MCPTaskStore is not None
    assert InMemoryTaskStore is MCPTaskStore
    assert MCPAuthBackend is not None
    assert MCPAuthConfig is not None
    assert OIDCProviderConfig is not None
    assert DefaultJWKSCache is not None
    assert JWKSCache is not None
    assert create_oidc_validator is not None
    assert MCPStdioContext is not None


def test_top_level_core_exports() -> None:
    """Verify protocol-agnostic core symbols are directly importable from litestar_mcp."""
    from litestar_mcp import (
        JSONRPCError,
        JSONRPCRequest,
        JSONRPCRouter,
    )

    assert JSONRPCError is not None
    assert JSONRPCRequest is not None
    assert JSONRPCRouter is not None


def test_top_level_a2a_exports() -> None:
    """Verify primary A2A symbols are directly importable from litestar_mcp."""
    from litestar_mcp import (
        A2AConfig,
        A2APlugin,
        Agent,
        AgentCard,
        Artifact,
        Message,
        Task,
        TaskContext,
        a2a_skill,
        skill,
    )

    assert A2AConfig is not None
    assert A2APlugin is not None
    assert Agent is not None
    assert AgentCard is not None
    assert Task is not None
    assert Message is not None
    assert Artifact is not None
    assert TaskContext is not None
    assert a2a_skill is not None
    assert skill is not None


def test_version_exported() -> None:
    """Verify package metadata version is directly exported."""
    from litestar_mcp import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_all_contains_all_exported_symbols() -> None:
    """Verify __all__ is accurate and every symbol in __all__ exists on the module."""
    for symbol_name in litestar_mcp.__all__:
        assert hasattr(litestar_mcp, symbol_name), f"Missing exported symbol: {symbol_name}"
