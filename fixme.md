# Route opt passthrough and discovery-route control

Implementation instructions for [#86](https://github.com/cofin/litestar-mcp/issues/86), plus a
second gap found while investigating it.

Everything below was verified against the working tree at `8af91ba`.

---

## Background

Two separate problems, both rooted in the same place: the plugin hardcodes route
`opt` on the handlers it registers and offers no way to influence them.

### Correction to the issue as filed

Issue #86 says the plugin hardcodes `opt={"exclude_from_auth": True}` "on the
routes it mounts". That overstates the scope. Only the two discovery handlers
carry it:

| Site | Route |
| --- | --- |
| `litestar_mcp/plugin.py:172` | `/.well-known/oauth-protected-resource` |
| `litestar_mcp/plugin.py:181` | `/.well-known/agent-card.json` |

The MCP surface itself — `MCPController` (`litestar_mcp/routes.py:390`), mounted
on `mcp_router` at `config.base_path` — carries **no** exclusion. Under a
security layer those routes already compile to "authentication required", so the
motivating scenario in the issue (deployments authenticating MCP with API keys or
IAP headers) already works today.

**The real gap is narrower than the issue states**: there is no way to attach
route `opt` to the mounted MCP router, and no way to influence the two discovery
handlers at all.

### The second problem: the discovery routes cannot be turned off

RFC 9728 pins the protected resource metadata document to the application root,
so any two plugins publishing it claim the same path. `litestar-security`
registers a handler at `/.well-known/oauth-protected-resource` when its
`protected_resource` is configured. Enabling both is a hard startup failure:

```
ImproperlyConfiguredException: Handler already registered for path
'/.well-known/oauth-protected-resource' and http method GET
```

`litestar-security` has added `ProtectedResourceConfig.register_route` to
suppress its own registration ([litestar-security#57](https://github.com/cofin/litestar-security/issues/57)).
This plugin needs the mirror image, so an application can choose which library
owns the path from either side.

---

## Part 1 — `route_opt` passthrough for the mounted MCP router

### Change 1.1: add the config field

`litestar_mcp/config.py`, in `MCPConfig` (the dataclass beginning at line 151).
Add beside `guards`, which is the existing passthrough of the same shape:

```python
route_opt: "dict[str, Any] | None" = None
```

Document it in the class docstring `Attributes:` block, next to the existing
`guards` entry:

```
route_opt: Optional route ``opt`` mapping applied to the mounted MCP router.
    Merged over the plugin's own defaults, so a caller-supplied key wins on
    conflict. Use this to declare an opt-based authentication policy for the
    MCP surface.
```

No `__post_init__` validation is needed. The value is passed to Litestar, which
owns its interpretation; validating key names here would guess at what a
security layer accepts.

### Change 1.2: merge it into the router

`litestar_mcp/plugin.py`, in the `router_kwargs` construction (lines 146-163).
Follow the existing `guards` pattern exactly:

```python
if self._config.guards is not None:
    router_kwargs["guards"] = self._config.guards
if self._config.route_opt is not None:
    router_kwargs["opt"] = dict(self._config.route_opt)
```

Copy the mapping rather than passing it through. Litestar merges layer `opt`
into each handler's resolved `opt`, and sharing the caller's dict invites
action at a distance if they mutate it later.

### Tests

`tests/unit/test_plugin.py`, beside the existing route-registration tests around
line 139.

1. **The router carries the opt.** Build an app with
   `MCPConfig(route_opt={"auth": "sentinel"})` and assert the MCP handler's
   resolved `opt` contains it. Select the handler by method, not by
   `route_handlers[0]` — an `HTTPRoute` also carries Litestar's generated
   OPTIONS handler and that sequence's ordering is not stable across runs.
2. **Default is unchanged.** With no `route_opt`, the resolved handler `opt`
   gains no new keys.
3. **Caller keys win.** If the plugin ever sets a router-level `opt` of its own,
   a caller key of the same name overrides it. Worth pinning now so a later
   default cannot silently take precedence.

---

## Part 2 — control over the discovery routes

### Design note

Prefer one field per route over a single boolean covering both. The two
documents have unrelated owners and unrelated reasons to be disabled:
`/.well-known/oauth-protected-resource` collides with `litestar-security`, while
`/.well-known/agent-card.json` is this plugin's own and has no known collision.
Collapsing them would force an application to drop the agent card in order to
resolve an OAuth metadata conflict.

### Change 2.1: add the config fields

`litestar_mcp/config.py`, in `MCPConfig`:

```python
register_oauth_protected_resource: "bool" = True
register_agent_card: "bool" = True
```

Docstring entries:

```
register_oauth_protected_resource: Whether to register the RFC 9728 protected
    resource metadata route. Set to ``False`` when another plugin publishes
    that document; RFC 9728 pins it to the application root, so two
    registrations collide and the application fails to start.
register_agent_card: Whether to register the agent card discovery route.
```

### Change 2.2: make registration conditional

`litestar_mcp/plugin.py`, at the `app_config.route_handlers.extend(...)` call on
line 190. Replace the unconditional extend:

```python
discovery_handlers: "list[Any]" = []
if self._config.register_oauth_protected_resource:
    discovery_handlers.append(oauth_protected_resource)
if self._config.register_agent_card:
    discovery_handlers.append(agent_card)
app_config.route_handlers.extend(discovery_handlers)
```

Leave both handler definitions in place unconditionally. They are cheap closures,
and defining them behind the flags would make the diff harder to read for no
runtime benefit.

### Change 2.3: nothing — the emitted documents are already independent

Checked, so it does not need rediscovering. `build_agent_card`
(`litestar_mcp/manifests.py:57-83`) derives its only URL from the request base
URL and `config.base_path`:

```python
"url": f"{base_url.rstrip('/')}{config.base_path}",
```

Neither document references the protected resource metadata URL, so suppressing
either route cannot leave a stale or wrong URL behind in the other. No change is
required here.

### Tests

`tests/unit/test_plugin.py`, extending the path assertions at lines 139-141 and
158-160, which already assert on registered paths.

1. **Suppression works, one route at a time.** With
   `register_oauth_protected_resource=False`, that path is absent and
   `/.well-known/agent-card.json` is still present. Then the reverse.
2. **Defaults are unchanged.** Both paths are registered when neither flag is
   set, which the existing assertions at 158-160 already cover — confirm they
   still pass rather than duplicating them.
3. **Co-installation works.** An app registering its own handler at
   `/.well-known/oauth-protected-resource` alongside
   `MCPConfig(register_oauth_protected_resource=False)` starts and serves the
   foreign handler. This is the case the change exists for, so it should be an
   explicit test rather than implied by the unit assertions.

---

## Verification

```bash
make lint
make test
```

Both must pass. `make lint` covers ruff, formatting, type-checking, and
slotscheck.

## Out of scope

- **Changing what the discovery handlers return.** This is about whether they
  are registered and what `opt` the MCP router carries, nothing else.
- **Removing `exclude_from_auth` from the discovery handlers.** Unauthenticated
  reachability is correct for both documents; a client discovers them precisely
  because it does not yet hold a token.
- **Any change to `MCPController` or the MCP surface's authentication.** It
  already authenticates normally, as noted in the correction above.
