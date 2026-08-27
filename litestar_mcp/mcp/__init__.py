"""Anthropic Model Context Protocol (MCP) support for Litestar."""

from litestar_mcp.mcp import bridge
from litestar_mcp.mcp.app import MCP, MCPStdioContext
from litestar_mcp.mcp.auth import (
    DefaultJWKSCache,
    JWKSCache,
    MCPAuthBackend,
    MCPAuthConfig,
    OIDCProviderConfig,
    TokenValidator,
    create_oidc_validator,
)
from litestar_mcp.mcp.bridge import (
    BridgeConnectionError,
    BridgeMessageTooLargeError,
    run_stdio_streamable_http_bridge,
)
from litestar_mcp.mcp.config import (
    AfterToolCallHook,
    BeforeToolCallHook,
    MCPConfig,
    MCPOptKeys,
    MCPTaskConfig,
)
from litestar_mcp.mcp.content import (
    MCPBlobResource,
    MCPInputRequiredResult,
    MCPResourceLink,
    MCPToolResult,
    enforce_blob_size,
    is_content_block,
    normalize_content_blocks,
)
from litestar_mcp.mcp.error_mapping import (
    RESOURCE_NOT_FOUND,
    mcp_error_for_prompt_execution,
    mcp_error_for_resource_not_found,
    mcp_error_for_resource_read,
)
from litestar_mcp.mcp.exceptions import (
    LitestarMCPError,
    MissingDependencyError,
)
from litestar_mcp.mcp.executor import (
    MCPHandlerResponse,
    MCPPathParamCoercionError,
    MCPToolErrorResult,
    NotCallableInCLIContextError,
    execute_handler,
    execute_handler_response,
    execute_tool,
)
from litestar_mcp.mcp.manifests import (
    build_agent_card,
    build_oauth_protected_resource,
)
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.registry import (
    PromptRegistration,
    Registry,
    render_prompt_entry,
    resolve_prompt_description,
    should_include_prompt,
)
from litestar_mcp.mcp.routes import (
    MCP_METHOD_HEADER,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    MCPController,
)
from litestar_mcp.mcp.schema import (
    iter_mcp_header_fields,
    validate_mcp_header_schema,
)
from litestar_mcp.mcp.service import (
    MCPHandlerService,
    MCPRequestContext,
    get_mcp_request_context,
)
from litestar_mcp.mcp.sse import (
    StreamLimitExceeded,
    SubscriptionManager,
)
from litestar_mcp.mcp.tasks import (
    InMemoryTaskStore,
    MCPTaskStore,
    TaskLookupError,
    TaskRecord,
    TaskStateError,
)
from litestar_mcp.mcp.utils import (
    DescriptionSources,
    MetadataRegistry,
    expand_template,
    extract_description_sources,
    get_handler_function,
    get_mcp_metadata,
    match_uri,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
    parse_template,
    render_description,
    should_include_handler,
)

MCPPlugin = LitestarMCP
tool = mcp_tool
resource = mcp_resource
prompt = mcp_prompt

__all__ = (
    "MCP",
    "MCP_METHOD_HEADER",
    "MCP_NAME_HEADER",
    "MCP_PROTOCOL_VERSION",
    "MCP_PROTOCOL_VERSION_HEADER",
    "RESOURCE_NOT_FOUND",
    "AfterToolCallHook",
    "BeforeToolCallHook",
    "BridgeConnectionError",
    "BridgeMessageTooLargeError",
    "DefaultJWKSCache",
    "DescriptionSources",
    "InMemoryTaskStore",
    "JWKSCache",
    "LitestarMCP",
    "LitestarMCPError",
    "MCPAuthBackend",
    "MCPAuthConfig",
    "MCPBlobResource",
    "MCPConfig",
    "MCPController",
    "MCPHandlerResponse",
    "MCPHandlerService",
    "MCPInputRequiredResult",
    "MCPOptKeys",
    "MCPPathParamCoercionError",
    "MCPPlugin",
    "MCPRequestContext",
    "MCPResourceLink",
    "MCPStdioContext",
    "MCPTaskConfig",
    "MCPTaskStore",
    "MCPToolErrorResult",
    "MCPToolResult",
    "MetadataRegistry",
    "MissingDependencyError",
    "NotCallableInCLIContextError",
    "OIDCProviderConfig",
    "PromptRegistration",
    "Registry",
    "StreamLimitExceeded",
    "SubscriptionManager",
    "TaskLookupError",
    "TaskRecord",
    "TaskStateError",
    "TokenValidator",
    "bridge",
    "build_agent_card",
    "build_oauth_protected_resource",
    "create_oidc_validator",
    "enforce_blob_size",
    "execute_handler",
    "execute_handler_response",
    "execute_tool",
    "expand_template",
    "extract_description_sources",
    "get_handler_function",
    "get_mcp_metadata",
    "get_mcp_request_context",
    "is_content_block",
    "iter_mcp_header_fields",
    "match_uri",
    "mcp_error_for_prompt_execution",
    "mcp_error_for_resource_not_found",
    "mcp_error_for_resource_read",
    "mcp_prompt",
    "mcp_resource",
    "mcp_tool",
    "normalize_content_blocks",
    "parse_template",
    "prompt",
    "render_description",
    "render_prompt_entry",
    "resolve_prompt_description",
    "resource",
    "run_stdio_streamable_http_bridge",
    "should_include_handler",
    "should_include_prompt",
    "tool",
    "validate_mcp_header_schema",
)
