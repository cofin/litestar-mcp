"""Tests for the CLI module."""

from typing import Any

from click.testing import CliRunner
from litestar import Litestar, get, post

from litestar_mcp import LitestarMCP, mcp_resource, mcp_tool
from litestar_mcp.cli import list_tools, mcp_cli


class TestCLI:
    """Test suite for CLI functionality."""

    def test_list_tools_with_discovered_tools(self) -> None:
        """Test that list-tools command shows discovered tools."""

        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            """Get all users from the system."""
            return [{"id": 1, "name": "Alice"}]

        @post("/analyze", opt={"mcp_tool": "analyze_data"})
        async def analyze(data: dict[str, Any]) -> dict[str, str]:
            """Analyze the provided data."""
            return {"result": "analyzed"}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[get_users, analyze])

        runner = CliRunner()
        result = runner.invoke(list_tools, obj=app)

        assert result.exit_code == 0
        assert "Discovered 2 tools:" in result.output
        assert "list_users" in result.output
        assert "analyze_data" in result.output
        assert "Get all users from the system" in result.output
        assert "Analyze the provided data" in result.output

    def test_list_tools_no_tools_discovered(self) -> None:
        """Test list-tools command when no tools are discovered."""

        @get("/regular")
        async def regular_route() -> dict[str, str]:
            return {"message": "regular"}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[regular_route])

        runner = CliRunner()
        result = runner.invoke(list_tools, obj=app)

        assert result.exit_code == 0
        assert "No MCP tools discovered" in result.output

    def test_list_tools_with_decorator_marked_tools(self) -> None:
        """Test list-tools with tools marked using decorators."""

        @mcp_tool(name="user_manager")
        @get("/users")
        async def get_users() -> list[dict[str, Any]]:
            """Manage users in the system."""
            return [{"id": 1, "name": "Alice"}]

        @mcp_resource(name="app_config")
        @get("/config")
        async def get_config() -> dict[str, Any]:
            """Get application configuration."""
            return {"debug": True}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[get_users, get_config])

        runner = CliRunner()
        result = runner.invoke(list_tools, obj=app)

        assert result.exit_code == 0
        assert "Discovered 1 tools:" in result.output  # Only tools, not resources
        assert "user_manager" in result.output
        assert "Manage users in the system" in result.output

    def test_run_command_discovery(self) -> None:
        """Test that run command discovers available tools."""

        @get("/greet", opt={"mcp_tool": "greeting"})
        async def greet(name: str, loud: bool = False) -> dict[str, str]:
            """Generate a greeting message."""
            message = f"Hello, {name}!"
            if loud:
                message = message.upper()
            return {"greeting": message}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[greet])

        runner = CliRunner()

        # Test that the greeting command is available
        result = runner.invoke(mcp_cli, ["run", "greeting", "--help"], obj=app)
        assert result.exit_code == 0
        assert "Generate a greeting message" in result.output
        assert "--name" in result.output
        assert "--loud" in result.output

    def test_run_command_execution_success(self) -> None:
        """Test successful tool execution via run command."""

        @get("/greet", opt={"mcp_tool": "greeting"})
        async def greet(name: str, loud: bool = False) -> dict[str, str]:
            """Generate a greeting message."""
            message = f"Hello, {name}!"
            if loud:
                message = message.upper()
            return {"greeting": message}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[greet])

        runner = CliRunner()

        # Test execution with required parameter
        result = runner.invoke(mcp_cli, ["run", "greeting", "--name", "World"], obj=app)
        assert result.exit_code == 0
        assert '"greeting": "Hello, World!"' in result.output

        # Test execution with optional parameter
        result = runner.invoke(mcp_cli, ["run", "greeting", "--name", "World", "--loud"], obj=app)
        assert result.exit_code == 0
        assert '"greeting": "HELLO, WORLD!"' in result.output

    def test_run_command_missing_required_args(self) -> None:
        """Test run command with missing required arguments."""

        @get("/greet", opt={"mcp_tool": "greeting"})
        async def greet(name: str) -> dict[str, str]:
            return {"greeting": f"Hello, {name}!"}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[greet])

        runner = CliRunner()

        # Test execution without required parameter should fail
        result = runner.invoke(mcp_cli, ["run", "greeting"], obj=app)
        assert result.exit_code == 2  # Click returns 2 for missing arguments
        # Click will show usage/help message for missing required arguments

    def test_run_command_json_parameter_parsing(self) -> None:
        """Test JSON parameter parsing for complex types."""

        @post("/process", opt={"mcp_tool": "data_processor"})
        async def process_data(data: dict[str, Any]) -> dict[str, Any]:
            """Process complex data structures."""
            return {"processed": True, "input_keys": list(data.keys())}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[process_data])

        runner = CliRunner()

        # Test with JSON string parameter
        json_data = '{"name": "test", "value": 42}'
        result = runner.invoke(mcp_cli, ["run", "data_processor", "--data", json_data], obj=app)

        assert result.exit_code == 0
        assert '"processed": true' in result.output
        # Check both keys are present (Rich may format JSON differently)
        assert '"name"' in result.output
        assert '"value"' in result.output
        assert '"input_keys"' in result.output

    def test_run_command_cli_context_limitation(self) -> None:
        """Test run command with tools that can't run in CLI context."""
        from litestar import Request

        @get("/request-dependent", opt={"mcp_tool": "request_tool"})
        async def request_dependent_tool(request: Request[Any, Any, Any], user_id: int) -> dict[str, Any]:
            """Tool that depends on request context."""
            return {"user_id": user_id, "path": str(request.url.path)}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[request_dependent_tool])

        runner = CliRunner()

        # This should fail because the tool requires request context
        result = runner.invoke(mcp_cli, ["run", "request_tool", "--user_id", "123"], obj=app)
        assert result.exit_code == 2  # Click returns 2 for missing arguments
        # The request parameter will be treated as a missing required argument

    def test_run_nonexistent_tool(self) -> None:
        """Test running a tool that doesn't exist."""

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin])

        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["run", "nonexistent"], obj=app)
        assert result.exit_code == 2  # Click's error code for unknown command

    def test_complex_type_parameter_help_text(self) -> None:
        """Test that complex type parameters show appropriate help text."""

        @post("/complex", opt={"mcp_tool": "complex_tool"})
        async def complex_tool(data: dict[str, Any], items: list[str]) -> str:
            """Tool with complex parameter types."""
            return f"Processed {len(items)} items from data"

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[complex_tool])

        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["run", "complex_tool", "--help"], obj=app)
        assert result.exit_code == 0
        assert "Pass as JSON string if complex type" in result.output

    def test_string_output_formatting(self) -> None:
        """Test that string outputs are formatted correctly."""

        @get("/simple", opt={"mcp_tool": "simple_string"})
        async def simple_string_tool(message: str) -> str:
            """Return a simple string."""
            return f"Processed: {message}"

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[simple_string_tool])

        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["run", "simple_string", "--message", "test"], obj=app)
        assert result.exit_code == 0
        assert "Processed: test" in result.output
        # Should not be JSON formatted for string output
        assert '{"' not in result.output

    def test_no_plugin_configured(self) -> None:
        """Test CLI behavior when no MCP plugin is configured."""

        app = Litestar()  # No MCP plugin

        runner = CliRunner()

        result = runner.invoke(list_tools, obj=app)
        assert result.exit_code == 0
        assert "No MCP tools discovered" in result.output

    def test_run_command_with_boolean_flags(self) -> None:
        """Test run command with boolean flag arguments."""

        @get("/toggle", opt={"mcp_tool": "toggle_feature"})
        async def toggle_feature(enabled: bool = False) -> dict[str, bool]:
            """Toggle a feature on or off."""
            return {"enabled": enabled}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[toggle_feature])

        runner = CliRunner()

        # Test with flag enabled
        result = runner.invoke(mcp_cli, ["run", "toggle_feature", "--enabled"], obj=app)
        assert result.exit_code == 0
        assert '"enabled": true' in result.output

        # Test with flag disabled (default)
        result = runner.invoke(mcp_cli, ["run", "toggle_feature"], obj=app)
        assert result.exit_code == 0
        assert '"enabled": false' in result.output

    def test_run_command_invalid_tool_name(self) -> None:
        """Test run command with invalid tool name."""

        @get("/valid", opt={"mcp_tool": "valid_tool"})
        async def valid_tool() -> dict[str, str]:
            return {"status": "ok"}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[valid_tool])

        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["run", "nonexistent_tool"], obj=app)
        assert result.exit_code != 0
        assert "No such command 'nonexistent_tool'" in result.output

    def test_run_command_execution_exception(self) -> None:
        """Test run command when tool execution raises an exception."""

        @get("/error", opt={"mcp_tool": "error_tool"})
        async def error_tool() -> dict[str, str]:
            """Tool that always raises an error."""
            msg = "Simulated error"
            raise ValueError(msg)

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[error_tool])

        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["run", "error_tool"], obj=app)
        assert result.exit_code != 0
        assert "Error executing tool" in result.output

    def test_run_command_with_resource_tool(self) -> None:
        """Test run command works with resource-type tools."""

        @get("/config", opt={"mcp_resource": "app_config"})
        async def get_config() -> dict[str, Any]:
            """Get application configuration."""
            return {"debug": True, "version": "1.0.0"}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[get_config])

        runner = CliRunner()

        # Resource tools should also be executable via CLI
        result = runner.invoke(mcp_cli, ["run", "app_config"], obj=app)
        assert result.exit_code == 0
        assert '"debug": true' in result.output
        assert '"version": "1.0.0"' in result.output

    def test_list_tools_output_formatting(self) -> None:
        """Test the formatting of list-tools output."""

        @get("/tool1", opt={"mcp_tool": "short_name"})
        async def tool_with_short_name() -> str:
            """Short description."""
            return "result"

        @get("/tool2", opt={"mcp_tool": "very_long_tool_name_that_might_affect_formatting"})
        async def tool_with_long_name() -> str:
            """A much longer description that might affect the formatting of the output table."""
            return "result"

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[tool_with_short_name, tool_with_long_name])

        runner = CliRunner()
        result = runner.invoke(list_tools, obj=app)

        assert result.exit_code == 0
        assert "short_name" in result.output
        assert "very_long_tool_name_that_might_affect_formatting" in result.output
        assert "Short description" in result.output
        assert "much longer description" in result.output

    def test_mcp_cli_help_command(self) -> None:
        """Test that MCP CLI shows help information."""
        runner = CliRunner()

        result = runner.invoke(mcp_cli, ["--help"])
        assert result.exit_code == 0
        assert "MCP CLI for Litestar applications" in result.output
        assert "list-tools" in result.output
        assert "run" in result.output

    def test_list_tools_help_command(self) -> None:
        """Test that list-tools shows help information."""
        runner = CliRunner()

        result = runner.invoke(list_tools, ["--help"])
        assert result.exit_code == 0
        assert "List all available MCP tools" in result.output
