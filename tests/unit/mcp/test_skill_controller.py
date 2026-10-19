"""Unit tests for SkillController tools, prompts, guards, DI, and metadata."""

import json
from typing import Annotated, Any, Literal

import pytest
from dishka import Provider, Scope, from_context, make_async_container, provide
from dishka.integrations.litestar import FromDishka, LitestarProvider, setup_dishka
from litestar import Litestar
from litestar.connection import ASGIConnection
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.params import Dependency
from litestar.testing import TestClient

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.prompt_controller import prompt
from litestar_mcp.mcp.skill_controller import SkillController
from tests.unit.conftest import mcp_post


class DeniedByTestGuardError(PermissionDeniedException):
    """Raised when tool access is denied by test guard."""

    def __init__(self) -> None:
        super().__init__("Tool access denied")


def deny_all(connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler) -> None:
    """Guard rejecting all requests."""
    raise DeniedByTestGuardError


class GuardedSkillController(SkillController):
    """Skill with controller-level guard."""

    name = "guarded_skill"
    guards = [deny_all]

    @tool(description="A guarded tool.")
    def secure_action(self, target: str) -> str:
        """Run a secure action."""
        return f"acted on {target}"


class StateAndDISkillController(SkillController):
    """Skill sharing instance state and DI dependencies."""

    name = "state_skill"
    description = "Demonstrates state sharing and DI."
    tags = ("state", "demo")
    examples = ("Run state test",)
    dependencies = {"service_token": Provide(lambda: "secret-token-123", sync_to_thread=False)}

    def __init__(self, owner: Any = None) -> None:
        super().__init__(owner)
        self.call_count = 0

    @tool(description="Increment and return count.")
    def bump(
        self,
        step: int,
        service_token: str,
        mode: Literal["fast", "slow"] = "fast",
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Increment count and return state."""
        self.call_count += step
        return {
            "count": self.call_count,
            "token": service_token,
            "mode": mode,
            "tenant": ctx.tenant_id if ctx else None,
        }

    @prompt(title="State Summary", description="Show current state.")
    def status(self) -> str:
        """Return status string with current count."""
        return f"Current count is {self.call_count}"


def test_to_agent_skill_projection() -> None:
    """Verify SkillController.to_agent_skill projects metadata attributes."""
    skill = StateAndDISkillController()
    proj = skill.to_agent_skill()
    assert proj["id"] == "state_skill"
    assert proj["name"] == "state_skill"
    assert proj["description"] == "Demonstrates state sharing and DI."
    assert proj["tags"] == ["state", "demo"]
    assert proj["examples"] == ["Run state test"]


def test_skill_tool_guards_apply() -> None:
    """Verify controller guards reject tools/call with isError=True and 403."""
    config = MCPConfig(skill_controllers=[GuardedSkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        res = mcp_post(
            client,
            "tools/call",
            {"name": "secure_action", "arguments": {"target": "prod"}},
        ).json()
        assert "result" in res
        assert res["result"]["isError"] is True
        error_text = res["result"]["content"][0]["text"]
        assert "Tool access denied" in error_text


def test_skill_tool_dependencies_and_schema_matching() -> None:
    """Verify controller dependencies are injected and inputSchema matches Tool.parameters."""
    config = MCPConfig(skill_controllers=[StateAndDISkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "tools/list", {}).json()
        tools = list_res["result"]["tools"]
        bump_tool = next(t for t in tools if t["name"] == "bump")

        assert "step" in bump_tool["inputSchema"]["properties"]
        assert "mode" in bump_tool["inputSchema"]["properties"]
        assert "service_token" not in bump_tool["inputSchema"]["properties"]
        assert "ctx" not in bump_tool["inputSchema"]["properties"]
        assert "request" not in bump_tool["inputSchema"]["properties"]

        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "bump", "arguments": {"step": 5, "mode": "slow"}},
        ).json()
        assert call_res["result"]["isError"] is False
        output = call_res["result"]["content"][0]["text"]
        assert "secret-token-123" in output
        assert "slow" in output


def test_skill_tool_shares_controller_instance_state() -> None:
    """Verify tools and prompts on the same controller share instance state."""
    config = MCPConfig(skill_controllers=[StateAndDISkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "bump", "arguments": {"step": 10}},
        ).json()
        assert call_res["result"]["isError"] is False

        prompt_res = mcp_post(
            client,
            "prompts/get",
            {"name": "state_skill/status", "arguments": {}},
        ).json()
        messages = prompt_res["result"]["messages"]
        assert "Current count is 10" in messages[0]["content"]["text"]


class _CustomService:
    """Non-serializable domain service used to verify DI parameters bypass msgspec schema compilation."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def format_value(self, item: str) -> str:
        return f"{self.prefix}:{item}"


class _InjectMarker:
    """Third-party Annotated DI marker (e.g. wireup.Inject / aioinject.Inject)."""

    __inject_marker__ = True


class _DependsMarker:
    """Callable DI default marker (e.g. FastAPI/FastDepends Depends(fn))."""

    def __init__(self, dependency: Any) -> None:
        self.dependency = dependency


class _SimpleContainer:
    """Minimal third-party container supporting .get(type_hint)."""

    def __init__(self, mapping: dict[Any, Any]) -> None:
        self._mapping = mapping

    def get(self, key: Any) -> Any:
        return self._mapping[key]


class _AuditProvider(Provider):
    """Dishka provider supplying _CustomService."""

    scope = Scope.REQUEST
    request = from_context(provides=ASGIConnection, scope=Scope.REQUEST)

    @provide
    def provide_service(self) -> _CustomService:
        return _CustomService("dishka-svc")


_DEFAULT_DEPENDS_SVC: Any = _DependsMarker(lambda: _CustomService("depends-fn"))


class UniversalDISkillController(SkillController):
    """Controller testing Litestar Provide, Dishka FromDishka, and generic Inject/Depends markers."""

    name = "universal_di"
    description = "Universal DI skill controller."
    dependencies = {
        "controller_svc": Provide(lambda: _CustomService("litestar-ctrl"), sync_to_thread=False),
    }

    @tool(
        description="Use Litestar Provide and Dependency markers.",
        dependencies={"tool_svc": Provide(lambda: _CustomService("litestar-tool"), sync_to_thread=False)},
    )
    def with_litestar_di(
        self,
        item: str,
        controller_svc: _CustomService,
        tool_svc: _CustomService,
        explicit_dep: Annotated[str, Dependency(skip_validation=True)] = "default-dep",
    ) -> dict[str, str]:
        """Run tool with Litestar native DI."""
        return {
            "ctrl": controller_svc.format_value(item),
            "tool": tool_svc.format_value(item),
            "explicit": explicit_dep,
        }

    @tool(description="Use Dishka FromDishka[T] without DishkaRouter.")
    async def with_dishka(
        self,
        item: str,
        svc: FromDishka[_CustomService],
    ) -> dict[str, str]:
        """Run tool with Dishka FromDishka service."""
        return {"formatted": svc.format_value(item)}

    @tool(description="Use third-party Annotated Inject and Depends markers.")
    async def with_third_party_di(
        self,
        item: str,
        injected_svc: Annotated[_CustomService, _InjectMarker()],
        dep_svc: _CustomService = _DEFAULT_DEPENDS_SVC,
    ) -> dict[str, str]:
        """Run tool with third-party DI markers."""
        return {
            "injected": injected_svc.format_value(item),
            "depends": dep_svc.format_value(item),
        }


@pytest.mark.anyio
async def test_universal_di_litestar_provide_and_direct_execute() -> None:
    """Verify Litestar Provide works on SkillController tools over MCP HTTP and direct Tool.execute()."""
    ctrl = UniversalDISkillController()
    tools_by_name = {t.name: t for t in ctrl.get_tools()}
    litestar_tool = tools_by_name["with_litestar_di"]

    assert set(litestar_tool.parameters["properties"].keys()) == {"item"}
    assert litestar_tool.parameters.get("required") == ["item"]

    direct_result = await litestar_tool.execute({"item": "direct"})
    assert direct_result == {
        "ctrl": "litestar-ctrl:direct",
        "tool": "litestar-tool:direct",
        "explicit": "default-dep",
    }

    config = MCPConfig(skill_controllers=[UniversalDISkillController])
    app = Litestar(
        plugins=[LitestarMCP(config=config)],
        dependencies={"explicit_dep": Provide(lambda: "from-app-di", sync_to_thread=False)},
    )
    with TestClient(app=app) as client:
        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "with_litestar_di", "arguments": {"item": "http"}},
        ).json()
        assert call_res["result"]["isError"] is False
        payload = json.loads(call_res["result"]["content"][0]["text"])
        assert payload == {
            "ctrl": "litestar-ctrl:http",
            "tool": "litestar-tool:http",
            "explicit": "from-app-di",
        }


@pytest.mark.anyio
async def test_universal_di_dishka_http_and_agent_runtime() -> None:
    """Verify FromDishka[T] works on SkillController tools and standalone @tool over MCP HTTP and Tool.execute()."""
    container = make_async_container(_AuditProvider(), LitestarProvider())

    @tool(description="Standalone Dishka tool.")
    async def standalone_dishka_tool(query: str, svc: FromDishka[_CustomService]) -> dict[str, str]:
        return {"result": svc.format_value(query)}

    assert set(standalone_dishka_tool.parameters["properties"].keys()) == {"query"}

    ctrl = UniversalDISkillController()
    dishka_tool = next(t for t in ctrl.get_tools() if t.name == "with_dishka")
    assert set(dishka_tool.parameters["properties"].keys()) == {"item"}

    runtime_out = await dishka_tool.execute(
        {"item": "agent"},
        context=ToolContext(container=container),
    )
    assert runtime_out == {"formatted": "dishka-svc:agent"}

    config = MCPConfig(skill_controllers=[UniversalDISkillController])
    plugin = LitestarMCP(config=config)
    plugin.register_tool(standalone_dishka_tool)
    app = Litestar(plugins=[plugin])
    setup_dishka(container, app)

    with TestClient(app=app) as client:
        tools_list = mcp_post(client, "tools/list", {}).json()["result"]["tools"]
        with_dishka_schema = next(t for t in tools_list if t["name"] == "with_dishka")["inputSchema"]
        assert set(with_dishka_schema["properties"].keys()) == {"item"}

        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "with_dishka", "arguments": {"item": "mcp-http"}},
        ).json()
        assert call_res["result"]["isError"] is False
        assert json.loads(call_res["result"]["content"][0]["text"]) == {"formatted": "dishka-svc:mcp-http"}

        standalone_res = mcp_post(
            client,
            "tools/call",
            {"name": "standalone_dishka_tool", "arguments": {"query": "standalone"}},
        ).json()
        assert standalone_res["result"]["isError"] is False
        assert json.loads(standalone_res["result"]["content"][0]["text"]) == {"result": "dishka-svc:standalone"}

    await container.close()


@pytest.mark.anyio
async def test_universal_di_third_party_markers_and_custom_resolver() -> None:
    """Verify Annotated[T, Inject()], Depends(fn), container state, and custom dependency_resolver hook."""
    ctrl = UniversalDISkillController()
    tp_tool = next(t for t in ctrl.get_tools() if t.name == "with_third_party_di")
    assert set(tp_tool.parameters["properties"].keys()) == {"item"}

    container = _SimpleContainer({_CustomService: _CustomService("wireup-box")})
    out_from_container = await tp_tool.execute(
        {"item": "alpha"},
        context=ToolContext(container=container),
    )
    assert out_from_container == {
        "injected": "wireup-box:alpha",
        "depends": "depends-fn:alpha",
    }

    out_from_resolver = await tp_tool.execute(
        {"item": "beta"},
        context=ToolContext(
            dependency_resolver=lambda name, _hint, _ctx: _CustomService(f"custom-{name}"),
        ),
    )
    assert out_from_resolver == {
        "injected": "custom-injected_svc:beta",
        "depends": "custom-dep_svc:beta",
    }

    config = MCPConfig(skill_controllers=[UniversalDISkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])
    app.state.wireup_container = container

    with TestClient(app=app) as client:
        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "with_third_party_di", "arguments": {"item": "http-tp"}},
        ).json()
        assert call_res["result"]["isError"] is False
        assert json.loads(call_res["result"]["content"][0]["text"]) == {
            "injected": "wireup-box:http-tp",
            "depends": "depends-fn:http-tp",
        }
