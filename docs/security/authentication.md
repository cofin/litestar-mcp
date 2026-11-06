---
title: Authentication Middleware, JWT & Google IAP
---

# Authentication Middleware, JWT & Google IAP

Authentication for the `/mcp` endpoint is a native **Litestar middleware** concern. Whatever authentication middleware your application installs—a custom `AbstractAuthenticationMiddleware`, Litestar's `OAuth2PasswordBearerAuth`, `litestar-security`, or an identity-proxy JWT assertion validator—executes before any route handler. When `MCPHandlerService` dispatches a tool, resource, or route-bound prompt through the internal ASGI pipeline, the target handler inherits `request.user` and `request.auth` identically to a direct HTTP request.

`litestar-mcp` ships no proprietary token validator, JWKS cache, or OAuth discovery route of its own so your HTTP API and MCP surface never diverge.

---

## Bring Your Own Litestar Middleware

Subclass `litestar.middleware.AbstractAuthenticationMiddleware` and register it with `DefineMiddleware` on your `Litestar` application. The middleware inspects the inbound `ASGIConnection` (headers, cookies, or client certificates) and returns an `AuthenticationResult(user=..., auth=...)`:

```python title="docs/examples/snippets/auth_middleware.py"
--8<-- "docs/examples/snippets/auth_middleware.py:example"
```

When a client sends `POST /mcp` with `X-API-Key: let-me-in`:

1. `ApiKeyAuthenticationMiddleware.authenticate_request` validates the header on the `/mcp` request and populates `scope["user"]` and `scope["auth"]`.
2. During `tools/call` for `whoami`, `execute_tool` synthesizes an internal ASGI scope that copies `request.user`, `request.auth`, and request headers from the parent `/mcp` request.
3. The `whoami` handler reads `request.user` (`"demo-user"`) and `request.auth["roles"]` (`["reader"]`) without any MCP-specific branching.

---

## JWT and OAuth2PasswordBearerAuth

Litestar's built-in JWT backends (`litestar[jwt]`) work unchanged with `LitestarMCP`. Install `litestar[jwt]` alongside `litestar-mcp`:

=== "uv"

    ```bash
    uv add "litestar[jwt]" litestar-mcp
    ```

=== "pip"

    ```bash
    pip install "litestar[jwt]" litestar-mcp
    ```

=== "pdm"

    ```bash
    pdm add "litestar[jwt]" litestar-mcp
    ```

=== "poetry"

    ```bash
    poetry add "litestar[jwt]" litestar-mcp
    ```

Configure `OAuth2PasswordBearerAuth` with your `retrieve_user_handler`, `token_secret`, `accepted_issuers`, and `accepted_audiences`, then attach `oauth_backend.on_app_init` to `Litestar(on_app_init=[...])`:

=== "OAuth2 Backend Setup (docs/examples/notes/shared/auth.py)"

    ```python title="docs/examples/notes/shared/auth.py"
    --8<-- "docs/examples/notes/shared/auth.py:117:135"
    ```

=== "Litestar App Wiring (docs/examples/notes/sqlspec/jwt_auth.py)"

    ```python title="docs/examples/notes/sqlspec/jwt_auth.py"
    --8<-- "docs/examples/notes/sqlspec/jwt_auth.py:65:130"
    ```

!!! tip "Excluding Public Discovery Paths"
    Pass `exclude=["^/.well-known/", "^/healthz"]` to `OAuth2PasswordBearerAuth` (or your custom `AbstractAuthenticationMiddleware`) when your application serves unauthenticated health checks, RFC 9728 `/.well-known/oauth-protected-resource` documents, or public A2A `/.well-known/agent-card.json` metadata alongside protected `/mcp` endpoints.

---

## Identity Proxies and Google Cloud IAP

Zero-trust identity proxies such as Google Cloud Identity-Aware Proxy (IAP) authenticate the user upstream at the load balancer and forward a signed ES256 JWT assertion in the `x-goog-iap-jwt-assertion` HTTP header.

To authenticate MCP and REST calls behind Google IAP, verify the `x-goog-iap-jwt-assertion` header against Google's public JWKS (`https://www.gstatic.com/iap/verify/public_key-jwk`), enforcing both `issuer="https://cloud.google.com/iap"` and your Cloud Run / Backend Service `audience`:

=== "IAP Middleware Implementation (docs/examples/notes/shared/auth.py)"

    ```python title="docs/examples/notes/shared/auth.py"
    --8<-- "docs/examples/notes/shared/auth.py:231:263"
    ```

=== "Google IAP Application Wiring (docs/examples/notes/sqlspec/google_iap.py)"

    ```python title="docs/examples/notes/sqlspec/google_iap.py"
    --8<-- "docs/examples/notes/sqlspec/google_iap.py:68:126"
    ```

!!! tip "Built-in GoogleIAPConfig in litestar-security"
    If your application uses `litestar-security`, you can configure `GoogleIAPConfig` and inspect typed `GoogleIAPClaims` directly on `SecurityConfig` instead of writing a custom IAP middleware class. See [First-Party litestar-security Integration](litestar-security.md) for details.

See [Notes Reference Examples Matrix](../architecture/reference-examples.md) for runnable SQLSpec and Advanced Alchemy variants covering `no-auth`, `JWT`, `Cloud Run`, and `Google IAP` with both Litestar `Provide` and Dishka (`FromDishka[T]`) dependency injection.

---

## Authenticating Stdio and HTTP Bridge Sessions

### In-Process Stdio

When running in-process over standard input/output (`litestar mcp stdio`, `run_stdio`, `run_stdio_async`, or `MCP.run(transport="stdio")`), every JSON-RPC frame is dispatched through the same ASGI application. You can authenticate stdio sessions in two ways:

1. **Header Injection via CLI Flags**: Pass `--bearer-env`, `--bearer-cmd`, or `--header` to `litestar mcp stdio` so your application's HTTP authentication middleware validates the synthesized `Authorization` (or custom `--header-name`) header on every request:
   ```bash
   litestar --app my_app:app mcp stdio --bearer-env MCP_API_TOKEN
   ```
2. **Pre-Seeded Identity via `MCPStdioContext`**: Standalone local applications that run without HTTP token middleware can resolve identity from the host OS or environment at startup and pass it to `MCPStdioContext`:
   ```python
   from litestar_mcp import MCPStdioContext
   from litestar_mcp.mcp.stdio import run_stdio

   context = MCPStdioContext(
       client_id="desktop-host",
       owner_id="user:alice",
       user={"sub": "alice", "roles": ["editor"]},
       auth={"sub": "alice", "scopes": ["notes:read", "notes:write"]},
   )
   run_stdio(app, stdio_context=context)
   ```

!!! important "Anonymous Stdio Has No Shared Owner ID"
    When `MCPStdioContext.owner_id` is `None` and no authenticated principal is present on `scope["user"]` or `scope["auth"]`, `litestar-mcp` assigns `owner_id=None` rather than a shared `"stdio"` string. Always supply an authenticated principal or explicit `owner_id` when running multi-user or persisted task stores.

### HTTP-to-Stdio Bridge

When desktop MCP hosts connect to a remote Litestar deployment through `litestar mcp bridge`, pass bearer credentials or custom proxy headers at the CLI boundary:

=== "Static Environment Variable Token"

    ```bash
    litestar --app my_app:app mcp bridge \
      --base-url https://api.example.com \
      --bearer-env MCP_BEARER_TOKEN
    ```

=== "Dynamic Token Command (Auto-Refreshed on 401)"

    ```bash
    litestar --app my_app:app mcp bridge \
      --base-url https://api.example.com \
      --bearer-cmd "gcloud auth print-identity-token"
    ```

=== "Custom Header Name (Google IAP)"

    ```bash
    litestar --app my_app:app mcp bridge \
      --base-url https://iap.example.com \
      --header-name "x-goog-iap-jwt-assertion" \
      --token-prefix "" \
      --bearer-env IAP_ASSERTION_JWT
    ```

When `--bearer-cmd` is configured and the remote Litestar server responds with HTTP `401 Unauthorized`, the bridge automatically re-executes the command once to mint a fresh token and retries the request.

---

## Next Steps

- [First-Party litestar-security Integration](litestar-security.md): Declare optional mechanism policies via `MCPConfig(auth=...)`, emit RFC 9728 `WWW-Authenticate` challenges, and rehydrate `SecurityContext` in `litestar-queues` workers.
- [Authorization, Guards, Scopes & Input Safety](authorization-and-scopes.md): Enforce route guards, validate domain object ownership, and protect multi-round `requestState`.
