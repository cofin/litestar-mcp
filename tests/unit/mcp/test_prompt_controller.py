"""Unit tests for PromptController, PromptMessage, and @prompt."""

from typing import Any, cast

import pytest
from dishka import Provider, Scope, from_context, make_async_container, provide
from dishka.integrations.litestar import DishkaRouter, FromDishka, LitestarProvider, setup_dishka
from litestar import Litestar
from litestar.connection import ASGIConnection
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.testing import TestClient

from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.content import PromptMessage
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.prompt_controller import PromptController, prompt
from tests.unit.conftest import mcp_post


class DeniedByTestGuardError(PermissionDeniedException):
    """Raised when access is denied by test guard."""

    def __init__(self) -> None:
        super().__init__("Access denied by test guard")


def deny_all(connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler) -> None:
    """Guard that unconditionally rejects access."""
    raise DeniedByTestGuardError


class GuardedPromptController(PromptController):
    """Controller protected by deny_all guard."""

    prefix = "guarded"
    guards = [deny_all]

    @prompt(title="Secret Prompt", description="Secret description")
    def secret(self, secret_id: str) -> str:
        """Return a secret prompt."""
        return f"Secret: {secret_id}"


class InjectedPromptController(PromptController):
    """Controller with DI dependencies and instructions."""

    prefix = "ops"
    instructions = "RULES"
    dependencies = {"greeting": Provide(lambda: "Hello from DI", sync_to_thread=False)}

    @prompt(title="Triage Alert", description="Triage an alert.")
    def triage(self, service: str, greeting: str) -> str:
        """Return triage prompt with greeting."""
        return f"{greeting} -> triage {service}"

    @prompt(title="Multi Turn", description="Multi turn prompt.")
    def conversation(self) -> list[PromptMessage]:
        """Return user and assistant messages."""
        return [
            PromptMessage.user("How do I fix this?"),
            PromptMessage.assistant("Follow these steps."),
        ]


def test_prompt_message_rejects_system_role() -> None:
    """Verify PromptMessage rejects roles other than user and assistant."""
    with pytest.raises(ValueError, match="MCP prompt roles are 'user' or 'assistant'"):
        PromptMessage(role=cast("Any", "system"), content={"type": "text", "text": "sys"})


def test_prompt_controller_guards_enforced_on_get() -> None:
    """Verify controller guards reject unauthorized prompts/get calls with error."""
    config = MCPConfig(prompt_controllers=[GuardedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        res = mcp_post(
            client,
            "prompts/get",
            {"name": "guarded/secret", "arguments": {"secret_id": "top_secret"}},
        ).json()
        assert "error" in res
        assert "result" not in res
        assert res["error"]["code"] == -32603


def test_prompt_controller_dependencies_injected() -> None:
    """Verify dependencies are injected into prompt methods and omitted from argument schema."""
    config = MCPConfig(prompt_controllers=[InjectedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "prompts/list", {}).json()
        prompts = list_res["result"]["prompts"]
        triage_prompt = next(p for p in prompts if p["name"] == "ops/triage")
        arg_names = [a["name"] for a in triage_prompt.get("arguments", [])]
        assert "service" in arg_names
        assert "greeting" not in arg_names

        get_res = mcp_post(
            client,
            "prompts/get",
            {"name": "ops/triage", "arguments": {"service": "payments"}},
        ).json()
        assert "result" in get_res
        messages = get_res["result"]["messages"]
        assert len(messages) == 1
        assert "Hello from DI -> triage payments" in messages[0]["content"]["text"]


def test_prompt_messages_use_only_user_and_assistant_roles() -> None:
    """Verify prompt results contain strictly user and assistant roles."""
    config = MCPConfig(prompt_controllers=[InjectedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        get_res = mcp_post(
            client,
            "prompts/get",
            {"name": "ops/conversation", "arguments": {}},
        ).json()
        messages = get_res["result"]["messages"]
        roles = [m["role"] for m in messages]
        for role in roles:
            assert role in {"user", "assistant"}


def test_instructions_prefix_first_user_message() -> None:
    """Verify controller instructions are prepended to the first user text message."""
    config = MCPConfig(prompt_controllers=[InjectedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        get_res = mcp_post(
            client,
            "prompts/get",
            {"name": "ops/triage", "arguments": {"service": "cache"}},
        ).json()
        messages = get_res["result"]["messages"]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"]["text"].startswith("RULES\n\n")


def test_prompt_prefix_namespaces_name() -> None:
    """Verify prompt prefix namespaces the registered prompt name."""
    config = MCPConfig(prompt_controllers=[InjectedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "prompts/list", {}).json()
        prompt_names = [p["name"] for p in list_res["result"]["prompts"]]
        assert "ops/triage" in prompt_names
        assert "ops/conversation" in prompt_names


def test_prompt_route_not_directly_reachable() -> None:
    """Verify internal prompt routes return 403 when accessed directly over HTTP."""
    config = MCPConfig(prompt_controllers=[InjectedPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    ctrl_path = InjectedPromptController.path
    with TestClient(app=app) as client:
        res = client.post(f"{ctrl_path}/prompts/triage", json={"service": "auth"})
        assert res.status_code == 403


class _PromptFormatter:
    """Service injected into @prompt methods via Dishka."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def render(self, subject: str) -> str:
        return f"[{self.tag}] {subject}"


class _PromptFormatterProvider(Provider):
    """Dishka provider for _PromptFormatter."""

    scope = Scope.REQUEST
    request = from_context(provides=ASGIConnection, scope=Scope.REQUEST)

    @provide
    def provide_formatter(self) -> _PromptFormatter:
        return _PromptFormatter("dishka-prompt")


class DishkaPromptController(PromptController):
    """PromptController using FromDishka[T] on a @prompt method."""

    prefix = "dishka_ops"

    @prompt(title="Format Subject", description="Format subject using Dishka.")
    async def format_subject(self, subject: str, formatter: FromDishka[_PromptFormatter]) -> str:
        """Render subject with Dishka-injected formatter."""
        return formatter.render(subject)


@pytest.mark.anyio
async def test_prompt_controller_dishka_injection_and_schema_filtering() -> None:
    """Verify @prompt supports FromDishka[T], omits it from prompts/list, and is idempotent on DishkaRouter."""
    container = make_async_container(_PromptFormatterProvider(), LitestarProvider())
    config = MCPConfig(prompt_controllers=[DishkaPromptController])
    app = Litestar(plugins=[LitestarMCP(config=config)])
    setup_dishka(container, app)

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "prompts/list", {}).json()
        prompts = list_res["result"]["prompts"]
        entry = next(p for p in prompts if p["name"] == "dishka_ops/format_subject")
        arg_names = [a["name"] for a in entry.get("arguments", [])]
        assert arg_names == ["subject"]

        get_res = mcp_post(
            client,
            "prompts/get",
            {"name": "dishka_ops/format_subject", "arguments": {"subject": "deploy"}},
        ).json()
        assert "result" in get_res
        text = get_res["result"]["messages"][0]["content"]["text"]
        assert text == "[dishka-prompt] deploy"

    router = DishkaRouter(path="", route_handlers=[DishkaPromptController])
    app_router = Litestar(route_handlers=[router], plugins=[LitestarMCP(config=MCPConfig())])
    setup_dishka(container, app_router)
    with TestClient(app=app_router) as client:
        get_res = mcp_post(
            client,
            "prompts/get",
            {"name": "dishka_ops/format_subject", "arguments": {"subject": "via-router"}},
        ).json()
        assert get_res["result"]["messages"][0]["content"]["text"] == "[dishka-prompt] via-router"

    await container.close()
