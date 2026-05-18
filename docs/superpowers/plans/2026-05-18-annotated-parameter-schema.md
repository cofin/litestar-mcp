# Annotated Parameter Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix issue #52 — `Annotated[T, Parameter(...)]` parameters are emitted as `{"type": "object"}` with the `typing.Annotated` class docstring. Unwrap them, merge `Parameter` metadata, key properties by wire name, and stop clobbering descriptions with `__doc__`.

**Architecture:** Add an `Annotated`-unwrap branch to `type_to_json_schema`. In `generate_schema_for_handler`, compute each parameter's wire name from `Parameter.query`/`alias`, key `properties`/`required` by wire name, and remove the unconditional `__doc__` clobber. Add a private `_parameter_aliases.py` helper used by `executor._split_tool_args` to rewrite incoming `tool_args` keys from wire name back to the Python kwarg name before dispatch.

**Tech Stack:** Python 3.11+, Litestar 2.x, `litestar.params.ParameterKwarg`, `typing.Annotated` / `get_origin` / `get_args`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-18-annotated-parameter-schema-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `litestar_mcp/schema_builder.py` | JSON Schema generation. Gains `Annotated` unwrap + metadata merge. | Modify |
| `litestar_mcp/_parameter_aliases.py` | Per-handler `wire_name → python_name` map. Shared by schema_builder and executor. | Create |
| `litestar_mcp/executor.py` | `_split_tool_args` rewrites incoming tool_args keys via the alias map. | Modify (one helper + two-line change in `_split_tool_args`) |
| `tests/unit/test_schema_builder.py` | Unit tests for unwrap, metadata merge, wire naming, collisions, regression. | Modify |
| `tests/unit/test_parameter_aliases.py` | Unit tests for the alias helper. | Create |
| `tests/integration/test_annotated_parameters.py` | Integration test mirroring the issue's repro. | Create |
| `CHANGELOG.md` | Entry under unreleased. | Modify |

---

## Test commands (reference)

- Run one test by node id: `uv run pytest <file>::<test_name> -v`
- Run a unit module: `uv run pytest tests/unit/test_schema_builder.py -v`
- Run integration module: `uv run pytest tests/integration/test_annotated_parameters.py -v`

---

## Task 1: `_unwrap_annotated` helper

Pure function that splits an `Annotated[...]` annotation into its inner type and list of `ParameterKwarg` metadata. Returns `(annotation, [])` for non-Annotated inputs. Foreign metadata (strings, `msgspec.Meta`, etc.) is filtered out.

**Files:**
- Modify: `litestar_mcp/schema_builder.py`
- Test: `tests/unit/test_schema_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_schema_builder.py`:

```python
from typing import Annotated

from litestar.params import Parameter, ParameterKwarg

from litestar_mcp.schema_builder import _unwrap_annotated


class TestUnwrapAnnotated:
    def test_non_annotated_returns_input_and_empty_list(self) -> None:
        inner, metas = _unwrap_annotated(int)
        assert inner is int
        assert metas == []

    def test_annotated_with_parameter_kwarg_returns_inner_and_meta(self) -> None:
        annotation = Annotated[bool, Parameter(description="paid")]
        inner, metas = _unwrap_annotated(annotation)
        assert inner is bool
        assert len(metas) == 1
        assert isinstance(metas[0], ParameterKwarg)
        assert metas[0].description == "paid"

    def test_annotated_filters_foreign_metadata(self) -> None:
        annotation = Annotated[int, "doc string", Parameter(ge=1)]
        inner, metas = _unwrap_annotated(annotation)
        assert inner is int
        assert len(metas) == 1
        assert metas[0].ge == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestUnwrapAnnotated -v`
Expected: FAIL with `ImportError: cannot import name '_unwrap_annotated'`.

- [ ] **Step 3: Add the helper to `schema_builder.py`**

Update imports at the top of `litestar_mcp/schema_builder.py`. The existing imports are:

```python
import contextlib
import inspect
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin
```

Add `Annotated` to the `typing` import and add a runtime import for `ParameterKwarg`:

```python
import contextlib
import inspect
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Union, get_args, get_origin

from litestar.params import ParameterKwarg
```

Then add the helper immediately after the `_EXECUTION_CONTEXT_PARAMS` line (around line 21, before `basic_type_to_json_schema`):

```python
def _unwrap_annotated(annotation: Any) -> "tuple[Any, list[ParameterKwarg]]":
    """Return ``(inner_type, [ParameterKwarg, ...])``.

    For non-Annotated annotations, returns ``(annotation, [])``.
    Foreign metadata (strings, ``msgspec.Meta``, etc.) is ignored.
    """
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], [m for m in args[1:] if isinstance(m, ParameterKwarg)]
    return annotation, []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestUnwrapAnnotated -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/schema_builder.py tests/unit/test_schema_builder.py
git commit -m "feat(schema): add _unwrap_annotated helper (#52)"
```

---

## Task 2: `_merge_parameter_meta` helper

Mutates a JSON Schema dict in place with non-None fields from a `ParameterKwarg`. Maps Litestar's Python-side constraint names to JSON Schema keywords.

**Files:**
- Modify: `litestar_mcp/schema_builder.py`
- Test: `tests/unit/test_schema_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_schema_builder.py`:

```python
from litestar_mcp.schema_builder import _merge_parameter_meta


class TestMergeParameterMeta:
    def test_description_merges(self) -> None:
        schema: dict[str, Any] = {"type": "boolean"}
        _merge_parameter_meta(schema, Parameter(description="paid"))
        assert schema == {"type": "boolean", "description": "paid"}

    def test_numeric_constraints_map_to_json_schema_keywords(self) -> None:
        schema: dict[str, Any] = {"type": "integer"}
        _merge_parameter_meta(schema, Parameter(ge=1, le=100, gt=0, lt=200, multiple_of=5))
        assert schema == {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "exclusiveMinimum": 0,
            "exclusiveMaximum": 200,
            "multipleOf": 5,
        }

    def test_string_constraints_map_to_json_schema_keywords(self) -> None:
        schema: dict[str, Any] = {"type": "string"}
        _merge_parameter_meta(schema, Parameter(min_length=3, max_length=50, pattern="^[a-z]+$"))
        assert schema == {
            "type": "string",
            "minLength": 3,
            "maxLength": 50,
            "pattern": "^[a-z]+$",
        }

    def test_title_and_examples_merge(self) -> None:
        schema: dict[str, Any] = {"type": "string"}
        _merge_parameter_meta(schema, Parameter(title="Email", examples=["a@b.com"]))
        assert schema["title"] == "Email"
        assert schema["examples"] == ["a@b.com"]

    def test_none_fields_skipped(self) -> None:
        schema: dict[str, Any] = {"type": "string"}
        _merge_parameter_meta(schema, Parameter())
        assert schema == {"type": "string"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestMergeParameterMeta -v`
Expected: FAIL with `ImportError: cannot import name '_merge_parameter_meta'`.

- [ ] **Step 3: Add the helper to `schema_builder.py`**

Insert immediately after `_unwrap_annotated`:

```python
_META_FIELD_MAP: "tuple[tuple[str, str], ...]" = (
    ("description", "description"),
    ("title", "title"),
    ("examples", "examples"),
    ("ge", "minimum"),
    ("le", "maximum"),
    ("gt", "exclusiveMinimum"),
    ("lt", "exclusiveMaximum"),
    ("min_length", "minLength"),
    ("max_length", "maxLength"),
    ("pattern", "pattern"),
    ("multiple_of", "multipleOf"),
    ("const", "const"),
)


def _merge_parameter_meta(schema: "dict[str, Any]", meta: ParameterKwarg) -> None:
    """Copy non-None fields from ``meta`` into ``schema`` using JSON Schema names."""
    for attr, key in _META_FIELD_MAP:
        value = getattr(meta, attr, None)
        if value is None:
            continue
        if key == "examples" and not isinstance(value, list):
            schema[key] = [value]
        else:
            schema[key] = value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestMergeParameterMeta -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/schema_builder.py tests/unit/test_schema_builder.py
git commit -m "feat(schema): add _merge_parameter_meta helper (#52)"
```

---

## Task 3: Wire `Annotated` unwrap into `type_to_json_schema`

The new branch must run **before** the existing dispatch so the inner type can be resolved normally afterward.

**Files:**
- Modify: `litestar_mcp/schema_builder.py:161-192`
- Test: `tests/unit/test_schema_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_schema_builder.py`:

```python
class TestTypeToJsonSchemaAnnotated:
    def test_annotated_bool_optional_yields_anyOf_with_description(self) -> None:
        annotation = Annotated[
            bool | None,
            Parameter(query="isPaid", description="Whether the order is paid"),
        ]
        schema = type_to_json_schema(annotation)
        assert "anyOf" in schema
        types_seen = {member.get("type") for member in schema["anyOf"]}
        assert types_seen == {"boolean", "null"}
        assert schema["description"] == "Whether the order is paid"

    def test_annotated_int_with_constraints(self) -> None:
        annotation = Annotated[int, Parameter(ge=1, le=100, description="qty")]
        schema = type_to_json_schema(annotation)
        assert schema == {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "qty",
        }

    def test_annotated_str_pattern_and_length(self) -> None:
        annotation = Annotated[str, Parameter(min_length=3, max_length=50, pattern="^[a-z]+$")]
        schema = type_to_json_schema(annotation)
        assert schema == {
            "type": "string",
            "minLength": 3,
            "maxLength": 50,
            "pattern": "^[a-z]+$",
        }

    def test_annotated_without_parameter_metadata_still_unwraps(self) -> None:
        annotation = Annotated[int, "doc string"]
        assert type_to_json_schema(annotation) == {"type": "integer"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestTypeToJsonSchemaAnnotated -v`
Expected: FAIL — the bool|None case returns `{"type": "object", "description": "Parameter of type ..."}`.

- [ ] **Step 3: Insert the unwrap branch at the top of `type_to_json_schema`**

In `litestar_mcp/schema_builder.py`, replace the body of `type_to_json_schema` (current lines 161-192):

```python
def type_to_json_schema(annotation: Any) -> "dict[str, Any]":
    """Convert a Python type annotation to JSON Schema format.

    Args:
        annotation: Python type annotation to convert.

    Returns:
        JSON Schema dictionary for the type.
    """
    if annotation is None or annotation == inspect.Parameter.empty:
        return {"type": "object", "description": "No type annotation provided"}

    inner, metas = _unwrap_annotated(annotation)
    if inner is not annotation:
        schema = type_to_json_schema(inner)
        for meta in metas:
            _merge_parameter_meta(schema, meta)
        return schema

    if isinstance(annotation, str):
        annotation = _resolve_string_annotation(annotation)
        if isinstance(annotation, dict):
            return annotation

    if result := basic_type_to_json_schema(annotation):
        return result
    if result := collection_type_to_json_schema(annotation):
        return result
    if result := model_to_json_schema(annotation):
        return result

    return union_type_to_json_schema(annotation) or {
        "type": "object",
        "description": "Parameter of type " + str(annotation),
    }
```

- [ ] **Step 4: Run new test + full schema_builder test module**

Run: `uv run pytest tests/unit/test_schema_builder.py -v`
Expected: PASS — new `TestTypeToJsonSchemaAnnotated` (4 tests) + all pre-existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/schema_builder.py tests/unit/test_schema_builder.py
git commit -m "feat(schema): unwrap Annotated in type_to_json_schema (#52)"
```

---

## Task 4: Create `_parameter_aliases.py` helper

Walks a handler's signature, unwraps each parameter's annotation, and returns `{wire_name: python_name}` entries only when they differ. Used by the executor.

**Files:**
- Create: `litestar_mcp/_parameter_aliases.py`
- Create: `tests/unit/test_parameter_aliases.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_parameter_aliases.py`:

```python
"""Tests for the wire-name → python-name alias helper."""

from typing import Annotated, Any

from litestar.params import Parameter

from litestar_mcp._parameter_aliases import parameter_aliases
from tests.unit.conftest import create_app_with_handler


class TestParameterAliases:
    def test_no_aliases_returns_empty_map(self) -> None:
        def handler(name: str, age: int) -> dict[str, Any]:
            return {"name": name, "age": age}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_query_alias_is_included(self) -> None:
        def handler(
            is_paid: Annotated[bool, Parameter(query="isPaid")] = False,
        ) -> dict[str, Any]:
            return {"is_paid": is_paid}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"isPaid": "is_paid"}

    def test_alias_field_used_when_query_missing(self) -> None:
        def handler(
            tenant_id: Annotated[str, Parameter(alias="tenantId")] = "",
        ) -> dict[str, Any]:
            return {"tenant_id": tenant_id}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"tenantId": "tenant_id"}

    def test_query_takes_precedence_over_alias(self) -> None:
        def handler(
            x: Annotated[int, Parameter(query="qX", alias="aX")] = 0,
        ) -> dict[str, Any]:
            return {"x": x}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"qX": "x"}

    def test_no_alias_omitted_even_when_annotated(self) -> None:
        def handler(
            n: Annotated[int, Parameter(description="count")] = 0,
        ) -> dict[str, Any]:
            return {"n": n}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_parameter_aliases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'litestar_mcp._parameter_aliases'`.

- [ ] **Step 3: Create the module**

Create `litestar_mcp/_parameter_aliases.py`:

```python
"""Per-handler wire-name → python-name alias mapping for Annotated parameters."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from litestar_mcp.schema_builder import _unwrap_annotated
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler


def parameter_aliases(handler: BaseRouteHandler) -> dict[str, str]:
    """Return ``{wire_name: python_name}`` for handler params whose wire name differs.

    Wire name is selected as:

        Parameter(query=...) > Parameter(alias=...) > python_name

    Parameters with no Annotated metadata, or with metadata that does not set
    ``query``/``alias``, are omitted.
    """
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        fn = handler  # raw function in tests

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}

    aliases: dict[str, str] = {}
    for python_name, param in sig.parameters.items():
        _, metas = _unwrap_annotated(param.annotation)
        for meta in metas:
            wire_name = meta.query or meta.alias
            if wire_name and wire_name != python_name:
                aliases[wire_name] = python_name
                break
    return aliases
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_parameter_aliases.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/_parameter_aliases.py tests/unit/test_parameter_aliases.py
git commit -m "feat(aliases): add per-handler wire-name alias helper (#52)"
```

---

## Task 5: Wire-name keying + drop `__doc__` clobber + collision detection in `generate_schema_for_handler`

Three coupled changes to `generate_schema_for_handler` in `schema_builder.py`:

1. Key `properties`/`required` by wire name (query/alias when set).
2. Delete the `__doc__` clobber (lines 254-255).
3. Raise `ValueError` on wire-name collisions.

**Files:**
- Modify: `litestar_mcp/schema_builder.py:215-282`
- Test: `tests/unit/test_schema_builder.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_schema_builder.py`:

```python
class TestGenerateSchemaWireNamesAndDocClobber:
    def test_query_alias_used_as_property_key(self) -> None:
        def handler(
            is_paid: Annotated[bool | None, Parameter(query="isPaid", description="paid?")] = None,
        ) -> dict[str, Any]:
            return {"is_paid": is_paid}

        _, h = create_app_with_handler(handler)
        schema = generate_schema_for_handler(h)
        assert "isPaid" in schema["properties"]
        assert "is_paid" not in schema["properties"]
        assert schema["properties"]["isPaid"]["description"] == "paid?"

    def test_required_uses_wire_names(self) -> None:
        def handler(
            user_id: Annotated[str, Parameter(query="userId")],
        ) -> dict[str, Any]:
            return {"user_id": user_id}

        _, h = create_app_with_handler(handler)
        schema = generate_schema_for_handler(h)
        assert schema["required"] == ["userId"]

    def test_no_doc_clobber_on_annotated_parameter(self) -> None:
        def handler(
            is_paid: Annotated[bool | None, Parameter(query="isPaid")] = None,
        ) -> dict[str, Any]:
            return {"is_paid": is_paid}

        _, h = create_app_with_handler(handler)
        schema = generate_schema_for_handler(h)
        prop = schema["properties"]["isPaid"]
        description = prop.get("description", "")
        assert "Runtime representation of an annotated type" not in description

    def test_no_doc_clobber_on_dataclass_parameter(self) -> None:
        @dataclass
        class Filter:
            """Filter dataclass with a meaningful docstring."""

            name: str

        def handler(filter: Filter) -> dict[str, Any]:
            return {"filter": filter}

        _, h = create_app_with_handler(handler)
        schema = generate_schema_for_handler(h)
        prop = schema["properties"]["filter"]
        assert "Filter dataclass with a meaningful docstring" not in prop.get("description", "")

    def test_wire_name_collision_raises(self) -> None:
        def handler(
            a: Annotated[str, Parameter(query="same")] = "",
            b: Annotated[str, Parameter(query="same")] = "",
        ) -> dict[str, Any]:
            return {"a": a, "b": b}

        _, h = create_app_with_handler(handler)
        with pytest.raises(ValueError, match="Wire-name collision"):
            generate_schema_for_handler(h)

    def test_bare_types_regression(self) -> None:
        def handler(is_paid: bool | None = None, name: str | None = None) -> dict[str, Any]:
            return {"is_paid": is_paid, "name": name}

        _, h = create_app_with_handler(handler)
        schema = generate_schema_for_handler(h)
        assert set(schema["properties"]) == {"is_paid", "name"}
        assert "anyOf" in schema["properties"]["is_paid"]
        assert "anyOf" in schema["properties"]["name"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_schema_builder.py::TestGenerateSchemaWireNamesAndDocClobber -v`
Expected: FAIL on wire-name tests (properties keyed by `is_paid` not `isPaid`), and on the doc-clobber tests (description currently contains `typing.Annotated.__doc__`). Collision test fails because the collision is not detected. Bare-types regression should already pass.

- [ ] **Step 3: Rewrite `generate_schema_for_handler`**

Replace the body of `generate_schema_for_handler` in `litestar_mcp/schema_builder.py` (current lines 215-282) with:

```python
def generate_schema_for_handler(handler: "BaseRouteHandler") -> "dict[str, Any]":
    """Generate a JSON Schema for an MCP tool handler.

    Args:
        handler: The route handler to generate schema for.

    Returns:
        JSON Schema dictionary describing the tool's input parameters.
    """
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        fn = handler

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    di_params: set[str] = set()
    with contextlib.suppress(Exception):
        di_params = set(handler.resolve_dependencies().keys())

    properties: dict[str, Any] = {}
    required: list[str] = []
    wire_to_python: dict[str, str] = {}

    for python_name, param in sig.parameters.items():
        if python_name in di_params or python_name in _EXECUTION_CONTEXT_PARAMS:
            continue

        _, metas = _unwrap_annotated(param.annotation)
        wire_name = python_name
        for meta in metas:
            candidate = meta.query or meta.alias
            if candidate:
                wire_name = candidate
                break

        if wire_name in wire_to_python and wire_to_python[wire_name] != python_name:
            existing = wire_to_python[wire_name]
            handler_name = getattr(fn, "__name__", "<handler>")
            msg = (
                f"Wire-name collision in handler {handler_name!r}: "
                f"{wire_name!r} maps to both {existing!r} and {python_name!r}"
            )
            raise ValueError(msg)
        wire_to_python[wire_name] = python_name

        properties[wire_name] = type_to_json_schema(param.annotation)

        if param.default is inspect.Parameter.empty:
            required.append(wire_name)

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required:
        schema["required"] = required

    fn_name = getattr(fn, "__name__", "unknown_function")
    fn_doc = getattr(fn, "__doc__", None)
    if fn_doc:
        schema["description"] = "Input parameters for " + str(fn_name) + ": " + str(fn_doc.strip())
    else:
        schema["description"] = "Input parameters for " + str(fn_name)

    return schema
```

Key deltas vs. the original:

- New `wire_to_python` map drives property keys + collision detection.
- Lines 254-255 (the `__doc__` clobber) are gone.
- Required entries are wire names, not Python names.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_schema_builder.py -v`
Expected: PASS — all new `TestGenerateSchemaWireNamesAndDocClobber` tests + all pre-existing schema_builder tests.

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/schema_builder.py tests/unit/test_schema_builder.py
git commit -m "feat(schema): wire-name keying, drop __doc__ clobber, collision detection (#52)"
```

---

## Task 6: Executor — rewrite `tool_args` wire names → python names in `_split_tool_args`

The executor currently filters `tool_args` by Python kwarg names. With wire-keyed tool input (e.g. `{"isPaid": true}`), unrewritten keys fall through to the body or get dropped. Use the alias helper to rewrite the dict before the existing partition runs.

**Files:**
- Modify: `litestar_mcp/executor.py:380-417`
- Test: `tests/integration/test_annotated_parameters.py` (created in Task 7 — this task is exercised end-to-end there)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_annotated_parameters.py` (this is the regression test for issue #52):

```python
"""Integration coverage for Annotated[T, Parameter(...)] query params (#52)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import pytest
from litestar import Litestar, get
from litestar.params import Parameter
from litestar.testing import TestClient

from litestar_mcp import MCPPlugin
from tests.integration.conftest import rpc


@get("/annotated", operation_id="annotated_list")
async def annotated_list(
    is_paid: Annotated[
        bool | None,
        Parameter(query="isPaid", description="Whether the order is paid"),
    ] = None,
    prepared_after: Annotated[
        datetime | None,
        Parameter(query="preparedAfter", description="Filter: prepared_at >= this"),
    ] = None,
) -> dict[str, Any]:
    return {
        "is_paid": is_paid,
        "prepared_after": prepared_after.isoformat() if prepared_after else None,
    }


@pytest.fixture
def annotated_app() -> Litestar:
    return Litestar(route_handlers=[annotated_list], plugins=[MCPPlugin()])


def test_annotated_query_params_yield_typed_schema(annotated_app: Litestar) -> None:
    with TestClient(app=annotated_app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "annotated_list")
        props = tool["inputSchema"]["properties"]

        assert set(props) == {"isPaid", "preparedAfter"}

        is_paid = props["isPaid"]
        assert "anyOf" in is_paid
        boolean_member = next(m for m in is_paid["anyOf"] if m.get("type") == "boolean")
        null_member = next(m for m in is_paid["anyOf"] if m.get("type") == "null")
        assert boolean_member == {"type": "boolean"}
        assert null_member == {"type": "null"}
        assert is_paid["description"] == "Whether the order is paid"
        assert "Runtime representation of an annotated type" not in is_paid.get("description", "")


def test_annotated_tool_call_dispatches_with_wire_keys(annotated_app: Litestar) -> None:
    with TestClient(app=annotated_app) as client:
        result = rpc(
            client,
            "tools/call",
            {
                "name": "annotated_list",
                "arguments": {"isPaid": True, "preparedAfter": "2026-01-01T00:00:00"},
            },
        )
        assert "result" in result
        assert "error" not in result
```

- [ ] **Step 2: Run integration test to verify it fails**

Run: `uv run pytest tests/integration/test_annotated_parameters.py -v`

Expected outcome before this task's fix is applied:
- `test_annotated_query_params_yield_typed_schema` already passes (Task 5 fixed the schema).
- `test_annotated_tool_call_dispatches_with_wire_keys` FAILS — `isPaid` is dropped by `_split_tool_args` because `is_paid` is the Python kwarg name; handler receives default `None` values, but the assertion structure may still pass with the current naive handler. Strengthen the assertion if needed:

Modify the second test to verify the kwargs reached the handler:

```python
def test_annotated_tool_call_dispatches_with_wire_keys(annotated_app: Litestar) -> None:
    with TestClient(app=annotated_app) as client:
        rpc_result = rpc(
            client,
            "tools/call",
            {
                "name": "annotated_list",
                "arguments": {"isPaid": True, "preparedAfter": "2026-01-01T00:00:00"},
            },
        )
        from tests.integration.conftest import parse_tool_payload

        payload = parse_tool_payload(rpc_result)
        assert payload == {"is_paid": True, "prepared_after": "2026-01-01T00:00:00"}
```

Now re-run: failure expected as `is_paid` will be `None` (the default), not `True`.

- [ ] **Step 3: Update `_split_tool_args` in `executor.py`**

Open `litestar_mcp/executor.py`. Locate `_split_tool_args` (around line 380).

Add the alias import near the top of the file with the other `litestar_mcp` imports:

```python
from litestar_mcp._parameter_aliases import parameter_aliases
```

Replace the body of `_split_tool_args` with:

```python
def _split_tool_args(
    handler: BaseRouteHandler,
    tool_args: dict[str, Any],
    path_parameters: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Partition ``tool_args`` into (path_params, query_params, body_bytes).

    Wire-name aliases (``Parameter(query=...)`` / ``Parameter(alias=...)``) are
    rewritten to Python kwarg names before partitioning so the rest of the
    pipeline can match against ``handler.parsed_fn_signature.parameters``.

    Precedence for each key (post-rewrite):

    1. Path parameter — if the name appears in the route's path template.
    2. Scalar handler kwarg — if the name matches a non-``data`` signature
       parameter, it's bound as a query parameter so the native extractor
       parses it via the signature model.
    3. Body — if the handler declares a ``data`` parameter, leftover keys
       become members of the JSON body that Litestar decodes into the
       ``data`` struct.
    4. Dropped — if none of the above match.
    """
    aliases = parameter_aliases(handler)
    if aliases:
        tool_args = {aliases.get(k, k): v for k, v in tool_args.items()}

    sig_params = handler.parsed_fn_signature.parameters
    has_data = "data" in sig_params
    scalar_sig_names = {name for name in sig_params if name != "data"}

    path_values = {k: tool_args[k] for k in path_parameters if k in tool_args}
    remaining = {k: v for k, v in tool_args.items() if k not in path_values}

    query_payload = {k: v for k, v in remaining.items() if k in scalar_sig_names}

    body_payload: Any = {}
    if has_data:
        if "data" in remaining:
            body_payload = remaining["data"]
        else:
            body_payload = {k: v for k, v in remaining.items() if k not in query_payload}

    body = msgspec.json.encode(body_payload) if body_payload else b""
    return path_values, query_payload, body
```

The only behavioral change is the leading `aliases` rewrite; everything else is preserved verbatim.

- [ ] **Step 4: Run integration test to verify it passes**

Run: `uv run pytest tests/integration/test_annotated_parameters.py -v`
Expected: PASS (both tests).

Then run the full executor test module to make sure nothing regressed:

Run: `uv run pytest tests/ -k "executor or split_tool or annotated" -v`
Expected: PASS — alias-less handlers behave identically (helper returns `{}`, rewrite is a no-op).

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/executor.py tests/integration/test_annotated_parameters.py
git commit -m "feat(executor): map wire-name aliases to python kwargs (#52)"
```

---

## Task 7: Full test suite + changelog

**Files:**
- Modify: `CHANGELOG.md`
- Verify: full suite

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: All tests pass. Investigate and fix any regression before moving on.

- [ ] **Step 2: Update `CHANGELOG.md`**

Open `CHANGELOG.md`. Under the unreleased section (or add one if missing), append:

```markdown
### Fixed

- Schema builder now unwraps `Annotated[T, Parameter(...)]` parameters, recursing on the inner type and merging `Parameter` metadata (description, title, examples, and constraints `ge`/`le`/`gt`/`lt`/`min_length`/`max_length`/`pattern`/`multiple_of`/`const`) into the JSON Schema. Previously these parameters were emitted as `{"type": "object"}` with the `typing.Annotated` class docstring as description. ([#52](https://github.com/cofin/litestar-mcp/issues/52))
- Tool `inputSchema.properties` and `required` are now keyed by the wire name (`Parameter(query=...)` → `Parameter(alias=...)` → Python parameter name). Tool calls accept arguments under the same wire-name keys. ([#52](https://github.com/cofin/litestar-mcp/issues/52))
- Removed the unconditional `description = annotation.__doc__` overwrite in `generate_schema_for_handler` that was clobbering Pydantic / msgspec / dataclass schema descriptions with class docstrings. ([#52](https://github.com/cofin/litestar-mcp/issues/52))
```

- [ ] **Step 3: Final verification**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note Annotated parameter schema fix (#52)"
```

---

## Self-Review

- **Spec coverage:**
  - Unwrap `Annotated`, recurse, merge metadata → Tasks 1, 2, 3.
  - Wire-name keying → Task 5.
  - Remove `__doc__` clobber → Task 5.
  - Constraint mapping → Task 2 (`_META_FIELD_MAP`) + Task 3 tests.
  - Shared alias helper → Task 4.
  - Executor wire→python rewrite → Task 6.
  - Wire-name collision raises at schema build → Task 5.
  - Regression for bare types → Task 5 (`test_bare_types_regression`).
  - Repro from issue → Task 6 integration test.
  - CHANGELOG → Task 7.
  - Non-goals (body `Annotated`, header/cookie params, foreign metadata): explicitly excluded.

- **Placeholder scan:** No TBDs. Every code block is complete and runnable. Test assertions are concrete.

- **Type consistency:** `_unwrap_annotated` defined Task 1, imported in Task 3 (same module) and Task 4. `_merge_parameter_meta` defined Task 2, referenced Task 3. `parameter_aliases` defined Task 4, imported Task 6. `wire_to_python` is local to `generate_schema_for_handler` only. JSON Schema keyword names (`minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `minLength`, `maxLength`, `multipleOf`) match across `_META_FIELD_MAP` and Task 3 assertions.
