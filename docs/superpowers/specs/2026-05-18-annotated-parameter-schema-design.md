# Annotated parameter schema generation — design

**Issue:** [#52](https://github.com/cofin/litestar-mcp/issues/52) — `Annotated[T, Parameter(...)]` parameters serialized as `type: "object"` with `typing.Annotated.__doc__` as description.

**Date:** 2026-05-18
**Branch:** `feat/infrequent-almandine`

## Problem

Two defects in `litestar_mcp/schema_builder.py`:

1. `type_to_json_schema` (line 161) has no `Annotated` branch. `basic_type_to_json_schema` uses identity checks (`annotation is str`, ...) that fail on the `Annotated` wrapper. Execution falls through to the fallback at line 189:

   ```python
   return union_type_to_json_schema(annotation) or {
       "type": "object",
       "description": "Parameter of type " + str(annotation),
   }
   ```

2. `generate_schema_for_handler` lines 254-255 unconditionally overwrite `description` with `param.annotation.__doc__`:

   ```python
   if getattr(param.annotation, "__doc__", None):
       param_schema["description"] = param.annotation.__doc__.strip()
   ```

   For `Annotated[...]` parameters this injects `typing.Annotated`'s class docstring. For Pydantic/msgspec/dataclass parameters it clobbers the description produced by the model schema builder.

Result: every `Annotated[T, Parameter(...)]` query parameter appears in `tools/list` as an opaque `object` with boilerplate `typing.Annotated` description, making the MCP tool unusable for LLM clients.

## Goals

- Unwrap `Annotated[T, Parameter(...), ...]`, recurse on `T`, and merge every `ParameterKwarg`'s metadata (description, constraints, title, examples) into the resulting JSON Schema.
- Schema property keys use the **wire name** (`Parameter(query=...)` → fallback `Parameter(alias=...)` → fallback Python parameter name). LLM clients see `isPaid`, not `is_paid`, matching the HTTP wire format and Litestar's OpenAPI output.
- Remove the unconditional `__doc__` clobber. Trust each schema branch's output.
- `tools/call` continues to dispatch correctly when arguments arrive keyed by wire name.

## Non-goals

- Body / `data` parameter `Annotated` unwrapping. Body schemas route through model schema builders (msgspec/pydantic), which handle their own metadata.
- `Parameter(header=..., cookie=...)` sourcing. The current executor synthesizes only query strings + path params; header/cookie support is a separate issue.
- Translating non-`ParameterKwarg` metadata in `Annotated` (e.g., `msgspec.Meta`, plain strings). Ignored for now.

## Architecture

Two files change: `litestar_mcp/schema_builder.py` and `litestar_mcp/executor.py`. A small shared helper module derives wire-name aliases so both stay consistent.

### Component diagram

```
                       handler (BaseRouteHandler)
                              │
            ┌─────────────────┼─────────────────┐
            ▼                                   ▼
   schema_builder.py                      executor.py
   generate_schema_for_handler            _split_tool_args
            │                                   │
            └──────────┬────────────────────────┘
                       ▼
              shared alias helper
              _parameter_aliases(handler) -> dict[wire, python]
```

### New helpers (`schema_builder.py`)

```python
from typing import Annotated, get_args, get_origin
from litestar.params import ParameterKwarg


def _unwrap_annotated(annotation: Any) -> tuple[Any, list[ParameterKwarg]]:
    """Return (inner_type, [ParameterKwarg, ...]).

    If annotation is not Annotated, returns (annotation, []).
    Foreign metadata (msgspec.Meta, str, etc.) is silently ignored.
    """
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], [m for m in args[1:] if isinstance(m, ParameterKwarg)]
    return annotation, []


def _merge_parameter_meta(schema: dict[str, Any], meta: ParameterKwarg) -> None:
    """Mutate ``schema`` with non-None ParameterKwarg fields.

    Mapping:
      description    → description
      title          → title
      examples       → examples (wrapped in list if scalar)
      ge             → minimum
      le             → maximum
      gt             → exclusiveMinimum
      lt             → exclusiveMaximum
      min_length     → minLength
      max_length     → maxLength
      pattern        → pattern
      multiple_of    → multipleOf
      const          → const
    """
```

### Branch in `type_to_json_schema`

Add at the top, before the existing dispatch:

```python
inner, metas = _unwrap_annotated(annotation)
if metas or inner is not annotation:
    schema = type_to_json_schema(inner)
    for m in metas:
        _merge_parameter_meta(schema, m)
    return schema
```

When the inner type is a union containing `None`, `type_to_json_schema` already emits `{"anyOf": [...]}`. The merged description applies to the union as a whole — correct, since the parameter's description belongs to the parameter, not to a member type.

### Changes to `generate_schema_for_handler`

1. Build `properties` and `required` keyed by wire name. For each parameter:
   - `python_name = param.name`
   - `_, metas = _unwrap_annotated(param.annotation)`
   - `wire_name = next((m.query or m.alias for m in metas if (m.query or m.alias)), python_name)`
   - `properties[wire_name] = type_to_json_schema(param.annotation)`
2. **Delete** the `__doc__` clobber (lines 254-255).
3. Detect wire-name collisions: if two parameters resolve to the same wire name, raise `ValueError(f"Wire-name collision in {handler}: {wire_name} maps to both {a} and {b}")`. Failing at schema build time (i.e. plugin registration) surfaces the bug at startup instead of at first tool call.

### Shared alias helper

New module: `litestar_mcp/_parameter_aliases.py` (underscore-prefixed = private).

```python
def parameter_aliases(handler: BaseRouteHandler) -> dict[str, str]:
    """Return ``{wire_name: python_name}`` for handler params whose wire name
    differs from the Python name. Params with no alias are omitted.

    Walks ``handler.parsed_fn_signature.parameters``, unwraps each Annotated
    annotation via ``_unwrap_annotated``, and picks the first available wire
    name:

        Parameter(query=...) > Parameter(alias=...) > python_name

    Used by executor.py to rewrite incoming ``tool_args`` keys from wire name
    → Python name before the existing path/query/body partition runs.
    """
```

`schema_builder.py` does not need this helper directly — it computes wire names inline per parameter while iterating the signature (see step 1 above). The helper exists so `executor.py` can do the reverse rewrite using the same `_unwrap_annotated` logic without duplicating it.

### Changes to `executor.py`

In `_split_tool_args` (line 380):

```python
aliases = parameter_aliases(handler)   # wire → python
if aliases:
    tool_args = {aliases.get(k, k): v for k, v in tool_args.items()}
# … existing path/query/body partition continues unchanged
```

The rewrite happens before the existing path / query / body partition, so `scalar_sig_names` (Python names) continues to match correctly. Litestar's native signature model still does the final `Parameter(query=...)` resolution because:

- The query string is built from Python kwarg names (e.g. `is_paid=true`).
- Litestar's `parsed_fn_signature` maps `?is_paid=true` → `is_paid` param directly. `Parameter(query="isPaid")` only changes the wire name; the kwarg binding works either way.

Path parameters: Litestar route templates use Python names (`/orders/{order_id}`), so the same wire→python rewrite gives `_find_route_path_parameters` the correct keys.

### Error handling

| Situation | Behavior |
|-----------|----------|
| `Annotated[T]` with no `ParameterKwarg` (only foreign metadata) | Unwrap to `T`, ignore foreign metadata, no warning |
| Multiple `ParameterKwarg` in one `Annotated` | Merge in order; later wins for scalar fields (description, title) |
| Two params with same wire name | `ValueError` at schema build / plugin registration |
| Unknown wire-name in `tool_args` (no matching alias) | Pass through unchanged; existing "drop unknown" behavior in `_split_tool_args` step 4 still applies |
| `Parameter(...)` constraint set on a type where JSON Schema keyword has no meaning (e.g. `min_length` on `int`) | Copy through anyway; this matches Litestar OpenAPI behavior, downstream JSON Schema validators flag it |

### Testing

**Unit (`tests/unit/test_schema_builder.py`):**

1. `Annotated[bool | None, Parameter(description="...")]` → `{"anyOf": [{"type":"boolean"},{"type":"null"}], "description": "..."}`
2. `Annotated[int, Parameter(ge=1, le=100, description="qty")]` → `{"type":"integer", "minimum":1, "maximum":100, "description":"qty"}`
3. `Annotated[str, Parameter(min_length=3, max_length=50, pattern="^[a-z]+$")]` → string schema with `minLength`, `maxLength`, `pattern`
4. `Annotated[str, Parameter(query="userName")]` → property key in `inputSchema.properties` is `userName`, not the Python param name
5. `Annotated[MyPydanticModel, Parameter(description="user")]` → Pydantic schema preserved, description merged, no `BaseModel.__doc__` leak
6. Regression: bare `bool | None` → identical schema to before (no `Annotated` overhead)
7. Wire-name collision → `ValueError`

**Integration (`tests/integration/`):**

- Port the issue's repro: `annotated_list` handler with `Annotated[bool|None, Parameter(query="isPaid", ...)]` and `Annotated[datetime|None, Parameter(query="preparedAfter", ...)]`. Assert `tools/list` returns the expected boolean / string (date-time) schemas with correct descriptions, and a `tools/call` with `{"isPaid": true, "preparedAfter": "2026-01-01T00:00:00"}` dispatches successfully.

### Files touched

| File | Change |
|------|--------|
| `litestar_mcp/schema_builder.py` | Add `_unwrap_annotated`, `_merge_parameter_meta`; new `Annotated` branch in `type_to_json_schema`; wire-name keying in `generate_schema_for_handler`; remove `__doc__` clobber |
| `litestar_mcp/_parameter_aliases.py` | New module: `parameter_aliases(handler)` |
| `litestar_mcp/executor.py` | `_split_tool_args` rewrites `tool_args` keys via `parameter_aliases` |
| `tests/unit/test_schema_builder.py` | New cases for Annotated, constraints, wire-name, collisions, regressions |
| `tests/integration/` | New integration test for the issue's repro |
| `CHANGELOG.md` | Entry under unreleased |

## Out-of-scope follow-ups (separate issues)

- `Annotated` on body fields with `Parameter(header=/cookie=)` sourcing.
- A general audit of msgspec `Meta` vs Litestar `Parameter` translation parity.
