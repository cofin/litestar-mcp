"""Generated discovery manifests for Litestar MCP."""

from typing import TYPE_CHECKING, Any

from litestar_mcp.auth import MCPAuthConfig  # noqa: TC001

if TYPE_CHECKING:
    from litestar import Litestar


def build_oauth_protected_resource(auth_config: "MCPAuthConfig | None", app: "Litestar") -> "dict[str, Any]":
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
