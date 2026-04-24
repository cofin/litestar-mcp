"""Generated discovery manifests for Litestar MCP."""

from typing import Any

from litestar import Litestar

from litestar_mcp.auth import MCPAuthConfig  # noqa: TC001
from litestar_mcp.config import MCPConfig
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.utils import get_handler_function, get_mcp_metadata, render_description

MCP_PROTOCOL_VERSION = "2025-11-25"


def _server_name(config: MCPConfig, app: Litestar) -> str:
    if config.name:
        return config.name
    if app.openapi_config and app.openapi_config.title:
        return app.openapi_config.title
    return "Litestar MCP Server"


def _server_version(app: Litestar) -> str:
    if app.openapi_config and app.openapi_config.version:
        return app.openapi_config.version
    return "1.0.0"


def build_oauth_protected_resource(auth_config: "MCPAuthConfig | None", app: Litestar) -> dict[str, Any]:
    """Build RFC 9728 protected resource metadata."""
    if auth_config and auth_config.issuer:
        return {
            "resource": auth_config.audience or "",
            "authorization_servers": [auth_config.issuer],
            "scopes_supported": list(auth_config.scopes.keys()) if auth_config.scopes else [],
        }

    openapi_config = app.openapi_config
    if not openapi_config:
        return {"resource": "", "authorization_servers": [], "scopes_supported": []}

    schema = app.openapi_schema
    if not schema.components or not schema.components.security_schemes:
        return {"resource": openapi_config.title or "", "authorization_servers": [], "scopes_supported": []}

    authorization_servers: list[str] = []
    scopes_supported: list[str] = []
    for scheme in schema.components.security_schemes.values():
        flows = getattr(scheme, "flows", None)
        if not flows:
            continue
        for flow_name in ("password", "authorization_code", "client_credentials", "implicit"):
            flow = getattr(flows, flow_name, None)
            if flow is None:
                continue
            if getattr(flow, "token_url", None):
                authorization_servers.append(flow.token_url)
            if getattr(flow, "authorization_url", None):
                authorization_servers.append(flow.authorization_url)
            if getattr(flow, "scopes", None):
                scopes_supported.extend(flow.scopes.keys() if isinstance(flow.scopes, dict) else flow.scopes)

    return {
        "resource": openapi_config.title or "",
        "authorization_servers": list(dict.fromkeys(authorization_servers)),
        "scopes_supported": list(dict.fromkeys(scopes_supported)),
    }


def build_agent_card(
    *,
    base_url: str,
    config: MCPConfig,
    app: Litestar,
    discovered_tools: dict[str, Any],
) -> dict[str, Any]:
    """Build an A2A-style agent card for MCP discovery."""
    skills = []
    for name, handler in discovered_tools.items():
        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
        skills.append(
            {
                "id": name,
                "name": name,
                "description": render_description(
                    handler, fn, kind="tool", fallback_name=name, opt_keys=config.opt_keys
                ),
                "tags": sorted(getattr(handler, "tags", []) or []),
                "examples": metadata.get("examples", []),
            }
        )

    return {
        "protocolVersion": "0.2.6",
        "name": _server_name(config, app),
        "description": f"A Litestar-native MCP integration for {_server_name(config, app)}.",
        "version": _server_version(app),
        "url": f"{base_url.rstrip('/')}{config.base_path}",
        "capabilities": {
            "streaming": True,
            "mcp": True,
            "tasks": config.task_config is not None,
        },
        "skills": skills,
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "supportsAuthenticatedExtendedCard": False,
    }


def build_mcp_server_manifest(
    *,
    base_url: str,
    config: MCPConfig,
    app: Litestar,
    discovered_tools: dict[str, Any],
    discovered_resources: dict[str, Any],
    discovered_prompts: dict[str, Any],
) -> dict[str, Any]:
    """Build an experimental MCP server manifest."""
    tools = []
    for name, handler in discovered_tools.items():
        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
        tool_entry: dict[str, Any] = {
            "name": name,
            "description": render_description(handler, fn, kind="tool", fallback_name=name, opt_keys=config.opt_keys),
            "inputSchema": generate_schema_for_handler(handler),
        }
        if metadata.get("task_support") is not None:
            tool_entry["execution"] = {"taskSupport": metadata["task_support"]}
        if metadata.get("scopes") is not None:
            tool_entry["security"] = {"scopes": metadata["scopes"]}
        tools.append(tool_entry)

    prompts_list = []
    if discovered_prompts:
        for _name, registration in sorted(discovered_prompts.items()):
            prompt_entry: dict[str, Any] = {"name": registration.name}
            if registration.description is not None:
                prompt_entry["description"] = registration.description
            arguments = registration.get_arguments()
            if arguments:
                prompt_entry["arguments"] = arguments
            prompts_list.append(prompt_entry)

    return {
        "experimental": True,
        "name": _server_name(config, app),
        "version": _server_version(app),
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "endpoints": {
            "mcp": f"{base_url.rstrip('/')}{config.base_path}",
            "oauthProtectedResource": f"{base_url.rstrip('/')}/.well-known/oauth-protected-resource",
            "agentCard": f"{base_url.rstrip('/')}/.well-known/agent-card.json",
        },
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"subscribe": True, "listChanged": True},
            "prompts": {"listChanged": True},
            "tasks": config.task_config is not None,
        },
        "tools": tools,
        "resources": sorted(discovered_resources.keys()),
        "prompts": prompts_list,
    }
