---
title: Authorization, Guards, Scopes & Input Safety
---

# Authorization, Guards, Scopes & Input Safety

Once authentication middleware establishes caller identity on `/mcp` (or `MCPStdioContext` seeds identity on stdio), authorization determines which tools, resources, prompts, and domain objects that caller may access.

Because `litestar-mcp` dispatches every primitive through Litestar's internal ASGI pipeline, your existing Litestar `Guard` functions, `Provide` / `FromDishka[T]` dependencies, and repository filters protect HTTP and MCP endpoints identically.

---

## Enforcing Access Control with Litestar Guards

Attach Litestar `Guard` functions at any layer of your application hierarchy:

- **MCP Router Level (`MCPConfig.guards`)**: Runs on every request to `/mcp` (`server/discover`, `initialize`, `tools/list`, `tools/call`, `resources/*`, `prompts/*`, `skills/*`, `tasks/*`).
- **Controller Level (`Controller.guards`, `PromptController.guards`, `SkillController.guards`)**: Protects every HTTP route, MCP tool, and MCP prompt declared on that controller.
- **Individual Route Level (`@get(..., guards=[...])`, `@mcp.tool(guards=[...])`)**: Protects a specific tool, resource, or prompt handler during internal ASGI execution.

```python title="docs/examples/snippets/authorization_guard.py"
--8<-- "docs/examples/snippets/authorization_guard.py:example"
```

Guards receive the same `ASGIConnection` and `BaseRouteHandler` during an MCP `tools/call` as they do during a direct HTTP request. Raising `PermissionDeniedException` or `NotAuthorizedException` inside a guard aborts execution before handler dependencies or tool logic run.

---

## Discovery Scopes vs Runtime Enforcement

The `scopes` parameter on `@mcp_tool(scopes=["report:read"])` (or `mcp_annotations={"scopes": ["report:read"]}`) is **discovery metadata only**:

- It serializes under `tools[].annotations.scopes` in `tools/list` responses and `litestar mcp export-tool-spec` manifests.
- MCP clients, approval UIs, and agent planners inspect `annotations.scopes` to determine which OAuth scopes or permissions a tool requires before invoking it.
- **`MCPHandlerService` does not enforce `annotations.scopes` inline during `tools/call`.** Runtime enforcement must always live in a Litestar `Guard` or dependency provider so that direct HTTP callers and MCP tool callers are governed by the exact same authorization logic.

| Mechanism | Where Declared | Wire Effect | Runtime Enforcement |
| :--- | :--- | :--- | :--- |
| `@mcp_tool(scopes=["report:read"])` | Route decorator / `mcp_annotations` | Advertised in `tools/list` under `tools[].annotations.scopes` | **None** (discovery metadata only) |
| `guards=[require_scope("report:read")]` | Route, `Controller`, `Router`, or `MCPConfig(guards=[...])` | Not advertised in `tools/list` | **Enforced** before handler execution on both HTTP and MCP paths |
| `MCPConfig(auth=required(...))` | `MCPConfig.auth` / `MCPConfig.route_opt` | Emits `WWW-Authenticate` on `401` when configured with `litestar-security` | **Enforced** by `litestar-security` on `/mcp` and synthesized tool scopes |

---

## Task Owner Transport Isolation vs Domain Object Ownership

### Transport-Level Task Isolation

When the `io.modelcontextprotocol/tasks` extension is enabled, `MCPHandlerService` derives an `owner_id` from the authenticated request (for example, `user:<subject>` from `request.user` / `request.auth` or `MCPStdioContext.owner_id`) and binds it to the created `TaskRecord` in `MCPTaskStore`:

- Calls to `tasks/get`, `tasks/update`, and `tasks/cancel` pass the current caller's `owner_id` to `MCPTaskStore.get(task_id, owner_id)`.
- If `record.owner_id` does not match the caller's `owner_id`, `MCPTaskStore` raises `TaskLookupError("Failed to retrieve task: Task not found")`, returning the exact same JSON-RPC `INVALID_PARAMS` (`-32602`) error as a non-existent task ID so task existence is never leaked across principals.
- Unauthenticated deployments (`owner_id=None`) rely solely on unguessable 128-bit UUIDv4 hex task IDs.

### Domain Object Ownership

Transport-level `owner_id` isolation only proves that the caller polling `tasks/get` is the same principal who initiated the `tools/call`. It **does not** prove that the caller owns the domain entity named in the tool's arguments (such as `workspace_owner_id`, `project_id`, `note_id`, or `tenant_id`).

Always validate domain object ownership against `request.user` / `request.auth` using Litestar `Provide` dependencies, Dishka request-scoped services, or repository query filters:

```python title="docs/examples/snippets/domain_authorization.py"
--8<-- "docs/examples/snippets/domain_authorization.py:example"
```

---

## Cryptographic Protection for Multi-Round Request State

Tools, resources, and prompts can pause execution to request interactive user confirmation or additional parameters by returning `MCPInputRequiredResult(input_requests=..., request_state=...)`.

When the client supplies the requested answers—either by retrying the request with `params.inputResponses` and `params.requestState` or by calling `tasks/update` on an `input_required` task—the handler reads `get_mcp_request_context().input_responses` and `get_mcp_request_context().request_state`.

!!! danger "`requestState` Is Untrusted Wire Input"
    Even though `MCPTaskStore` persists an internal copy of `request_state` on `TaskRecord`, synchronous multi-round-trip requests (`task_input_before_start=True` or non-task `MCPInputRequiredResult` flows) receive `params.requestState` directly from the remote client on retry. Treat `requestState` as attacker-controlled wire input.

Follow four rules whenever `requestState` carries security-relevant context across rounds:

1. **Authenticate or Encrypt with AEAD / HMAC**: Sign or encrypt the state payload with a server-held secret key (for example, using `hmac.new(secret, payload, hashlib.sha256)` or AES-GCM / ChaCha20-Poly1305 AEAD).
2. **Bind to the Authenticated Principal**: Include the server-validated `owner_id` (or `request.user.sub`) inside the signed payload and verify on resume that it matches the current request's authenticated principal.
3. **Bind to the Tool Name & Original Arguments**: Include the tool name and a canonical hash of the original arguments so a valid state token from one tool invocation cannot be replayed against a different tool or mutated target ID.
4. **Enforce an Expiration Timestamp**: Embed an `exp` timestamp and reject expired state tokens with `PermissionDeniedException`. Never trust `clientInfo` (`get_mcp_request_context().client_info`) as an authorization identity.

```python
import base64
import hashlib
import hmac
import time
from typing import Any

from litestar.exceptions import PermissionDeniedException
from litestar.serialization import decode_json, encode_json


def seal_request_state(
    *,
    secret: bytes,
    owner_id: str,
    tool_name: str,
    target_id: str,
    ttl_seconds: int = 300,
) -> str:
    """Create an HMAC-authenticated requestState token bound to principal and target."""
    envelope = {
        "owner_id": owner_id,
        "tool": tool_name,
        "target_id": target_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = encode_json(envelope)
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(raw).decode("ascii")
    mac = base64.urlsafe_b64encode(sig).decode("ascii")
    return f"{token}.{mac}"


def unseal_request_state(
    token_str: str,
    *,
    secret: bytes,
    expected_owner_id: str,
    expected_tool_name: str,
    expected_target_id: str,
) -> dict[str, Any]:
    """Verify an HMAC-authenticated requestState token or raise PermissionDeniedException."""
    try:
        b64_payload, b64_mac = token_str.split(".", 1)
        raw = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
        actual_sig = base64.urlsafe_b64decode(b64_mac.encode("ascii"))
    except ValueError as exc:
        msg = "Malformed requestState token"
        raise PermissionDeniedException(msg) from exc

    expected_sig = hmac.new(secret, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(actual_sig, expected_sig):
        msg = "Invalid requestState signature"
        raise PermissionDeniedException(msg)

    payload: dict[str, Any] = decode_json(raw)
    if (
        payload.get("owner_id") != expected_owner_id
        or payload.get("tool") != expected_tool_name
        or payload.get("target_id") != expected_target_id
        or int(payload.get("exp", 0)) < int(time.time())
    ):
        msg = "Expired or mismatched requestState context"
        raise PermissionDeniedException(msg)
    return payload
```

---

## Remote-Safe File and Path Arguments and Skill Integrity

### Why Raw Filesystem Paths Are Unsafe on Remote MCP Servers

Raw path arguments passed to an MCP tool (for example, `{"path": "/Users/alice/exports/report.csv"}`) refer to paths inside the **server process's filesystem**, not the remote client machine. Accepting arbitrary filesystem paths from an LLM or remote MCP client exposes the server to path-traversal and local file disclosure vulnerabilities.

Prefer remote-safe tool argument contracts:

| Pattern | Recommended Argument Shape | Security Advantage |
| :--- | :--- | :--- |
| **Inline Content** | `content: str` or base64 `data: str` (bounded by `max_length` or `MCPConfig.max_blob_bytes`) | Zero server filesystem traversal; payload is validated directly by `msgspec`. |
| **Application Object IDs** | `document_id: UUID` or `note_id: str` | Resolved through your database repository after verifying caller ownership. |
| **Object-Store Handles** | `bucket: str`, `key: str` or pre-signed URLs | Validated against tenant prefixes and fetched via scoped storage credentials. |
| **Local Bridge Pre-Reading** | Local client reads file before calling remote `/mcp` | Keeps client filesystem access on the client machine while sending content or object handles to the server. |

If a local or sandboxed MCP tool intentionally operates on server-side files, restrict it to an explicit allowlisted root directory, resolve symlinks with `Path.resolve()`, verify containment with `resolved_path.is_relative_to(allowed_root)`, and authorize the caller before opening the file.

### Built-In Agent Skills Containment and SkillIntegrityError

`litestar-mcp` applies strict filesystem containment and cryptographic integrity verification to Agent Skills (`MCPSkillsConfig`, `FilesystemSkillSource`, `SkillCatalog`, `SkillFile`):

1. **Symlink & Dotfile Rejection**: `FilesystemSkillSource` walks configured skill directories with `os.walk(..., followlinks=False)`, skipping dot-prefixed directories/files and symlinked child directories or files, and raises `ImproperlyConfiguredException` at startup if `SKILL.md` is a symlink.
2. **Traversal-Proof URI Parsing (`parse_skill_uri`)**: `skill://<name>/<relative_path>` URIs reject empty segments, `.` and `..` traversal components, leading or trailing slashes, and backslash (`\`) characters.
3. **Resource Bounds (`max_files_per_skill`, `max_bytes_per_skill`)**: Startup validation enforces a hard cap of `512` files and `16 MiB` (`16_777_216` bytes) per skill folder by default.
4. **Runtime SHA-256 Verification (`verify_skill_file` & `SkillIntegrityError`)**: Every `SkillFile` records its exact byte `size` and `sha256:<64-lowercase-hex>` `digest` in the manifest. On every `resources/read` request for a `skill://` URI, `SkillCatalog.read_file` runs `verify_skill_file(file, data)`. If the on-disk file or `SkillSource.read_file` output was mutated or replaced after manifest generation, `SkillIntegrityError` is raised and mapped to JSON-RPC `INTERNAL_ERROR` (`-32603`) without serving the tampered bytes.
