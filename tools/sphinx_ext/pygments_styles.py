"""Custom Pygments syntax highlighting styles for litestar-mcp documentation.

Provides light and dark themes tuned to the Shibuya amber accent used by the
litestar-mcp documentation site. Ported from ``sqlspec``'s style module with
the keyword accent palette swapped from purple to amber so code blocks align
with the rest of the theme.
"""

from pygments.style import Style
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Token,
    Whitespace,
)


class LitestarMcpLightStyle(Style):
    """Light syntax highlighting style for litestar-mcp documentation.

    The keyword accent uses the Tailwind ``amber-600`` tone (``#d97706``) so
    keywords and operator words echo the Shibuya ``amber`` theme accent
    declared in ``docs/conf.py``.
    """

    name = "litestar-mcp-light"
    background_color = "#f8f9fa"

    styles = {
        Token: "",
        Whitespace: "",
        Error: "#dc2626",
        Keyword: "bold #d97706",
        Keyword.Constant: "bold #d97706",
        Keyword.Declaration: "bold #d97706",
        Keyword.Namespace: "bold #d97706",
        Keyword.Pseudo: "bold #d97706",
        Keyword.Reserved: "bold #d97706",
        Keyword.Type: "bold #b45309",
        # SQL-specific keyword tokens (kept for SQL code samples).
        Keyword.DML: "bold #d97706",
        Keyword.DDL: "bold #d97706",
        Keyword.DQL: "bold #d97706",
        # Names.
        Name: "#1e293b",
        Name.Attribute: "#6d4c07",
        Name.Builtin: "#0369a1",
        Name.Builtin.Pseudo: "#0369a1",
        Name.Class: "#b45309",
        Name.Constant: "#1e293b",
        Name.Decorator: "italic #b45309",
        Name.Entity: "#1e293b",
        Name.Exception: "#c2410c",
        Name.Function: "#b45309",
        Name.Function.Magic: "#b45309",
        Name.Label: "#1e293b",
        Name.Namespace: "#0e7490",
        Name.Other: "#1e293b",
        Name.Property: "#1e293b",
        Name.Tag: "#1e293b",
        Name.Variable: "#202235",
        Name.Variable.Class: "#202235",
        Name.Variable.Global: "#202235",
        Name.Variable.Instance: "#202235",
        Name.Variable.Magic: "#202235",
        # Strings.
        String: "#107535",
        String.Affix: "#107535",
        String.Backtick: "#107535",
        String.Char: "#107535",
        String.Delimiter: "#107535",
        String.Doc: "italic #4d7c0f",
        String.Double: "#107535",
        String.Escape: "bold #0d6d6e",
        String.Heredoc: "#107535",
        String.Interpol: "#0d6d6e",
        String.Other: "#107535",
        String.Regex: "#107535",
        String.Single: "#107535",
        String.Symbol: "#107535",
        # Numbers.
        Number: "#b45309",
        Number.Bin: "#b45309",
        Number.Float: "#b45309",
        Number.Hex: "#b45309",
        Number.Integer: "#b45309",
        Number.Integer.Long: "#b45309",
        Number.Oct: "#b45309",
        # Comments.
        Comment: "italic #6b7280",
        Comment.Hashbang: "italic #6b7280",
        Comment.Multiline: "italic #6b7280",
        Comment.Preproc: "italic #6b7280",
        Comment.PreprocFile: "italic #6b7280",
        Comment.Single: "italic #6b7280",
        Comment.Special: "italic #6b7280",
        # Operators and punctuation.
        Operator: "#64748b",
        Operator.Word: "bold #d97706",
        Punctuation: "#475569",
        # Generic tokens.
        Generic.Deleted: "#dc2626",
        Generic.Emph: "italic",
        Generic.Error: "#dc2626",
        Generic.Heading: "bold",
        Generic.Inserted: "#107535",
        Generic.Output: "",
        Generic.Prompt: "bold",
        Generic.Strong: "bold",
        Generic.Subheading: "bold",
        Generic.Traceback: "#dc2626",
    }


class LitestarMcpDarkStyle(Style):
    """Dark syntax highlighting style for litestar-mcp documentation.

    Mirrors :class:`LitestarMcpLightStyle` with the keyword accent swapped to
    Tailwind ``amber-400`` (``#fbbf24``) for contrast on the Shibuya dark
    background.
    """

    name = "litestar-mcp-dark"
    background_color = "#1a1b2e"

    styles = {
        Token: "#e6edf3",
        Whitespace: "",
        Error: "#ef9a9a",
        Keyword: "bold #fbbf24",
        Keyword.Constant: "bold #fbbf24",
        Keyword.Declaration: "bold #fbbf24",
        Keyword.Namespace: "bold #fbbf24",
        Keyword.Pseudo: "bold #fbbf24",
        Keyword.Reserved: "bold #fbbf24",
        Keyword.Type: "bold #fcd34d",
        # SQL-specific keyword tokens.
        Keyword.DML: "bold #fbbf24",
        Keyword.DDL: "bold #fbbf24",
        Keyword.DQL: "bold #fbbf24",
        # Names.
        Name: "#cdd6f4",
        Name.Attribute: "#f9e2af",
        Name.Builtin: "#89dceb",
        Name.Builtin.Pseudo: "#89dceb",
        Name.Class: "#fab387",
        Name.Constant: "#cdd6f4",
        Name.Decorator: "italic #fab387",
        Name.Entity: "#cdd6f4",
        Name.Exception: "#ef9a9a",
        Name.Function: "#fab387",
        Name.Function.Magic: "#fab387",
        Name.Label: "#cdd6f4",
        Name.Namespace: "#94e2d5",
        Name.Other: "#cdd6f4",
        Name.Property: "#cdd6f4",
        Name.Tag: "#cdd6f4",
        Name.Variable: "#e6edf3",
        Name.Variable.Class: "#e6edf3",
        Name.Variable.Global: "#e6edf3",
        Name.Variable.Instance: "#e6edf3",
        Name.Variable.Magic: "#e6edf3",
        # Strings.
        String: "#A5D6A7",
        String.Affix: "#A5D6A7",
        String.Backtick: "#A5D6A7",
        String.Char: "#A5D6A7",
        String.Delimiter: "#A5D6A7",
        String.Doc: "italic #a6e3a1",
        String.Double: "#A5D6A7",
        String.Escape: "bold #94e2d5",
        String.Heredoc: "#A5D6A7",
        String.Interpol: "#94e2d5",
        String.Other: "#A5D6A7",
        String.Regex: "#A5D6A7",
        String.Single: "#A5D6A7",
        String.Symbol: "#A5D6A7",
        # Numbers.
        Number: "#fab387",
        Number.Bin: "#fab387",
        Number.Float: "#fab387",
        Number.Hex: "#fab387",
        Number.Integer: "#fab387",
        Number.Integer.Long: "#fab387",
        Number.Oct: "#fab387",
        # Comments.
        Comment: "italic #9CA3AF",
        Comment.Hashbang: "italic #9CA3AF",
        Comment.Multiline: "italic #9CA3AF",
        Comment.Preproc: "italic #9CA3AF",
        Comment.PreprocFile: "italic #9CA3AF",
        Comment.Single: "italic #9CA3AF",
        Comment.Special: "italic #9CA3AF",
        # Operators and punctuation.
        Operator: "#B0BEC5",
        Operator.Word: "bold #fbbf24",
        Punctuation: "#9CA3AF",
        # Generic tokens.
        Generic.Deleted: "#ef9a9a",
        Generic.Emph: "italic",
        Generic.Error: "#ef9a9a",
        Generic.Heading: "bold",
        Generic.Inserted: "#A5D6A7",
        Generic.Output: "",
        Generic.Prompt: "bold",
        Generic.Strong: "bold",
        Generic.Subheading: "bold",
        Generic.Traceback: "#ef9a9a",
    }
