"""Pins the Litestar internal-ish dispatch API that the MCP executor depends on.

Ch2 re-implements ``execute_tool`` on top of Litestar's own
``KwargsModel`` / ``SignatureModel`` pipeline. If upstream Litestar renames
or restructures any of the calls below, this test fails first with a clear
signal rather than producing confusing runtime errors deeper in the executor.
"""

from __future__ import annotations

import asyncio
from typing import Any

import msgspec
import pytest
from litestar import Litestar, post
from litestar.testing import RequestFactory

pytestmark = pytest.mark.unit


class _Payload(msgspec.Struct):
    title: str


@post("/x", sync_to_thread=False)
def _handler(data: _Payload) -> dict[str, str]:
    return {"title": data.title}


def test_litestar_dispatch_api_shape() -> None:
    """Smoke-check the full handler dispatch pipeline we plan to call.

    ``handler.create_kwargs_model(path_parameters=dict)`` must return a
    ``KwargsModel`` exposing async ``to_kwargs`` and ``resolve_dependencies``.
    ``handler.signature_model`` must expose
    ``parse_values_from_connection_kwargs``. The route object must expose
    ``path_parameters`` as a mapping (the executor looks them up to route
    tool_args into path params). If any of this breaks, Ch2's executor
    breaks loudly here.
    """
    app = Litestar(route_handlers=[_handler])

    handler = None
    route_path_parameters: dict[str, object] | None = None
    for route in app.routes:
        if getattr(route, "path", None) == "/x":
            route_path_parameters = getattr(route, "path_parameters", None)
            for candidate in getattr(route, "route_handlers", []):
                if "POST" in getattr(candidate, "http_methods", ()):
                    handler = candidate
    assert handler is not None, "test handler not registered"
    assert route_path_parameters is not None, "route.path_parameters missing"

    kwargs_model = handler.create_kwargs_model(path_parameters=route_path_parameters)
    assert hasattr(kwargs_model, "to_kwargs")
    assert hasattr(kwargs_model, "resolve_dependencies")

    sig_model = handler.signature_model
    assert hasattr(sig_model, "parse_values_from_connection_kwargs")

    async def exercise() -> Any:
        request = RequestFactory(app=app).post("/x", data={"title": "hi"})
        kwargs = await kwargs_model.to_kwargs(connection=request)
        cleanup = await kwargs_model.resolve_dependencies(request, kwargs)
        async with cleanup:
            return sig_model.parse_values_from_connection_kwargs(connection=request, kwargs=kwargs)

    parsed = asyncio.run(exercise())
    assert "data" in parsed
    assert parsed["data"].title == "hi"
