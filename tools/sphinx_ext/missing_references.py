"""Sphinx extension for changelog and change directives."""

# ruff: noqa: PLR0911
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from docutils.nodes import Element, Node
    from sphinx.addnodes import pending_xref
    from sphinx.application import Sphinx
    from sphinx.environment import BuildEnvironment


def _get_module_ast(source_file: str) -> ast.AST | ast.Module:
    return ast.parse(Path(source_file).read_text(encoding="utf-8"))


def _get_import_nodes(nodes: list[ast.stmt]) -> Generator[ast.Import | ast.ImportFrom, None, None]:
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            yield from _get_import_nodes(node.body)


def get_module_global_imports(module_import_path: str, reference_target_source_obj: str) -> set[str]:
    """Return a set of names that are imported globally within the containing module of ``reference_target_source_obj``,
    including imports in ``if TYPE_CHECKING`` blocks.
    """
    module = importlib.import_module(module_import_path)
    obj = getattr(module, reference_target_source_obj)
    tree = _get_module_ast(inspect.getsourcefile(obj))  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    import_nodes = _get_import_nodes(tree.body)  # type: ignore[attr-defined]
    return {path.asname or path.name for import_node in import_nodes for path in import_node.names}


def _resolve_local_reference(module_path: str, target: str) -> bool:
    """Attempt to resolve a reference within the local codebase.

    Args:
        module_path: The module path (e.g., 'advanced_alchemy.base')
        target: The target class/attribute name

    Returns:
        bool: True if reference exists, False otherwise
    """
    try:
        module = importlib.import_module(module_path)
        if "." in target:
            # Handle fully qualified names (e.g., advanced_alchemy.base.BasicAttributes)
            parts = target.split(".")
            current = module
            for part in parts:
                current = getattr(current, part)
            return True
        return hasattr(module, target)
    except (ImportError, AttributeError):
        return False


def _resolve_litestar_reference(target: str) -> bool:
    """Attempt to resolve Litestar references.

    Args:
        target: The target class/attribute name

    Returns:
        bool: True if reference exists, False otherwise
    """
    try:
        import litestar
        from litestar import connection, datastructures, di, dto, params, response, router, types

        # Handle common Litestar classes
        common_classes = {
            "Litestar",
            "State",
            "Scope",
            "Message",
            "AppConfig",
            "BeforeMessageSendHookHandler",
            "connection.Request",
            "connection.WebSocket",
            "types.Guard",
            "types.Middleware",
            "types.Scope",
            "di.Provide",
            "dto.base_dto.AbstractDTO",
            "params.Parameter",
            "router.Router",
            "response.Response",
        }

        if target in common_classes:
            return True

        # Handle module-prefixed references
        if target.startswith("datastructures."):
            _, attr = target.split(".", 1)
            return hasattr(datastructures, attr)
        if target.startswith("config.app."):
            return True  # These are valid Litestar config references
        if target.startswith("connection."):
            _, attr = target.split(".", 1)
            return hasattr(connection, attr)
        if target.startswith("types."):
            _, attr = target.split(".", 1)
            return hasattr(types, attr)
        if target.startswith("di."):
            _, attr = target.split(".", 1)
            return hasattr(di, attr)
        if target.startswith("dto."):
            parts = target.split(".")
            if len(parts) >= 3 and parts[1] == "base_dto":
                return True  # dto.base_dto.AbstractDTO
            return hasattr(dto, parts[1])
        if target.startswith("params."):
            _, attr = target.split(".", 1)
            return hasattr(params, attr)
        if target.startswith("router."):
            _, attr = target.split(".", 1)
            return hasattr(router, attr)
        if target.startswith("response."):
            _, attr = target.split(".", 1)
            return hasattr(response, attr)

        return hasattr(litestar, target)
    except ImportError:
        return False


def _resolve_serialization_reference(target: str) -> bool:
    """Attempt to resolve serialization-related references.

    Args:
        target: The target class/attribute name

    Returns:
        bool: True if reference exists, False otherwise
    """
    serialization_attrs = {"decode_json", "encode_json"}
    return target in serialization_attrs


def _resolve_click_reference(target: str) -> bool:
    """Attempt to resolve Click references.

    Args:
        target: The target class/attribute name

    Returns:
        bool: True if reference exists, False otherwise
    """
    try:
        import click

        if target == "Group":
            return True
        return hasattr(click, target)
    except ImportError:
        return False


def on_warn_missing_reference(app: Sphinx, domain: str, node: Node) -> bool | None:
    if node.tagname != "pending_xref":  # type: ignore[attr-defined]
        return None

    if not hasattr(node, "attributes"):
        return None

    # Wrap the main logic in a try-except to catch potential AttributeErrors (e.g., startswith on None)
    try:
        attributes = node.attributes  # type: ignore[attr-defined,unused-ignore]
        target = attributes["reftarget"]  # pyright: ignore
        ref_type = attributes.get("reftype")  # pyright: ignore

        # Handle TypeVar references
        if hasattr(target, "__class__") and target.__class__.__name__ == "TypeVar":  # pyright: ignore
            return True

        if ref_type in {"class", "obj"} and (
            (
                isinstance(target, str)
                and target.startswith(
                    (
                        "datastructures.",
                        "config.app.",
                        "connection.",
                        "types.",
                        "di.",
                        "dto.",
                        "params.",
                        "router.",
                        "response.",
                    )
                )
            )
            or target
            in {
                "Litestar",
                "State",
                "Scope",
                "Message",
                "AppConfig",
                "BeforeMessageSendHookHandler",
                "FieldDefinition",
                "ImproperConfigurationError",
                "connection.Request",
                "connection.WebSocket",
                "types.Guard",
                "types.Middleware",
                "types.Scope",
                "di.Provide",
                "dto.base_dto.AbstractDTO",
                "params.Parameter",
                "router.Router",
                "response.Response",
            }
        ):
            return _resolve_litestar_reference(target)  # pyright: ignore

        # Handle serialization references
        if ref_type in {"attr", "meth"} and _resolve_serialization_reference(target):  # pyright: ignore
            return True

        # Handle Click references
        if ref_type == "class" and _resolve_click_reference(target):  # pyright: ignore
            return True

    except AttributeError:
        # Catch the specific error (likely startswith on None) and allow Sphinx to handle the warning normally
        return None

    return None


def on_missing_reference(app: Sphinx, env: BuildEnvironment, node: pending_xref, contnode: Element) -> Element | None:
    """Handle missing references by attempting to resolve them through different methods.

    Args:
        app: The Sphinx application instance
        env: The Sphinx build environment
        node: The pending cross-reference node
        contnode: The content node

    Returns:
        Element | None: The resolved reference node if found, None otherwise
    """
    if not hasattr(node, "attributes"):
        return None

    attributes = node.attributes  # type: ignore[attr-defined,unused-ignore]
    target = attributes["reftarget"]

    # Remove this check since it's causing issues
    if not isinstance(target, str):
        return None

    py_domain = env.domains["py"]

    # autodoc sometimes incorrectly resolves these types, so we try to resolve them as py:data first and fall back to any
    new_node = py_domain.resolve_xref(env, node["refdoc"], app.builder, "data", target, node, contnode)
    if new_node is None:
        resolved_xrefs = py_domain.resolve_any_xref(env, node["refdoc"], app.builder, target, node, contnode)
        for ref in resolved_xrefs:
            if ref:
                return ref[1]
    return new_node


def on_env_before_read_docs(app: Sphinx, env: BuildEnvironment, docnames: set[str]) -> None:
    tmp_examples_path = Path.cwd() / "docs/_build/_tmp_examples"
    tmp_examples_path.mkdir(exist_ok=True, parents=True)
    env.tmp_examples_path = tmp_examples_path  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]


def setup(app: Sphinx) -> dict[str, bool]:
    app.connect("env-before-read-docs", on_env_before_read_docs)
    app.connect("missing-reference", on_missing_reference)
    app.connect("warn-missing-reference", on_warn_missing_reference)
    app.add_config_value("ignore_missing_refs", default={}, rebuild="")
    return {"parallel_read_safe": True, "parallel_write_safe": True}
