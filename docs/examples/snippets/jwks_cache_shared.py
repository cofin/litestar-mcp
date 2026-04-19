"""Share one :class:`DefaultJWKSCache` across MCP validators."""

# start-example
from litestar_mcp import DefaultJWKSCache, create_oidc_validator
from litestar_mcp.auth import OIDCProviderConfig

shared_cache = DefaultJWKSCache()

validator_a = create_oidc_validator(
    "https://company.okta.com",
    "api://mcp-tools",
    jwks_cache=shared_cache,
)
provider_b = OIDCProviderConfig(
    issuer="https://company.okta.com",
    audience="api://admin",
    jwks_cache=shared_cache,
)
# end-example
