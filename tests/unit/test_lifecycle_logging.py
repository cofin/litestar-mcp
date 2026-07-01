import logging

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.registry import Registry

pytestmark = pytest.mark.unit


def _warning_records(caplog: "pytest.LogCaptureFixture", *logger_names: str) -> "list[logging.LogRecord]":
    return [
        record for record in caplog.records if record.levelno >= logging.WARNING and record.name in set(logger_names)
    ]


def test_plugin_startup_and_shutdown_lifecycle_logs_are_not_warnings(
    caplog: "pytest.LogCaptureFixture",
) -> "None":
    @get("/ping", mcp_tool="ping", sync_to_thread=False)
    def ping() -> "dict[str, bool]":
        return {"ok": True}

    app = Litestar(route_handlers=[ping], plugins=[LitestarMCP()], logging_config=None)
    caplog.set_level(logging.WARNING, logger="litestar_mcp.plugin")

    with TestClient(app=app):
        pass

    assert _warning_records(caplog, "litestar_mcp.plugin") == []


def test_registry_change_callback_invalidation_logs_are_not_warnings(
    caplog: "pytest.LogCaptureFixture",
) -> "None":
    @get("/ping", mcp_tool="ping", sync_to_thread=False)
    def ping() -> "dict[str, bool]":
        return {"ok": True}

    plugin = LitestarMCP()
    app = Litestar(route_handlers=[ping], plugins=[plugin], logging_config=None)
    caplog.set_level(logging.WARNING, logger="litestar_mcp.plugin")
    caplog.set_level(logging.WARNING, logger="litestar_mcp.registry")

    @get("/dynamic", mcp_tool="dynamic", sync_to_thread=False)
    def dynamic() -> "dict[str, bool]":
        return {"ok": True}

    with TestClient(app=app):
        app.state.mcp_router = object()
        caplog.clear()
        plugin.registry.register_tool("dynamic", dynamic)

        assert not hasattr(app.state, "mcp_router")

    assert _warning_records(caplog, "litestar_mcp.plugin", "litestar_mcp.registry") == []


def test_registry_overwrite_still_warns(caplog: "pytest.LogCaptureFixture") -> "None":
    @get("/ping", mcp_tool="ping", sync_to_thread=False)
    def ping() -> "dict[str, bool]":
        return {"ok": True}

    registry = Registry()
    caplog.set_level(logging.WARNING, logger="litestar_mcp.registry")

    registry.register_tool("ping", ping)
    registry.register_tool("ping", ping)

    messages = [record.getMessage() for record in _warning_records(caplog, "litestar_mcp.registry")]
    assert any("Overwriting existing tool registration: ping" in message for message in messages)
