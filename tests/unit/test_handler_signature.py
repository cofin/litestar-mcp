"""Shared handler signature introspection coverage."""

from typing import Annotated, Any

import pytest
from litestar import Request
from litestar.di import Provide
from litestar.params import Parameter

from litestar_mcp.utils.handler_signature import extract_advertised_handler_arguments
from tests.unit.conftest import create_app_with_handler

pytestmark = pytest.mark.unit


def test_extract_advertised_handler_arguments_filters_di_reserved_and_path_params() -> None:
    async def provide_secret() -> str:
        return "secret"

    def handler(
        item_id: str,
        request: Request[Any, Any, Any],
        secret: str,
        q: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        return {"item_id": item_id, "q": q, "limit": limit, "secret": secret, "path": request.url.path}

    _, route_handler = create_app_with_handler(
        handler,
        route_path="/items/{item_id:str}",
        mcp_prompt="item_prompt",
        dependencies={"secret": Provide(provide_secret)},
    )

    args = extract_advertised_handler_arguments(route_handler, path_parameters={"item_id"})
    assert [(arg["name"], arg["required"]) for arg in args] == [("q", True), ("limit", False)]


def test_extract_advertised_handler_arguments_uses_query_alias_and_docstring_description() -> None:
    def handler(
        is_paid: Annotated[bool, Parameter(query="isPaid")],
        page_size: Annotated[int, Parameter(query="pageSize")] = 50,
    ) -> dict[str, Any]:
        """List invoices.

        Args:
            is_paid: Whether the invoice is paid.
            page_size: Number of invoices to return.
        """
        return {"is_paid": is_paid, "page_size": page_size}

    _, route_handler = create_app_with_handler(handler)
    args = extract_advertised_handler_arguments(route_handler)
    assert args == [
        {"name": "isPaid", "description": "Whether the invoice is paid.", "required": True},
        {"name": "pageSize", "description": "Number of invoices to return.", "required": False},
    ]


def test_extract_advertised_handler_arguments_filters_litestar_signature_namespace() -> None:
    def handler(headers: dict[str, str], text: str) -> dict[str, str]:
        return {"text": text, "headers": str(headers)}

    _, route_handler = create_app_with_handler(handler)
    args = extract_advertised_handler_arguments(route_handler)
    assert [arg["name"] for arg in args] == ["text"]


def test_extract_advertised_handler_arguments_filters_dishka_resolved_provider_params() -> None:
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.litestar import LitestarProvider, setup_dishka
    from litestar import Litestar, get

    from tests.unit.conftest import get_handler_from_app

    class Driver:
        pass

    class TaskService:
        pass

    class DishkaProvider(Provider):
        scope = Scope.REQUEST

        @provide
        def driver(self) -> Driver:
            return Driver()

    async def provide_task_service(driver: Driver) -> TaskService:
        return TaskService()

    @get(
        "/hello",
        opt={"mcp_tool": "hello"},
        dependencies={"task_service": Provide(provide_task_service)},
        sync_to_thread=False,
    )
    def hello(name: str) -> dict[str, str]:
        return {"hello": name}

    app = Litestar(route_handlers=[hello])
    container = make_async_container(LitestarProvider(), DishkaProvider())
    setup_dishka(container=container, app=app)
    route_handler = get_handler_from_app(app, "/hello")

    args = extract_advertised_handler_arguments(route_handler)
    assert [arg["name"] for arg in args] == ["name"]
