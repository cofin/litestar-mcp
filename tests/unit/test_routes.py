"""Unit tests for MCP route resource MIME parameter preservation and content decoding."""

from typing import Any

import pytest
from litestar import Litestar, Response, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from tests.unit.conftest import mcp_post

APP_HTML = "<!doctype html><html><body><h1>Explorer</h1></body></html>"


def test_resource_read_preserves_mcp_app_mime_parameter() -> None:
    """resources/list and resources/read preserve text/html;profile=mcp-app (issue #106)."""

    @get(
        "/ui/explorer",
        mcp_resource="explorer_ui",
        mcp_resource_uri="ui://demo/explorer.html",
        mcp_resource_mime_type="text/html;profile=mcp-app",
        sync_to_thread=False,
    )
    def explorer_ui() -> Response[str]:
        return Response(content=APP_HTML, media_type="text/html;profile=mcp-app")

    app = Litestar(route_handlers=[explorer_ui], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        list_resp = mcp_post(client, "resources/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": "ui://demo/explorer.html"}).json()

    resources = list_resp["result"]["resources"]
    explorer_entry = next(item for item in resources if item["uri"] == "ui://demo/explorer.html")
    assert explorer_entry["mimeType"] == "text/html;profile=mcp-app"
    assert read_resp["result"]["contents"] == [
        {
            "uri": "ui://demo/explorer.html",
            "mimeType": "text/html;profile=mcp-app",
            "text": APP_HTML,
        }
    ]


@pytest.mark.parametrize(
    ("uri", "declared_mime", "response_media_type", "payload", "expected_mime", "expected_text"),
    [
        (
            "ui://demo/explorer.html",
            "text/html;profile=mcp-app",
            "text/html;profile=mcp-app",
            APP_HTML,
            "text/html;profile=mcp-app",
            APP_HTML,
        ),
        (
            "ui://demo/fallback.html",
            "text/html;profile=mcp-app",
            "text/html",
            APP_HTML,
            "text/html;profile=mcp-app",
            APP_HTML,
        ),
        (
            "litestar://guide_md",
            "text/markdown; variant=GFM",
            "text/markdown; variant=GFM",
            "# Guide\n\n- item\n",
            "text/markdown; variant=GFM",
            "# Guide\n\n- item\n",
        ),
        (
            "litestar://schema_doc",
            "application/json;profile=mcp-schema",
            "application/json;profile=mcp-schema",
            b'{"type":"object"}',
            "application/json;profile=mcp-schema",
            '{"type":"object"}',
        ),
    ],
    ids=[
        "mcp_app_response_and_declared",
        "mcp_app_declared_with_bare_response_media_type",
        "parameterized_text_markdown",
        "parameterized_application_json",
    ],
)
def test_resources_read_preserves_mime_parameters_and_decodes_utf8_text(
    uri: str,
    declared_mime: str,
    response_media_type: str,
    payload: Any,
    expected_mime: str,
    expected_text: str,
) -> None:
    """Parameterized MIME types preserve non-charset parameters and decode UTF-8 bodies as text."""

    @get(
        "/resource",
        mcp_resource="test_resource",
        mcp_resource_uri=uri,
        mcp_resource_mime_type=declared_mime,
        sync_to_thread=False,
    )
    def resource_handler() -> Response[Any]:
        return Response(content=payload, media_type=response_media_type)

    app = Litestar(route_handlers=[resource_handler], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        read_resp = mcp_post(client, "resources/read", {"uri": uri}).json()

    contents = read_resp["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == uri
    assert contents[0]["mimeType"] == expected_mime
    assert contents[0]["text"] == expected_text
    assert "blob" not in contents[0]


def test_resource_template_read_preserves_declared_mime_parameters_on_bare_response() -> None:
    """URI-template resources also preserve declared MIME parameters when response media_type is bare."""

    @get(
        "/ui/{view:str}",
        mcp_resource="view_ui",
        mcp_resource_template="ui://demo/{view}.html",
        mcp_resource_mime_type="text/html;profile=mcp-app",
        sync_to_thread=False,
    )
    def view_ui(view: str) -> Response[str]:
        return Response(content=f"<main>{view}</main>", media_type="text/html")

    app = Litestar(route_handlers=[view_ui], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        templates_resp = mcp_post(client, "resources/templates/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": "ui://demo/inspector.html"}).json()

    templates = templates_resp["result"]["resourceTemplates"]
    template_entry = next(item for item in templates if item["name"] == "view_ui")
    assert template_entry["mimeType"] == "text/html;profile=mcp-app"
    assert read_resp["result"]["contents"] == [
        {
            "uri": "ui://demo/inspector.html",
            "mimeType": "text/html;profile=mcp-app",
            "text": "<main>inspector</main>",
        }
    ]
