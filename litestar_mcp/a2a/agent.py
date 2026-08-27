"""Standalone Agent runner and skill decorators conforming to A2A protocol."""

import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from litestar import Litestar
from litestar.types import ControllerRouterHandler

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.plugin import A2APlugin
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration


def _convert_kwargs_to_flags(kwargs: dict[str, Any]) -> list[str]:
    """Convert keyword arguments to Litestar CLI run flags."""
    flags: list[str] = []
    mapping = {
        "host": "--host",
        "port": "--port",
        "reload": "--reload",
        "reload_dirs": "--reload-dir",
        "reload_includes": "--reload-include",
        "reload_excludes": "--reload-exclude",
        "workers": "--web-concurrency",
        "fd": "--fd",
        "uds": "--uds",
        "debug": "--debug",
        "pdb": "--pdb",
        "ssl_certfile": "--ssl-certfile",
        "ssl_keyfile": "--ssl-keyfile",
        "create_self_signed_cert": "--create-self-signed-cert",
    }

    for key, value in kwargs.items():
        if key not in mapping:
            continue

        flag = mapping[key]
        if isinstance(value, bool):
            if value:
                flags.append(flag)
        elif isinstance(value, (list, tuple)):
            for item in value:
                flags.extend([flag, str(item)])
        elif value is not None:
            flags.extend([flag, str(value)])

    return flags


def _resolve_litestar_app_env(app: Litestar) -> str | None:
    """Attempt to resolve the import path for the Litestar application."""
    if os.getenv("LITESTAR_APP"):
        return os.getenv("LITESTAR_APP")

    frame = inspect.currentframe()
    caller_frame = None
    while frame:
        module = inspect.getmodule(frame)
        if module and "litestar_mcp" not in module.__name__:
            caller_frame = frame
            break
        frame = frame.f_back

    if not caller_frame:
        return None

    caller_globals = caller_frame.f_globals
    caller_module = inspect.getmodule(caller_frame)
    if not caller_module:
        return None

    app_var_name = None
    for name, val in caller_globals.items():
        if val is app:
            app_var_name = name
            break

    if not app_var_name:
        return None

    module_name = caller_module.__name__
    if module_name == "__main__" and hasattr(caller_module, "__file__") and caller_module.__file__:
        file_path = Path(caller_module.__file__).resolve()
        for path_str in sys.path:
            if not path_str:
                continue
            path = Path(path_str).resolve()
            if file_path.is_relative_to(path):
                rel_path = file_path.relative_to(path)
                module_name = ".".join(rel_path.with_suffix("").parts)
                break
        else:
            module_name = file_path.stem

    return f"{module_name}:{app_var_name}"


def skill(
    skill_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a function as an A2A skill.

    Args:
        skill_id: Optional unique skill identifier. Defaults to function name.
        name: Optional human-readable skill name. Defaults to skill ID.
        description: Optional skill description. Defaults to docstring summary.
        tags: Optional list of skill tags.
        **kwargs: Additional options such as legacy 'id' keyword.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        resolved_id = skill_id or kwargs.get("id") or name or fn.__name__
        resolved_name = name or skill_id or kwargs.get("id") or fn.__name__
        metadata = {
            "id": resolved_id,
            "name": resolved_name,
            "description": description or fn.__doc__,
            "tags": tags or [],
        }
        setattr(fn, "_a2a_skill", metadata)
        return fn

    return decorator


a2a_skill = skill


class Agent:
    """Standalone runner and application manager for Agent-to-Agent (A2A) protocol servers."""

    def __init__(
        self,
        name: str = "A2A Agent",
        *,
        description: str | None = None,
        version: str = "1.0.0",
        config: A2AConfig | None = None,
        plugins: list[Any] | None = None,
        route_handlers: list[ControllerRouterHandler] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the standalone Agent.

        Args:
            name: Agent name.
            description: Optional agent description.
            version: Agent version string.
            config: Optional custom A2AConfig instance.
            plugins: Optional list of additional Litestar plugins.
            route_handlers: Optional list of additional route handlers.
            **kwargs: Additional keyword arguments passed to Litestar constructor.
        """
        resolved_plugins = list(plugins or [])
        found_plugin: A2APlugin | None = None
        for p in resolved_plugins:
            if isinstance(p, A2APlugin):
                found_plugin = p
                break

        if found_plugin is None:
            self.config = config or A2AConfig()
            self.config.name = name
            if description is not None:
                self.config.description = description
            self.config.version = version
            found_plugin = A2APlugin(config=self.config)
            resolved_plugins.append(found_plugin)
        else:
            self.config = found_plugin.config
            self.config.name = name
            if description is not None:
                self.config.description = description
            self.config.version = version

        self.plugin: A2APlugin = found_plugin
        self._route_handlers = list(route_handlers or [])
        self._plugins = resolved_plugins
        self._kwargs = kwargs
        self._app: Litestar | None = None

    @property
    def registry(self) -> A2ARegistry:
        """Get the skill registry."""
        return self.plugin.registry

    @property
    def discovered_skills(self) -> dict[str, SkillRegistration]:
        """Dictionary of registered skills by ID."""
        return self.plugin.discovered_skills

    @property
    def app(self) -> Litestar:
        """Lazily instantiate and return the Litestar application."""
        if self._app is None:
            self._app = Litestar(
                route_handlers=self._route_handlers,
                plugins=self._plugins,
                **self._kwargs,
            )
        return self._app

    def skill(
        self,
        skill_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a skill directly on this Agent instance.

        Args:
            skill_id: Unique skill identifier. Defaults to function name.
            name: Human-readable skill name. Defaults to skill ID.
            description: Skill description. Defaults to docstring summary.
            tags: Optional tags list.
            **kwargs: Additional options such as legacy 'id' keyword.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            resolved_id = skill_id or kwargs.get("id") or name or fn.__name__
            resolved_name = name or skill_id or kwargs.get("id") or fn.__name__
            reg = SkillRegistration(
                fn=fn,
                id=resolved_id,
                name=resolved_name,
                description=description or fn.__doc__,
                tags=tags or [],
            )
            self.plugin.registry.register(reg)
            return fn

        return decorator

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        **kwargs: Any,
    ) -> None:
        """Run the A2A agent server application."""
        kwargs["host"] = host
        kwargs["port"] = port
        args = ["run", *_convert_kwargs_to_flags(kwargs)]
        self._execute_cli(args)

    def _execute_cli(self, args: list[str]) -> None:
        """Execute the Litestar CLI programmatically."""
        from litestar.cli._utils import LitestarEnv
        from litestar.cli.main import litestar_group

        app_path = _resolve_litestar_app_env(self.app)
        if not app_path:
            msg = (
                "Could not resolve the Litestar application import path. "
                "Please expose the Litestar instance globally (e.g., 'app = agent.app') "
                "or set the LITESTAR_APP environment variable."
            )
            raise RuntimeError(msg)

        os.environ["LITESTAR_APP"] = app_path
        env = LitestarEnv.from_env(app_path)
        litestar_group.main(args=args, obj=env)
