"""Tests for helper utilities."""

import inspect

import pytest
from dishka.integrations.litestar import FromDishka, inject

from litestar_mcp._cursor import decode_cursor, encode_cursor
from litestar_mcp.utils import (
    expand_template,
    get_handler_function,
    match_uri,
    parse_template,
)
from tests.unit.conftest import create_app_with_handler


class DummyService:
    """Simple marker dependency for Dishka wrapping tests."""


def test_get_handler_function_unwraps_dishka_injected_handlers() -> "None":
    """Dishka-injected handlers should resolve to the original callable."""

    @inject
    async def load_widget(service: "FromDishka[DummyService]") -> "dict[str, bool]":
        return {"ok": service is not None}

    _app, handler = create_app_with_handler(load_widget)

    resolved = get_handler_function(handler)
    params = inspect.signature(resolved).parameters

    assert "service" in params
    assert "request" not in params
    assert resolved is load_widget.__dishka_orig_func__  # type: ignore[attr-defined]


# ==============================================================================
# URI Template Utility Tests (from test_uri_template.py)
# ==============================================================================


class TestURITemplateMatch:
    def test_simple_single_var(self) -> "None":
        assert match_uri("app://w/{id}", "app://w/42") == {"id": "42"}

    def test_two_vars(self) -> "None":
        assert match_uri("app://w/{wid}/f/{fid}", "app://w/1/f/2") == {"wid": "1", "fid": "2"}

    def test_no_match_on_prefix_mismatch(self) -> "None":
        assert match_uri("app://w/{id}", "other://w/42") is None

    def test_no_match_on_trailing_slash(self) -> "None":
        assert match_uri("app://w/{id}", "app://w/42/") is None

    def test_var_does_not_cross_slash(self) -> "None":
        assert match_uri("app://w/{id}", "app://w/1/2") is None

    def test_empty_var_rejected(self) -> "None":
        assert match_uri("app://w/{id}", "app://w/") is None

    def test_no_vars_is_exact_match(self) -> "None":
        assert match_uri("app://config", "app://config") == {}

    def test_no_vars_no_match(self) -> "None":
        assert match_uri("app://config", "app://other") is None

    def test_var_at_end_takes_rest(self) -> "None":
        assert match_uri("app://w/{suffix}", "app://w/some-id") == {"suffix": "some-id"}


class TestURITemplateExpand:
    def test_expand_simple(self) -> "None":
        assert expand_template("app://w/{id}", {"id": "42"}) == "app://w/42"

    def test_expand_multi(self) -> "None":
        assert expand_template("app://w/{wid}/f/{fid}", {"wid": "a", "fid": "b"}) == "app://w/a/f/b"

    def test_missing_var_raises(self) -> "None":
        with pytest.raises(KeyError):
            expand_template("app://w/{id}", {})


class TestURITemplateParse:
    def test_invalid_var_name_raises(self) -> "None":
        with pytest.raises(ValueError, match=r"[Ii]nvalid|identifier"):
            parse_template("app://w/{0foo}")

    def test_unbalanced_open_brace_raises(self) -> "None":
        with pytest.raises(ValueError, match=r"[Uu]nbalanced|[Ii]nvalid"):
            parse_template("app://w/{id")

    def test_unbalanced_close_brace_raises(self) -> "None":
        with pytest.raises(ValueError, match=r"[Uu]nbalanced|[Ii]nvalid"):
            parse_template("app://w/id}")

    def test_empty_var_raises(self) -> "None":
        with pytest.raises(ValueError):
            parse_template("app://w/{}")

    def test_plain_string_parses(self) -> "None":
        """A template with no braces parses as a single literal."""
        segments = parse_template("app://config")
        assert len(segments) == 1


# ==============================================================================
# Cursor Encoding / Decoding Utility Tests
# ==============================================================================


class TestCursorEncoding:
    def test_encode_decode_roundtrip(self) -> "None":
        """Encoding and decoding should recover the original offset."""
        for offset in [0, 42, 1000]:
            cursor = encode_cursor(offset)
            assert isinstance(cursor, str)
            assert decode_cursor(cursor) == offset

    def test_decode_invalid_base64_raises_value_error(self) -> "None":
        """Passing non-base64 characters should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor("!!!not-base64!!!")

    def test_decode_non_integer_raises_value_error(self) -> "None":
        """Decoding base64 payload containing non-integers should raise ValueError."""
        import base64

        bad_cursor = base64.urlsafe_b64encode(b"abc").decode("ascii")
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(bad_cursor)

    def test_decode_negative_offset_raises_value_error(self) -> "None":
        """Decoding negative integers should raise ValueError."""
        import base64

        negative_cursor = base64.urlsafe_b64encode(b"-5").decode("ascii")
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(negative_cursor)
