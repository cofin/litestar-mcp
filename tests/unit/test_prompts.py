"""Tests for MCP Prompts support (prompts/list and prompts/get)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from litestar import Litestar, Response, get
from litestar.status_codes import HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_prompt
from litestar_mcp.registry import (
    PromptRegistration,
    Registry,
    _normalize_prompt_result,
    _parse_docstring_args,
)
from litestar_mcp.utils import get_mcp_metadata

# ---------------------------------------------------------------------------
# Helpers — mirrors _ensure_session / _rpc pattern from test_plugin.py
# ---------------------------------------------------------------------------


def _ensure_session(client: TestClient[Any]) -> str:
    key = "_mcp_session::/mcp"
    sid = getattr(client, key, None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    setattr(client, key, sid)
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    msg_id: int = 1,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def _make_app_with_prompts(*prompt_fns: Callable[..., Any]) -> Litestar:
    """Create a Litestar app with MCP prompts registered."""
    plugin = LitestarMCP(config=MCPConfig(), prompts=list(prompt_fns))
    return Litestar(route_handlers=[], plugins=[plugin])


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

    def test_handler_with_no_params_returns_empty_arguments(self) -> None:
        @get("/")
        def handler() -> str:
            return ""

        reg = PromptRegistration(name="x", handler=handler)
        assert reg.get_arguments() == []

    def test_handler_arguments_introspected_from_signature_model(self) -> None:
        """Handler-based prompts derive arguments from the handler's
        ``signature_model`` so ``prompts/list`` advertises the real shape
        instead of an empty list.
        """

        @get("/x", mcp_prompt="x_prompt")
        def x_handler(code: str, style: str = "brief") -> str:
            """Review handler.

            Args:
                code: The source code.
                style: Output style.
            """
            return ""

        plugin = LitestarMCP(config=MCPConfig())
        Litestar(route_handlers=[x_handler], plugins=[plugin])
        args = plugin.discovered_prompts["x_prompt"].get_arguments()
        names_required = [(a["name"], a.get("required")) for a in args]
        assert ("code", True) in names_required
        assert ("style", False) in names_required
        descriptions = {a["name"]: a.get("description") for a in args}
        assert descriptions["code"] == "The source code."
        assert descriptions["style"] == "Output style."

    def test_get_arguments_skips_var_positional_and_var_keyword(self) -> None:
        def fn(a: str, *args: str, **kwargs: str) -> str:
            return ""

        reg = PromptRegistration(name="test", fn=fn)
        args = reg.get_arguments()
        names = [arg["name"] for arg in args]
        assert "a" in names
        assert "args" not in names
        assert "kwargs" not in names

    def test_handler_request_param_filtered_from_arguments(self) -> None:
        """Litestar's magic-injected ``request`` must not leak into prompts/list."""
        from litestar import Request

        @get("/with-request", mcp_prompt="needs_req")
        async def needs_req(text: str, request: Request) -> str:  # noqa: ARG001
            return text

        plugin = LitestarMCP(config=MCPConfig())
        Litestar(route_handlers=[needs_req], plugins=[plugin])
        args = plugin.discovered_prompts["needs_req"].get_arguments()
        names = [a["name"] for a in args]
        assert "text" in names
        assert "request" not in names, "magic-injected param leaked into MCP arg list"

    def test_handler_headers_param_filtered_from_arguments(self) -> None:
        """``headers`` is a framework-injected name and must be filtered."""

        @get("/with-headers", mcp_prompt="needs_hdr")
        async def needs_hdr(text: str, headers: dict[str, str]) -> str:  # noqa: ARG001
            return text

        plugin = LitestarMCP(config=MCPConfig())
        Litestar(route_handlers=[needs_hdr], plugins=[plugin])
        names = [a["name"] for a in plugin.discovered_prompts["needs_hdr"].get_arguments()]
        assert "headers" not in names

    def test_handler_di_dependency_filtered_from_arguments(self) -> None:
        """Provide() dependencies must be excluded from prompts/list."""
        from litestar.di import Provide

        async def supply_secret() -> str:
            return "shh"

        @get("/with-di", mcp_prompt="needs_di", dependencies={"secret": Provide(supply_secret)})
        async def needs_di(text: str, secret: str) -> str:  # noqa: ARG001
            return text

        plugin = LitestarMCP(config=MCPConfig())
        Litestar(route_handlers=[needs_di], plugins=[plugin])
        names = [a["name"] for a in plugin.discovered_prompts["needs_di"].get_arguments()]
        assert "text" in names
        assert "secret" not in names, "DI dependency leaked into advertised arguments"

    def test_introspect_handler_resolve_dependencies_failure_is_silent(self) -> None:
        """resolve_dependencies() raising AttributeError/TypeError must not
        propagate — empty di_params is the safe fallback."""
        from types import SimpleNamespace

        from litestar_mcp.registry import _introspect_handler_arguments

        # Stub handler whose resolve_dependencies blows up. We still want
        # introspection to succeed against a None signature_model.
        stub = SimpleNamespace(
            signature_model=None,
            resolve_dependencies=lambda: (_ for _ in ()).throw(AttributeError("nope")),
        )
        assert _introspect_handler_arguments(stub) == []  # type: ignore[arg-type]


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

    def test_blank_line_between_params(self) -> None:
        doc = """Fn.

        Args:
            x: First param.

            y: Second param.
        """
        result = _parse_docstring_args(doc)
        assert result == {"x": "First param.", "y": "Second param."}

    def test_stops_at_notes_section(self) -> None:
        doc = """Fn.

        Args:
            x: A param.

        Notes:
            Some notes.
        """
        result = _parse_docstring_args(doc)
        assert result == {"x": "A param."}

    def test_continuation_line_with_colon_not_treated_as_param(self) -> None:
        """I1 regression: a continuation line containing ``:`` (URL,
        ``Default: foo``, ``e.g.: bar``) must extend the current
        parameter's description, not introduce a phantom parameter."""
        doc = """Fn.

        Args:
            code: The code to review.
                Default: see config above.
                See also: https://example.com/docs
            style: Output style.
        """
        result = _parse_docstring_args(doc)
        assert result == {
            "code": (
                "The code to review. Default: see config above. "
                "See also: https://example.com/docs"
            ),
            "style": "Output style.",
        }

    def test_empty_args_section(self) -> None:
        """Args: header followed immediately by another section returns {}."""
        doc = """Fn.

        Args:

        Returns:
            Nothing.
        """
        assert _parse_docstring_args(doc) == {}

    def test_args_section_terminated_by_unindented_text(self) -> None:
        """Unindented non-empty line ends the Args section."""
        doc = "Fn.\n\n        Args:\n            x: First.\nAfter args block.\n"
        assert _parse_docstring_args(doc) == {"x": "First."}

    def test_line_without_colon_appended_to_previous_param(self) -> None:
        """Indented line w/o colon = continuation, not a new param."""
        doc = """Fn.

        Args:
            x: First param.
                additional notes without colon
        """
        assert _parse_docstring_args(doc) == {"x": "First param. additional notes without colon"}


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
        await anext(stream)  # Prime event

        await registry.notify_prompts_list_changed()

        msg = await anext(stream)
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

    def test_list_with_malformed_dict_coerced(self) -> None:
        result = _normalize_prompt_result([{"text": "no role or content"}])
        assert result == [{"role": "user", "content": {"type": "text", "text": "{'text': 'no role or content'}"}}]

    def test_other_types_stringified(self) -> None:
        result = _normalize_prompt_result(42)
        assert result == [{"role": "user", "content": {"type": "text", "text": "42"}}]

    def test_image_content_block_passes_through(self) -> None:
        """Per MCP spec a message w/ image content survives normalization."""
        msg = {
            "role": "user",
            "content": {"type": "image", "data": "Zg==", "mimeType": "image/png"},
        }
        assert _normalize_prompt_result(msg) == [msg]

    def test_audio_content_block_passes_through(self) -> None:
        msg = {
            "role": "user",
            "content": {"type": "audio", "data": "Zg==", "mimeType": "audio/wav"},
        }
        assert _normalize_prompt_result(msg) == [msg]

    def test_resource_link_content_block_passes_through(self) -> None:
        msg = {
            "role": "user",
            "content": {"type": "resource_link", "uri": "file://x", "name": "x"},
        }
        assert _normalize_prompt_result(msg) == [msg]

    def test_resource_content_block_passes_through(self) -> None:
        msg = {
            "role": "user",
            "content": {"type": "resource", "resource": {"uri": "file://x", "text": "y"}},
        }
        assert _normalize_prompt_result(msg) == [msg]

    def test_unwrapped_image_block_wrapped_in_user_envelope(self) -> None:
        """Bare content block without role wrapped in user-role envelope."""
        block = {"type": "image", "data": "Zg==", "mimeType": "image/png"}
        result = _normalize_prompt_result(block)
        assert result == [{"role": "user", "content": block}]

    def test_image_missing_required_key_falls_back_to_string(self) -> None:
        """Image block missing 'data' is malformed → stringified fallback."""
        block = {"type": "image", "mimeType": "image/png"}
        result = _normalize_prompt_result(block)
        assert result[0]["content"]["type"] == "text"
        assert "image" in result[0]["content"]["text"]

    def test_content_list_inside_message_preserved(self) -> None:
        """A list of content blocks under message.content is not flattened."""
        blocks = [
            {"type": "text", "text": "hi"},
            {"type": "image", "data": "Zg==", "mimeType": "image/png"},
        ]
        msg = {"role": "user", "content": blocks}
        assert _normalize_prompt_result(msg) == [msg]


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


class TestPromptsListRPC:
    def test_empty_prompts_list(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            assert data["result"]["prompts"] == []

    def test_lists_registered_prompts(self) -> None:
        @mcp_prompt(name="summarize", title="Summarize", description="Summarize text")
        def summarize(text: str) -> str:
            return f"Summary of: {text}"

        app = _make_app_with_prompts(summarize)
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            prompts = data["result"]["prompts"]
            assert len(prompts) == 1
            assert prompts[0]["name"] == "summarize"
            assert prompts[0]["title"] == "Summarize"
            assert prompts[0]["description"] == "Summarize text"
            assert prompts[0]["arguments"] == [{"name": "text", "required": True}]

    def test_lists_prompt_with_icons_in_meta(self) -> None:
        icons = [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]}]

        @mcp_prompt(name="fancy", description="Fancy prompt", icons=icons)
        def fancy() -> str:
            return ""

        app = _make_app_with_prompts(fancy)
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            prompt = data["result"]["prompts"][0]
            assert prompt["icons"] == icons
            assert "_meta" not in prompt, "icons belong at top level per MCP Icons mixin, not in _meta"

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
            data = _rpc(client, "prompts/list")
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
            data = _rpc(client, "prompts/list")
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
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "greet", "arguments": {"name": "World"}},
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
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "async_greet", "arguments": {"name": "Bob"}},
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
            data = _rpc(client, "prompts/get", params={"name": "multi_msg"})
            messages = data["result"]["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "user"
            assert messages[1]["role"] == "assistant"

    def test_get_nonexistent_prompt_error(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "nonexistent"})
            assert "error" in data
            assert data["error"]["code"] == -32602  # INVALID_PARAMS

    def test_get_prompt_missing_name_error(self) -> None:
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={})
            assert "error" in data
            assert "Missing required param" in data["error"]["message"]

    def test_get_prompt_arguments_must_be_object(self) -> None:
        @mcp_prompt(name="typed")
        def typed(name: str) -> str:
            return name

        app = _make_app_with_prompts(typed)
        with TestClient(app) as client:
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "typed", "arguments": []},
            )
            assert "error" in data
            assert data["error"]["code"] == -32602

    def test_get_prompt_rejects_non_string_argument_values(self) -> None:
        @mcp_prompt(name="typed")
        def typed(name: str) -> str:
            return name

        app = _make_app_with_prompts(typed)
        with TestClient(app) as client:
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "typed", "arguments": {"name": 42}},
            )
            assert "error" in data
            assert data["error"]["code"] == -32602
            assert "must be a string" in data["error"]["message"]

    def test_get_prompt_invalid_arguments_error(self) -> None:
        @mcp_prompt(name="strict")
        def strict(required_arg: str) -> str:
            return required_arg

        app = _make_app_with_prompts(strict)
        with TestClient(app) as client:
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "strict", "arguments": {"wrong_arg": "val"}},
            )
            assert "error" in data


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------


class TestPromptsCapability:
    def test_initialize_advertises_prompts_when_registered(self) -> None:
        @mcp_prompt(name="any_prompt")
        def any_prompt() -> str:
            return ""

        app = _make_app_with_prompts(any_prompt)
        with TestClient(app) as client:
            data = _rpc(client, "initialize")
            capabilities = data["result"]["capabilities"]
            assert "prompts" in capabilities
            assert capabilities["prompts"]["listChanged"] is True

    def test_initialize_omits_prompts_capability_when_none_registered(self) -> None:
        """Per MCP spec, only advertise capabilities the server provides."""
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            data = _rpc(client, "initialize")
            assert "prompts" not in data["result"]["capabilities"]


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
        Litestar(route_handlers=[review_handler], plugins=[plugin])
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
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "handler_greet"},
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
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "raw_prompt"},
            )
            result = data["result"]
            assert result["description"] == "Raw prompt"
            assert result["messages"] == [
                {"role": "user", "content": {"type": "text", "text": "Normalized"}}
            ]

    def test_handler_prompt_missing_required_arg_returns_invalid_params(self) -> None:
        """MCP spec: missing a required prompt argument MUST surface as INVALID_PARAMS."""

        @get("/needs-code", mcp_prompt="needs_code", mcp_prompt_description="Needs code")
        async def needs_code(code: str) -> dict:
            return {
                "messages": [{"role": "user", "content": {"type": "text", "text": code}}]
            }

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[needs_code], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "needs_code", "arguments": {}})
            assert "error" in data
            assert data["error"]["code"] == -32602
            assert "code" in data["error"]["message"]

    def test_handler_prompt_unknown_arg_returns_invalid_params(self) -> None:
        """Unknown prompt argument names MUST surface as INVALID_PARAMS."""

        @get("/typed-handler", mcp_prompt="typed_handler", mcp_prompt_description="Typed handler")
        async def typed_handler(name: str) -> dict:
            return {
                "messages": [{"role": "user", "content": {"type": "text", "text": name}}]
            }

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[typed_handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(
                client,
                "prompts/get",
                params={"name": "typed_handler", "arguments": {"name": "x", "wrong": "y"}},
            )
            assert "error" in data
            assert data["error"]["code"] == -32602
            assert "wrong" in data["error"]["message"]


# ---------------------------------------------------------------------------
# Spec coverage: _meta echo on prompts/get
# ---------------------------------------------------------------------------


class TestPromptsGetMetaEcho:
    def test_handler_prompt_meta_passthrough(self) -> None:
        """Handler-set _meta on the prompt result is preserved on the wire.

        MCP spec allows servers to attach ``_meta`` to ``GetPromptResult``;
        when a prompt handler explicitly returns it, the route layer must
        not strip it.
        """

        @get("/meta-handler", mcp_prompt="meta_prompt", mcp_prompt_description="Meta prompt")
        async def meta_handler() -> dict:
            return {
                "messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}],
                "_meta": {"trace_id": "abc-123"},
            }

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[meta_handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "meta_prompt"})
            result = data["result"]
            assert result["_meta"] == {"trace_id": "abc-123"}


# ---------------------------------------------------------------------------
# Opt-key parity: title / arguments / icons
# ---------------------------------------------------------------------------


class TestHandlerPromptOptKeys:
    def test_opt_keys_carry_title_arguments_icons(self) -> None:
        explicit_args = [{"name": "topic", "description": "Topic to summarise", "required": True}]
        icons = [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml"}]

        @get(
            "/summarise",
            mcp_prompt="opt_summarise",
            mcp_prompt_title="Summarise",
            mcp_prompt_description="Summarise a topic",
            mcp_prompt_arguments=explicit_args,
            mcp_prompt_icons=icons,
        )
        async def summarise(topic: str) -> dict:
            return {"messages": [{"role": "user", "content": {"type": "text", "text": topic}}]}

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[summarise], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            entry = next(p for p in data["result"]["prompts"] if p["name"] == "opt_summarise")
            assert entry["title"] == "Summarise"
            assert entry["arguments"] == explicit_args
            assert entry["icons"] == icons


# ---------------------------------------------------------------------------
# Spec coverage: PromptArgument.title pass-through (BaseMetadata mixin)
# ---------------------------------------------------------------------------


class TestPromptArgumentTitle:
    def test_explicit_argument_title_passes_through(self) -> None:
        """Per MCP spec, ``PromptArgument`` carries an optional ``title`` from
        the ``BaseMetadata`` mixin. It cannot be derived from a function
        signature; explicit ``arguments=[...]`` carries it through verbatim.
        """
        explicit = [
            {"name": "code", "title": "Source Code", "description": "The code", "required": True},
        ]

        @mcp_prompt(name="titled", arguments=explicit)
        def titled(code: str) -> str:
            return code

        app = _make_app_with_prompts(titled)
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            entry = next(p for p in data["result"]["prompts"] if p["name"] == "titled")
            assert entry["arguments"] == explicit
            assert entry["arguments"][0]["title"] == "Source Code"


# ---------------------------------------------------------------------------
# Error-mapping coverage: handler 4xx vs 5xx, fn exception INTERNAL_ERROR
# ---------------------------------------------------------------------------


class TestPromptErrorMapping:
    def test_standalone_fn_exception_maps_to_internal_error(self) -> None:
        """Plain exceptions inside a standalone prompt fn surface as -32603."""

        @mcp_prompt(name="boom")
        def boom() -> str:
            msg = "kaboom"
            raise RuntimeError(msg)

        app = _make_app_with_prompts(boom)
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "boom"})
            assert "error" in data
            assert data["error"]["code"] == -32603  # INTERNAL_ERROR
            # Per JSON-RPC 2.0 §5.1, structured exception context lives in
            # the ``data`` member; the ``message`` is a stable label.
            assert data["error"]["message"] == "Prompt execution failed"
            assert "kaboom" in data["error"]["data"]["detail"]
            assert data["error"]["data"]["error"] == "RuntimeError"

    def test_handler_4xx_maps_to_invalid_params(self) -> None:
        """Handler returning a 4xx Response surfaces as -32602 (INVALID_PARAMS).

        ``MCPToolErrorResult.is_client_error`` keys off the captured HTTP
        status; the route layer maps that to JSON-RPC ``INVALID_PARAMS``.
        """

        @get("/bad-handler", mcp_prompt="bad_4xx", mcp_prompt_description="Bad handler")
        async def bad_handler() -> Response[dict[str, str]]:
            return Response(content={"error": "bad input"}, status_code=HTTP_400_BAD_REQUEST)

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[bad_handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "bad_4xx"})
            assert "error" in data
            assert data["error"]["code"] == -32602  # INVALID_PARAMS

    def test_handler_5xx_maps_to_internal_error(self) -> None:
        """Handler returning a 5xx Response surfaces as -32603 (INTERNAL_ERROR)."""

        @get("/boom-handler", mcp_prompt="bad_5xx", mcp_prompt_description="Boom handler")
        async def boom_handler() -> Response[dict[str, str]]:
            return Response(content={"error": "boom"}, status_code=HTTP_500_INTERNAL_SERVER_ERROR)

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[boom_handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "bad_5xx"})
            assert "error" in data
            assert data["error"]["code"] == -32603


# ---------------------------------------------------------------------------
# Filter coverage: prompts respect include/exclude_operations + tags
# ---------------------------------------------------------------------------


class TestPromptFiltering:
    def test_handler_prompt_excluded_by_operations_hidden_from_list(self) -> None:
        @get("/secret", mcp_prompt="secret_prompt", mcp_prompt_description="Secret")
        async def secret() -> dict:
            return {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]}

        plugin = LitestarMCP(config=MCPConfig(exclude_operations=["secret_prompt"]))
        app = Litestar(route_handlers=[secret], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            names = [p["name"] for p in data["result"]["prompts"]]
            assert "secret_prompt" not in names

    def test_handler_prompt_excluded_by_operations_returns_invalid_params(self) -> None:
        @get("/secret-get", mcp_prompt="secret_get", mcp_prompt_description="Secret")
        async def secret_get() -> dict:
            return {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]}

        plugin = LitestarMCP(config=MCPConfig(exclude_operations=["secret_get"]))
        app = Litestar(route_handlers=[secret_get], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": "secret_get"})
            assert "error" in data
            assert data["error"]["code"] == -32602
            assert "not found" in data["error"]["message"].lower()

    def test_handler_prompt_excluded_by_tags(self) -> None:
        @get("/tagged", mcp_prompt="tagged_prompt", mcp_prompt_description="Tagged", tags=["internal"])
        async def tagged() -> dict:
            return {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]}

        plugin = LitestarMCP(config=MCPConfig(exclude_tags=["internal"]))
        app = Litestar(route_handlers=[tagged], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            names = [p["name"] for p in data["result"]["prompts"]]
            assert "tagged_prompt" not in names

    def test_standalone_prompt_excluded_by_operations(self) -> None:
        """fn-based prompts are subject to name-based filters too."""

        @mcp_prompt(name="hidden_fn")
        def hidden_fn() -> str:
            return ""

        plugin = LitestarMCP(
            config=MCPConfig(exclude_operations=["hidden_fn"]),
            prompts=[hidden_fn],
        )
        app = Litestar(route_handlers=[], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/list")
            assert data["result"]["prompts"] == []
            data = _rpc(client, "prompts/get", params={"name": "hidden_fn"})
            assert "error" in data
            assert data["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# Manifest (.well-known/mcp-server.json) capability + filter parity with
# the JSON-RPC initialize/prompts/list responses.
# ---------------------------------------------------------------------------


class TestPromptsManifest:
    def test_manifest_omits_prompts_capability_when_none_registered(self) -> None:
        """Per MCP spec, advertise prompts capability only when prompts exist."""
        app = _make_app_with_prompts()
        with TestClient(app) as client:
            payload = client.get("/.well-known/mcp-server.json").json()
            assert "prompts" not in payload["capabilities"]
            assert payload["prompts"] == []

    def test_manifest_advertises_prompts_capability_when_registered(self) -> None:
        @mcp_prompt(name="hello")
        def hello() -> str:
            return ""

        app = _make_app_with_prompts(hello)
        with TestClient(app) as client:
            payload = client.get("/.well-known/mcp-server.json").json()
            assert payload["capabilities"]["prompts"] == {"listChanged": True}
            assert any(p["name"] == "hello" for p in payload["prompts"])

    def test_manifest_filters_excluded_prompts(self) -> None:
        """Manifest must apply should_include_prompt — must not leak hidden prompts."""

        @mcp_prompt(name="hidden")
        def hidden() -> str:
            return ""

        plugin = LitestarMCP(
            config=MCPConfig(exclude_operations=["hidden"]),
            prompts=[hidden],
        )
        app = Litestar(route_handlers=[], plugins=[plugin])
        with TestClient(app) as client:
            payload = client.get("/.well-known/mcp-server.json").json()
            assert payload["prompts"] == []
            assert "prompts" not in payload["capabilities"]


# ---------------------------------------------------------------------------
# C1 regression: ASGI handler exits without http.response.start
# ---------------------------------------------------------------------------


class TestCaptureAsgiResponseStatusZero:
    """Cover the path where _capture_asgi_response observes no response start.

    Without the C1 fix the function returned (None, 0), which slipped past
    the >= 400 error gate in execute_handler, leaving callers to surface the
    literal string "None" as a successful prompt body.
    """

    @pytest.mark.asyncio
    async def test_asgi_app_without_response_start_classified_as_500(self) -> None:
        from types import SimpleNamespace

        from litestar_mcp.executor import _NON_JSON_STATUS, _capture_asgi_response

        async def silent_asgi_app(scope: Any, receive: Any, send: Any) -> None:
            return  # never calls send → no http.response.start

        request_stub = SimpleNamespace(scope={"type": "http"}, receive=lambda: None)

        content, status = await _capture_asgi_response(silent_asgi_app, request_stub)  # type: ignore[arg-type]
        assert status == _NON_JSON_STATUS
        assert isinstance(content, dict)
        assert "error" in content
        assert "without sending" in content["error"].lower()


# ---------------------------------------------------------------------------
# I4 regression: 4xx → JSON-RPC code mapping is narrow (400/422 only)
# ---------------------------------------------------------------------------


class TestPromptHandlerErrorCodeMapping:
    """4xx classes other than 400/422 must NOT be reported as INVALID_PARAMS.

    JSON-RPC INVALID_PARAMS (-32602) is reserved for invalid method
    parameters. 401/403/404/409 are not parameter-validation failures and
    should fall through to INTERNAL_ERROR until the dedicated error
    taxonomy lands (issue #48).
    """

    @pytest.mark.parametrize("status_code", [401, 403, 404, 409])
    def test_non_validation_4xx_maps_to_internal_error(self, status_code: int) -> None:
        @get(
            f"/handler-{status_code}",
            mcp_prompt=f"prompt_{status_code}",
            mcp_prompt_description="Handler that returns a non-validation 4xx",
        )
        async def handler() -> Response[dict]:
            return Response({"error": "no"}, status_code=status_code)

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": f"prompt_{status_code}"})
            assert "error" in data
            assert data["error"]["code"] == -32603, (
                f"{status_code} must map to INTERNAL_ERROR, not INVALID_PARAMS"
            )

    @pytest.mark.parametrize("status_code", [400, 422])
    def test_validation_4xx_maps_to_invalid_params(self, status_code: int) -> None:
        @get(
            f"/validation-{status_code}",
            mcp_prompt=f"prompt_v{status_code}",
            mcp_prompt_description="Handler that returns a validation 4xx",
        )
        async def handler() -> Response[dict]:
            return Response({"error": "bad input"}, status_code=status_code)

        plugin = LitestarMCP(config=MCPConfig())
        app = Litestar(route_handlers=[handler], plugins=[plugin])
        with TestClient(app) as client:
            data = _rpc(client, "prompts/get", params={"name": f"prompt_v{status_code}"})
            assert "error" in data
            assert data["error"]["code"] == -32602, (
                f"{status_code} must map to INVALID_PARAMS (validation error)"
            )
