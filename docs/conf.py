# Configuration file for the Sphinx documentation builder.
from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Any

    from sphinx.application import Sphinx

from litestar_mcp.__metadata__ import __project__, __version__

# -- Environmental Data ------------------------------------------------------

# -- Project information -----------------------------------------------------
current_year = datetime.datetime.now().year
project = __project__
copyright = f"{current_year}, Litestar Organization"
release = os.getenv("_LITESTAR_MCP_DOCS_BUILD_VERSION", __version__.rsplit(".")[0])
suppress_warnings = [
    "autosectionlabel.*",
    "ref.python",  # TODO: remove when https://github.com/sphinx-doc/sphinx/issues/4961 is fixed
]

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.intersphinx",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.githubpages",
    "sphinx.ext.viewcode",
    "tools.sphinx_ext.missing_references",
    "tools.sphinx_ext.changelog",
    "sphinx_autodoc_typehints",
    "myst_parser",
    "auto_pytabs.sphinx_ext",
    "sphinx_copybutton",
    "sphinx.ext.todo",
    "sphinx_click",
    "sphinx_toolbox.collapse",
    "sphinx_design",
    "sphinx_togglebutton",
    "sphinx_paramlinks",
]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "litestar": ("https://docs.litestar.dev/latest/", None),
    "click": ("https://click.palletsprojects.com/en/stable/", None),
    "typing_extensions": ("https://typing-extensions.readthedocs.io/en/stable/", None),
}

PY_CLASS = "py:class"
PY_EXC = "py:exc"
PY_RE = r"py:.*"
PY_METH = "py:meth"
PY_ATTR = "py:attr"
PY_OBJ = "py:obj"
PY_FUNC = "py:func"
nitpicky = True
nitpick_ignore: list[str] = []
nitpick_ignore_regex: list[str] = []

auto_pytabs_min_version = (3, 9)
auto_pytabs_max_version = (3, 13)

napoleon_google_docstring = True
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = False
napoleon_attr_annotations = True

autoclass_content = "class"
autodoc_class_signature = "separated"
autodoc_default_options = {"special-members": "__init__", "show-inheritance": True, "members": True}
autodoc_member_order = "bysource"
autodoc_typehints_format = "short"
autodoc_type_aliases = {
    "RouteHandlerType": "litestar.types.RouteHandlerType",
    "MCPConfig": "litestar_mcp.config.MCPConfig",
    "MCPResource": "litestar_mcp.schema.MCPResource",
    "MCPTool": "litestar_mcp.schema.MCPTool",
    "Union": "typing.Union",
    "Callable": "typing.Callable",
    "Any": "typing.Any",
    "Optional": "typing.Optional",
}

autosectionlabel_prefix_document = True

# Strip the dollar prompt when copying code
# https://sphinx-copybutton.readthedocs.io/en/latest/use.html#strip-and-configure-input-prompts-for-code-cells
copybutton_prompt_text = "$ "

# -- Style configuration -----------------------------------------------------
html_theme = "shibuya"
html_title = "Litestar MCP"
html_short_title = "MCP"
pygments_style = "dracula"
todo_include_todos = True

html_static_path = ["_static"]
html_favicon = "_static/favicon.png"
templates_path = ["_templates"]
html_js_files = ["versioning.js"]
html_css_files = ["custom.css"]
html_show_sourcelink = True
html_copy_source = True

html_context = {
    "source_type": "github",
    "source_user": "litestar-org",
    "source_repo": "litestar-mcp",
    "current_version": "latest",
    "version": release,
}

html_theme_options = {
    "logo_target": "/",
    "accent_color": "amber",
    "github_url": "https://github.com/litestar-org/litestar-mcp",
    "discord_url": "https://discord.gg/dSDXd4mKhp",
    "navigation_with_keys": True,
    "globaltoc_expand_depth": 2,
    "light_logo": "_static/logo-default.png",
    "dark_logo": "_static/logo-default.png",
    "discussion_url": "https://discord.gg/dSDXd4mKhp",
    "nav_links": [
        {"title": "Home", "url": "index"},
        {
            "title": "About",
            "children": [
                {
                    "title": "Changelog",
                    "url": "changelog",
                    "summary": "All changes for Litestar MCP",
                },
                {
                    "title": "Litestar Organization",
                    "summary": "Details about the Litestar organization, the team behind Litestar MCP",
                    "url": "https://litestar.dev/about/organization",
                    "icon": "org",
                },
                {
                    "title": "Releases",
                    "summary": "Explore the release process, versioning, and deprecation policy for Litestar MCP",
                    "url": "releases",
                    "icon": "releases",
                },
            ],
        },
        {
            "title": "Community",
            "children": [
                {
                    "title": "Contributing",
                    "summary": "Learn how to contribute to Litestar MCP",
                    "url": "contribution-guide",
                    "icon": "contributing",
                },
                {
                    "title": "Security",
                    "summary": "Litestar MCP security reporting process",
                    "url": "https://github.com/litestar-org/litestar-mcp/security/policy",
                    "icon": "security",
                },
                {
                    "title": "Code of Conduct",
                    "summary": "Litestar organization Code of Conduct",
                    "url": "https://github.com/litestar-org/.github/blob/main/CODE_OF_CONDUCT.md",
                    "icon": "coc",
                },
            ],
        },
        {
            "title": "Help",
            "children": [
                {
                    "title": "Discord Help Forum",
                    "summary": "Dedicated Discord help forum",
                    "url": "https://discord.gg/dSDXd4mKhp",
                    "icon": "help",
                },
                {
                    "title": "GitHub Discussions",
                    "summary": "GitHub repository discussions",
                    "url": "https://github.com/litestar-org/litestar-mcp/discussions",
                    "icon": "discussions",
                },
                {
                    "title": "Stack Overflow",
                    "summary": "We monitor the 'litestar' tag on Stack Overflow",
                    "url": "https://stackoverflow.com/questions/tagged/litestar",
                    "icon": "stackoverflow",
                },
            ],
        },
    ],
}


def update_html_context(app, pagename, templatename, context, doctree):  # type: ignore[misc]
    context["READTHEDOCS"] = False


def setup(app: Sphinx) -> dict[str, Any]:
    app.connect("html-page-context", update_html_context)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
