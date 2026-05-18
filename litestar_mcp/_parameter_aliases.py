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

    Wire name is selected in precedence order:
        Parameter(query=...) > Parameter(header=...) > Parameter(cookie=...) > python_name

    Parameters with no Annotated metadata, or with metadata that does not set
    ``query``/``header``/``cookie``, are omitted.
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
            # Try wire name sources in precedence order
            wire_name = meta.query or meta.header or meta.cookie
            if wire_name and wire_name != python_name:
                aliases[wire_name] = python_name
                break
    return aliases
