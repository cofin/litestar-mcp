"""Tests for helper utilities."""

import inspect

from dishka.integrations.litestar import FromDishka, inject

from litestar_mcp.utils import get_handler_function
from tests.unit.conftest import create_app_with_handler


class DummyService:
    """Simple marker dependency for Dishka wrapping tests."""


def test_get_handler_function_unwraps_dishka_injected_handlers() -> None:
    """Dishka-injected handlers should resolve to the original callable."""

    @inject
    async def load_widget(service: FromDishka[DummyService]) -> dict[str, bool]:
        return {"ok": service is not None}

    _app, handler = create_app_with_handler(load_widget)

    resolved = get_handler_function(handler)
    params = inspect.signature(resolved).parameters

    assert "service" in params
    assert "request" not in params
    assert resolved is load_widget.__dishka_orig_func__  # type: ignore[attr-defined]
