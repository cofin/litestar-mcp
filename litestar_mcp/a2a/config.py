"""Configuration for the optional A2A adapter."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from a2a.server.context import ServerCallContext
    from litestar.connection import Request
    from litestar.types import Guard


@dataclass(frozen=True)
class A2AConfig:
    """Configure the Litestar routes wrapping the official A2A SDK."""

    path: str = "/a2a"
    agent_card_path: str = "/.well-known/agent-card.json"
    guards: "Sequence[Guard]" = ()
    route_opt: dict[str, Any] = field(default_factory=dict)
    context_builder: "Callable[[Request[Any, Any, Any]], ServerCallContext] | None" = None
    enable_v0_3_compat: bool = False
    include_in_schema: bool = False
    agent_card_max_age: int = 300

    def __post_init__(self) -> None:
        for name, value in (("path", self.path), ("agent_card_path", self.agent_card_path)):
            if not value.startswith("/"):
                msg = f"{name} must start with '/'"
                raise ValueError(msg)
        if self.agent_card_max_age < 0:
            msg = f"agent_card_max_age must not be negative, got {self.agent_card_max_age}"
            raise ValueError(msg)
