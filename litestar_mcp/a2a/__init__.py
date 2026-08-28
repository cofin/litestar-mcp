"""Optional A2A integration backed by the official SDK."""

try:
    import a2a as _a2a
except ImportError as exc:  # pragma: no cover - exercised by clean-wheel smoke tests
    from litestar_mcp.exceptions import MissingDependencyError

    raise MissingDependencyError(package="a2a-sdk", extra="a2a") from exc

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.plugin import LitestarA2A

__all__ = ("A2AConfig", "LitestarA2A")
