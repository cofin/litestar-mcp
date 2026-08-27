"""Unit tests for A2APlugin, configuration, and skill discovery."""

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.plugin import A2APlugin
from litestar_mcp.mcp import LitestarMCP


def test_a2a_plugin_default_init() -> None:
    """Test A2APlugin initialized with defaults."""
    plugin = A2APlugin()
    assert plugin.config.name == "A2A Agent"
    assert plugin.config.base_path == "/a2a"
    assert plugin.registry is not None
    assert plugin.task_store is not None


def test_a2a_plugin_custom_config() -> None:
    """Test A2APlugin with custom configuration."""
    config = A2AConfig(
        name="Custom Agent",
        description="Custom Agent Description",
        version="2.0.0",
        base_path="/custom-a2a",
    )
    plugin = A2APlugin(config=config)
    assert plugin.config.name == "Custom Agent"
    assert plugin.config.version == "2.0.0"
    assert plugin.config.base_path == "/custom-a2a"


def test_a2a_plugin_skill_discovery() -> None:
    """Test automatic discovery of route handlers annotated with opt['a2a_skill']."""

    @get("/skills/add", opt={"a2a_skill": "add_numbers", "a2a_description": "Add two numbers"})
    def add(a: int, b: int) -> dict[str, int]:
        """Add two integers."""
        return {"sum": a + b}

    plugin = A2APlugin()
    _ = Litestar(route_handlers=[add], plugins=[plugin])

    assert "add_numbers" in plugin.discovered_skills
    skill = plugin.registry.get("add_numbers")
    assert skill is not None
    assert skill.name == "add_numbers"


def test_a2a_agent_card_endpoint() -> None:
    """Test dynamic GET /.well-known/agent-card.json endpoint."""

    @get("/skills/greet", opt={"a2a_skill": "greet", "a2a_description": "Greet a user"})
    def greet(name: str) -> dict[str, str]:
        """Return a greeting."""
        return {"greeting": f"Hello, {name}!"}

    plugin = A2APlugin(config=A2AConfig(name="Greeting Agent", version="1.2.3"))
    app = Litestar(route_handlers=[greet], plugins=[plugin])

    with TestClient(app=app) as client:
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        card_data = resp.json()
        assert card_data["name"] == "Greeting Agent"
        assert card_data["version"] == "1.2.3"
        assert card_data["protocolVersion"] == "1.0"
        assert len(card_data["skills"]) == 1
        assert card_data["skills"][0]["id"] == "greet"


def test_a2a_auto_export_mcp_tools() -> None:
    """Test auto-export of MCP tools into AgentCard when enabled."""

    @get("/tools/echo", opt={"mcp_tool": "mcp_echo"})
    def echo_tool(text: str) -> str:
        """Echo input text."""
        return text

    mcp_plugin = LitestarMCP()
    a2a_plugin = A2APlugin(config=A2AConfig(auto_export_mcp_tools=True))
    app = Litestar(
        route_handlers=[echo_tool],
        plugins=[mcp_plugin, a2a_plugin],
    )

    with TestClient(app=app) as client:
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        card_data = resp.json()
        skill_ids = [s["id"] for s in card_data["skills"]]
        assert "mcp_echo" in skill_ids
