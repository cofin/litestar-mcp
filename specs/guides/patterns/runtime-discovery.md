# Runtime Discovery Pattern (Controllers)

## Purpose

Litestar `Controller` routes are materialized into concrete `BaseRouteHandler` instances **after** app construction. If you only scan `AppConfig.route_handlers`, you can miss controller-defined endpoints.

This pattern ensures MCP discovery runs against the runtime route graph so controller-defined tools/resources are included.

## When to Use

Use runtime discovery when:

- Users define MCP-marked endpoints inside a `Controller` class.
- Route handlers are created dynamically or only become concrete after Litestar builds routes.

## Implementation

In `litestar-mcp`, the plugin performs:

1. A config-time scan (good for function-decorated handlers)
2. A runtime scan against `app.routes[*].route_handlers` (covers controllers)

See: `litestar_mcp/plugin.py`

## Notes

- The runtime scan should run before serving requests (startup hook).
- The registry rebuild should re-use the same filtering logic used for config-time discovery.

## Examples in Codebase

- `litestar_mcp/plugin.py` — runtime discovery + registry rebuild
- `tests/test_plugin.py` — controller discovery regression test

