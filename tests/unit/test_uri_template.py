"""RFC 6570 Level 1 URI template helper — match + expand + parse."""

import pytest

from litestar_mcp.utils import expand_template, match_uri, parse_template

pytestmark = pytest.mark.unit


class TestMatch:
    def test_simple_single_var(self) -> None:
        assert match_uri("app://w/{id}", "app://w/42") == {"id": "42"}

    def test_two_vars(self) -> None:
        assert match_uri("app://w/{wid}/f/{fid}", "app://w/1/f/2") == {"wid": "1", "fid": "2"}

    def test_no_match_on_prefix_mismatch(self) -> None:
        assert match_uri("app://w/{id}", "other://w/42") is None

    def test_no_match_on_trailing_slash(self) -> None:
        assert match_uri("app://w/{id}", "app://w/42/") is None

    def test_var_does_not_cross_slash(self) -> None:
        assert match_uri("app://w/{id}", "app://w/1/2") is None

    def test_empty_var_rejected(self) -> None:
        assert match_uri("app://w/{id}", "app://w/") is None

    def test_no_vars_is_exact_match(self) -> None:
        assert match_uri("app://config", "app://config") == {}

    def test_no_vars_no_match(self) -> None:
        assert match_uri("app://config", "app://other") is None

    def test_var_at_end_takes_rest(self) -> None:
        assert match_uri("app://w/{suffix}", "app://w/some-id") == {"suffix": "some-id"}


class TestExpand:
    def test_expand_simple(self) -> None:
        assert expand_template("app://w/{id}", {"id": "42"}) == "app://w/42"

    def test_expand_multi(self) -> None:
        assert expand_template("app://w/{wid}/f/{fid}", {"wid": "a", "fid": "b"}) == "app://w/a/f/b"

    def test_missing_var_raises(self) -> None:
        with pytest.raises(KeyError):
            expand_template("app://w/{id}", {})


class TestParse:
    def test_invalid_var_name_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid|identifier"):
            parse_template("app://w/{0foo}")

    def test_unbalanced_open_brace_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Uu]nbalanced|[Ii]nvalid"):
            parse_template("app://w/{id")

    def test_unbalanced_close_brace_raises(self) -> None:
        with pytest.raises(ValueError, match=r"[Uu]nbalanced|[Ii]nvalid"):
            parse_template("app://w/id}")

    def test_empty_var_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_template("app://w/{}")

    def test_plain_string_parses(self) -> None:
        """A template with no braces parses as a single literal."""
        segments = parse_template("app://config")
        assert len(segments) == 1
