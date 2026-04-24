"""Tests for MCP Prompts support (prompts/list and prompts/get)."""

import json

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_prompt
from litestar_mcp.registry import PromptRegistration, Registry, _parse_docstring_args
from litestar_mcp.routes import _normalize_prompt_result
from litestar_mcp.utils import get_mcp_metadata


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


class TestMcpPromptDecorator:
    def test_stores_metadata(self) -> None:
        @mcp_prompt(name="greet", description="Greet a user")
        def greet(name: str) -> str:
            return f"Hello {name}"

        metadata = get_mcp_metadata(greet)
        assert metadata is not None
        assert metadata["type"] == "prompt"
        assert metadata["name"] == "greet"
        assert metadata["description"] == "Greet a user"

    def test_stores_title(self) -> None:
        @mcp_prompt(name="t", title="My Title")
        def fn() -> str:
            return ""

        metadata = get_mcp_metadata(fn)
        assert metadata is not None
        assert metadata["title"] == "My Title"

    def test_stores_explicit_arguments(self) -> None:
        args = [{"name": "code", "description": "The code", "required": True}]

        @mcp_prompt(name="review", arguments=args)
        def review(code: str) -> str:
            return code

        metadata = get_mcp_metadata(review)
        assert metadata is not None
        assert metadata["arguments"] == args

    def test_stores_icons(self) -> None:
        icons = [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml"}]

        @mcp_prompt(name="with_icons", icons=icons)
        def fn() -> str:
            return ""

        metadata = get_mcp_metadata(fn)
        assert metadata is not None
        assert metadata["icons"] == icons

    def test_optional_fields_omitted_when_none(self) -> None:
        @mcp_prompt(name="bare")
        def bare() -> str:
            return ""

        metadata = get_mcp_metadata(bare)
        assert metadata is not None
        assert "title" not in metadata
        assert "description" not in metadata
        assert "arguments" not in metadata
        assert "icons" not in metadata


# ---------------------------------------------------------------------------
# PromptRegistration tests
# ---------------------------------------------------------------------------


class TestPromptRegistration:
    def test_get_arguments_from_signature(self) -> None:
        def fn(code: str, style: str = "brief") -> str:
            return ""

        reg = PromptRegistration(name="test", fn=fn)
        args = reg.get_arguments()
        assert len(args) == 2
        assert args[0] == {"name": "code", "required": True}
        assert args[1] == {"name": "style", "required": False}

    def test_get_arguments_with_docstring_descriptions(self) -> None:
        def fn(code: str, style: str = "brief") -> str:
            """Review code.

            Args:
                code: The source code to review.
                style: Output style (brief or detailed).
            """
            return ""

        reg = PromptRegistration(name="test", fn=fn)
        args = reg.get_arguments()
        assert args[0]["description"] == "The source code to review."
        assert args[1]["description"] == "Output style (brief or detailed)."

    def test_get_arguments_explicit_overrides(self) -> None:
        explicit = [{"name": "x", "description": "The X", "required": True}]
        reg = PromptRegistration(name="test", fn=lambda x: x, arguments=explicit)
        assert reg.get_arguments() == explicit

    def test_get_arguments_explicit_empty(self) -> None:
        reg = PromptRegistration(name="test", fn=lambda: "", arguments=[])
        assert reg.get_arguments() == []

    def test_icons_stored(self) -> None:
        icons = [{"src": "https://example.com/icon.png", "mimeType": "image/png"}]
        reg = PromptRegistration(name="test", fn=lambda: "", icons=icons)
        assert reg.icons == icons

    def test_post_init_raises_when_both_fn_and_handler(self) -> None:
        with pytest.raises(ValueError, match="cannot have both"):
            PromptRegistration(name="x", fn=lambda: "", handler=lambda: "")  # type: ignore[arg-type]

    def test_post_init_raises_when_neither_fn_nor_handler(self) -> None:
        with pytest.raises(ValueError, match="must have either"):
            PromptRegistration(name="x")

    def test_handler_only_returns_empty_arguments(self) -> None:
        @get("/")
        def handler() -> str:
            return ""

        reg = PromptRegistration(name="x", handler=handler)
        assert reg.get_arguments() == []

    def test_get_arguments_skips_var_positional_and_var_keyword(self) -> None:
        def fn(a: str, *args: str, **kwargs: str) -> str:
            return ""

        reg = PromptRegistration(name="test", fn=fn)
        args = reg.get_arguments()
        names = [arg["name"] for arg in args]
        assert "a" in names
        assert "args" not in names
        assert "kwargs" not in names


# ---------------------------------------------------------------------------
# Docstring argument parsing tests
# ---------------------------------------------------------------------------


class TestParseDocstringArgs:
    def test_google_style(self) -> None:
        doc = """Do something.

        Args:
            x: First param.
            y: Second param.
        """
        result = _parse_docstring_args(doc)
        assert result == {"x": "First param.", "y": "Second param."}

    def test_multiline_description(self) -> None:
        doc = """Do something.

        Args:
            code: The code to review. Can be
                multi-line description.
            style: Brief or detailed.
        """
        result = _parse_docstring_args(doc)
        assert result["code"] == "The code to review. Can be multi-line description."
        assert result["style"] == "Brief or detailed."

    def test_with_type_annotations(self) -> None:
        doc = """Prompt.

        Args:
            name (str): User name.
            age (int): User age.
        """
        result = _parse_docstring_args(doc)
        assert result == {"name": "User name.", "age": "User age."}

    def test_empty_docstring(self) -> None:
        assert _parse_docstring_args(None) == {}
        assert _parse_docstring_args("") == {}

    def test_no_args_section(self) -> None:
        assert _parse_docstring_args("Just a description.") == {}

    def test_stops_at_next_section(self) -> None:
        doc = """Prompt.

        Args:
            x: A param.

        Returns:
            Something.
        """
        result = _parse_docstring_args(doc)
        assert result == {"x": "A param."}
        assert "Returns" not in result

    def test_arguments_header(self) -> None:
        doc = """Fn.

        Arguments:
            x: First.
        """
        assert _parse_docstring_args(doc) == {"x": "First."}

    def test_params_header(self) -> None:
        doc = """Fn.

        Params:
            x: First.
        """
        assert _parse_docstring_args(doc) == {"x": "First."}

    def test_parameters_header(self) -> None:
        doc = """Fn.

        Parameters:
            x: First.
        """
        assert _parse_docstring_args(doc) == {"x": "First."}

    def test_stops_at_notes_section(self) -> None:
        doc = """Fn.

        Args:
            x: A param.

        Notes:
            Some notes.
        """
        result = _parse_docstring_args(doc)
        assert result == {"x": "A param."}


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistryPrompts:
    @pytest.fixture
    def registry(self) -> Registry:
        return Registry()

    def test_register_prompt(self, registry: Registry) -> None:
        def my_prompt() -> str:
            return "hello"

        registry.register_prompt("my_prompt", my_prompt, description="A prompt")
        assert "my_prompt" in registry.prompts
        assert registry.prompts["my_prompt"].name == "my_prompt"
        assert registry.prompts["my_prompt"].description == "A prompt"
        assert registry.prompts["my_prompt"].fn is my_prompt

    def test_register_prompt_falls_back_to_docstring(self, registry: Registry) -> None:
        def documented() -> str:
            """Docstring description."""
            return ""

        registry.register_prompt("doc_prompt", documented)
        assert registry.prompts["doc_prompt"].description == "Docstring description."

    def test_register_prompt_handler(self, registry: Registry) -> None:
        @get("/")
        def handler() -> dict:
            return {"messages": []}

        registry.register_prompt_handler("handler_prompt", handler, description="Handler prompt")
        assert "handler_prompt" in registry.prompts
        assert registry.prompts["handler_prompt"].handler is handler

    @pytest.mark.asyncio
    async def test_notify_prompts_list_changed(self, registry: Registry) -> None:
        from litestar_mcp.sse import SSEManager

        sse_manager = SSEManager()
        registry.set_sse_manager(sse_manager)

        stream_id, stream = await sse_manager.open_stream(session_id="session1")
        await stream.__anext__()  # Prime event

        await registry.notify_prompts_list_changed()

        msg = await stream.__anext__()
        data = json.loads(msg.data)
        assert data["method"] == "notifications/prompts/list_changed"
        sse_manager.disconnect(stream_id)


# ---------------------------------------------------------------------------
# Result normalization tests
# ---------------------------------------------------------------------------


class TestNormalizePromptResult:
    def test_string_to_user_message(self) -> None:
        result = _normalize_prompt_result("hello")
        assert result == [{"role": "user", "content": {"type": "text", "text": "hello"}}]

    def test_dict_wraps_in_list(self) -> None:
        msg = {"role": "assistant", "content": {"type": "text", "text": "hi"}}
        assert _normalize_prompt_result(msg) == [msg]

    def test_dict_missing_keys_coerced(self) -> None:
        result = _normalize_prompt_result({"text": "raw"})
        assert result == [{"role": "user", "content": {"type": "text", "text": "{'text': 'raw'}"}}]

    def test_list_passes_through(self) -> None:
        msgs = [
            {"role": "user", "content": {"type": "text", "text": "q"}},
            {"role": "assistant", "content": {"type": "text", "text": "a"}},
        ]
        assert _normalize_prompt_result(msgs) == msgs

    def test_other_types_stringified(self) -> None:
        result = _normalize_prompt_result(42)
        assert result == [{"role": "user", "content": {"type": "text", "text": "42"}}]


# ---------------------------------------------------------------------------
# Plugin registration tests
# ---------------------------------------------------------------------------


class TestPluginPromptRegistration:
    def test_registers_decorated_prompts(self) -> None:
        @mcp_prompt(name="test_prompt", description="Test")
        def my_prompt(x: str) -> str:
            return x

        plugin = LitestarMCP(prompts=[my_prompt])
        assert "test_prompt" in plugin.discovered_prompts

    def test_rejects_undecorated_functions(self) -> None:
        def not_a_prompt() -> str:
            return ""

        with pytest.raises(ValueError, match="not decorated with @mcp_prompt"):
            LitestarMCP(prompts=[not_a_prompt])


# ---------------------------------------------------------------------------
# Integration: prompts/list and prompts/get via JSON-RPC
# ---------------------------------------------------------------------------


def _make_app_with_prompts(*prompt_fns) -> Litestar:
    """Create a Litestar app with MCP prompts registered."""
    plugin = LitestarMCP(config=MCPConfig(), prompts=list(prompt_fns))
    return Litestar(route_handlers=[], plugins=[plugin])


def _jsonrpc(client: TestClient, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
    """Send a JSON-RPC request and return the result."""
    body = {"jsonrpc": "2.0", "method": method, "id": 1, "params": params or {}}
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    resp = client.post("/mcp", json=body, headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _init_session(client: TestClient) -> str:
    """Initialize an MCP session and return the session ID."""
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        headers={"Content-Type": "application/json"},
    )
    session_id = resp.headers.get("Mcp-Session-Id")
    assert session_id is not None
    # Mark initialized
    _jsonrpc(client, "notifications/initialized", session_id=session_id)
    return session_id


class TestPromptsListRPC:
    def test_empty_prompts_list(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/list", session_id=session_id)
            assert data["result"]["prompts"] == []

    def test_lists_registered_prompts(self) -> None:
        @mcp_prompt(name="summarize", title="Summarize", description="Summarize text")
        def summarize(text: str) -> str:
            return f"Summary of: {text}"

        app = _make_app_with_prompts(summarize)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/list", session_id=session_id)
            prompts = data["result"]["prompts"]
            assert len(prompts) == 1
            assert prompts[0]["name"] == "summarize"
            assert prompts[0]["title"] == "Summarize"
            assert prompts[0]["description"] == "Summarize text"
            assert prompts[0]["arguments"] == [{"name": "text", "required": True}]

    def test_lists_prompt_with_icons(self) -> None:
        icons = [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]}]

        @mcp_prompt(name="fancy", description="Fancy prompt", icons=icons)
        def fancy() -> str:
            return ""

        app = _make_app_with_prompts(fancy)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/list", session_id=session_id)
            prompt = data["result"]["prompts"][0]
            assert prompt["icons"] == icons

    def test_lists_prompt_with_docstring_arg_descriptions(self) -> None:
        @mcp_prompt(name="documented")
        def documented(code: str, lang: str = "python") -> str:
            """Review code.

            Args:
                code: The source code to review.
                lang: Programming language.
            """
            return code

        app = _make_app_with_prompts(documented)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/list", session_id=session_id)
            args = data["result"]["prompts"][0]["arguments"]
            assert args[0]["name"] == "code"
            assert args[0]["description"] == "The source code to review."
            assert args[1]["name"] == "lang"
            assert args[1]["description"] == "Programming language."

    def test_lists_multiple_prompts(self) -> None:
        @mcp_prompt(name="prompt_a")
        def a() -> str:
            return ""

        @mcp_prompt(name="prompt_b")
        def b(x: str = "default") -> str:
            return x

        app = _make_app_with_prompts(a, b)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/list", session_id=session_id)
            names = [p["name"] for p in data["result"]["prompts"]]
            assert "prompt_a" in names
            assert "prompt_b" in names


class TestPromptsGetRPC:
    def test_get_sync_prompt(self) -> None:
        @mcp_prompt(name="greet", description="Greet someone")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        app = _make_app_with_prompts(greet)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "greet", "arguments": {"name": "World"}},
                session_id=session_id,
            )
            result = data["result"]
            assert result["description"] == "Greet someone"
            assert len(result["messages"]) == 1
            assert result["messages"][0]["role"] == "user"
            assert result["messages"][0]["content"]["text"] == "Hello, World!"

    def test_get_async_prompt(self) -> None:
        @mcp_prompt(name="async_greet")
        async def async_greet(name: str) -> str:
            return f"Async hello, {name}!"

        app = _make_app_with_prompts(async_greet)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "async_greet", "arguments": {"name": "Bob"}},
                session_id=session_id,
            )
            assert data["result"]["messages"][0]["content"]["text"] == "Async hello, Bob!"

    def test_get_prompt_returns_messages_list(self) -> None:
        @mcp_prompt(name="multi_msg")
        def multi() -> list:
            return [
                {"role": "user", "content": {"type": "text", "text": "Question"}},
                {"role": "assistant", "content": {"type": "text", "text": "Answer"}},
            ]

        app = _make_app_with_prompts(multi)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/get", params={"name": "multi_msg"}, session_id=session_id)
            messages = data["result"]["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "user"
            assert messages[1]["role"] == "assistant"

    def test_get_nonexistent_prompt_error(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client, "prompts/get", params={"name": "nonexistent"}, session_id=session_id
            )
            assert "error" in data
            assert data["error"]["code"] == -32602  # INVALID_PARAMS

    def test_get_prompt_missing_name_error(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(client, "prompts/get", params={}, session_id=session_id)
            assert "error" in data
            assert "Missing required param" in data["error"]["message"]

    def test_get_prompt_arguments_must_be_object(self) -> None:
        @mcp_prompt(name="typed")
        def typed(name: str) -> str:
            return name

        app = _make_app_with_prompts(typed)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "typed", "arguments": []},
                session_id=session_id,
            )
            assert "error" in data
            assert data["error"]["code"] == -32602

    def test_get_prompt_invalid_arguments_error(self) -> None:
        @mcp_prompt(name="strict")
        def strict(required_arg: str) -> str:
            return required_arg

        app = _make_app_with_prompts(strict)
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "strict", "arguments": {"wrong_arg": "val"}},
                session_id=session_id,
            )
            assert "error" in data


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------


class TestPromptsCapability:
    def test_initialize_advertises_prompts(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            data = _jsonrpc(client, "initialize")
            capabilities = data["result"]["capabilities"]
            assert "prompts" in capabilities
            assert capabilities["prompts"]["listChanged"] is True


# ---------------------------------------------------------------------------
# Handler-based prompt discovery via opt key
# ---------------------------------------------------------------------------


class TestHandlerBasedPromptDiscovery:
    def test_opt_key_prompt_discovered(self) -> None:
        @get("/review", mcp_prompt="code_review", mcp_prompt_description="Review code")
        async def review_handler(code: str) -> dict:
            return {
                "messages": [{"role": "user", "content": {"type": "text", "text": f"Review: {code}"}}]
            }

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[review_handler], plugins=[plugin])
        assert "code_review" in plugin.discovered_prompts

    def test_handler_prompt_get_e2e_dict_response(self) -> None:
        """Handler returns {"messages": ...} — passed through directly."""

        @get("/greet-handler", mcp_prompt="handler_greet", mcp_prompt_description="Handler greet")
        async def greet_handler() -> dict:
            return {
                "messages": [{"role": "assistant", "content": {"type": "text", "text": "Handler says hi"}}]
            }

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[greet_handler], plugins=[plugin])
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "handler_greet"},
                session_id=session_id,
            )
            result = data["result"]
            assert result["description"] == "Handler greet"
            assert len(result["messages"]) == 1
            assert result["messages"][0]["role"] == "assistant"
            assert result["messages"][0]["content"]["text"] == "Handler says hi"

    def test_handler_prompt_get_e2e_normalized_response(self) -> None:
        """Handler returns dict without 'messages' key — normalized via _normalize_prompt_result."""

        @get("/raw-handler", mcp_prompt="raw_prompt", mcp_prompt_description="Raw prompt")
        async def raw_handler() -> dict:
            return {"role": "user", "content": {"type": "text", "text": "Normalized"}}

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[raw_handler], plugins=[plugin])
        with TestClient(app) as client:
            session_id = _init_session(client)
            data = _jsonrpc(
                client,
                "prompts/get",
                params={"name": "raw_prompt"},
                session_id=session_id,
            )
            result = data["result"]
            assert result["description"] == "Raw prompt"
            assert result["messages"] == [
                {"role": "user", "content": {"type": "text", "text": "Normalized"}}
            ]
