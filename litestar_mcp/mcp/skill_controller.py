"""Class-based controller unifying tools, prompts, instructions, and guards into a cohesive skill."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, get_type_hints

from litestar import Request, Response
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import BaseRouteHandler, post
from litestar.serialization import encode_json

from litestar_mcp.agent.context import resolve_tool_context
from litestar_mcp.agent.tools import RunContextRegistry, Tool, _is_context_parameter
from litestar_mcp.mcp.config import MCPOptKeys
from litestar_mcp.mcp.prompt_controller import PromptController
from litestar_mcp.utils import get_mcp_metadata

if TYPE_CHECKING:
    from collections.abc import Sequence

_INTERNAL_DISPATCH_SCOPE_KEY = "litestar_mcp.internal_dispatch"


def _require_internal_dispatch(connection: Any, _route_handler: Any) -> None:
    """Reject direct HTTP access to standalone internal MCP tool routes."""
    if not connection.scope.get(_INTERNAL_DISPATCH_SCOPE_KEY):
        msg = "Standalone MCP internal routes are not directly accessible"
        raise PermissionDeniedException(msg)


def _to_json_response(res: Any) -> Response[Any]:
    """Serialize a tool result into a Litestar JSON Response."""
    if isinstance(res, Response):
        return res
    if res is None or isinstance(res, (str, bytes, bytearray)):
        return Response(content=encode_json(res), media_type="application/json")
    return Response(content=res, media_type="application/json")


def _extract_tool_callable_info(
    tool_or_fn: Any,
) -> tuple[Any, Any, str, str, set[str]]:
    """Extract raw callable, original function, name, description, and stripped params."""
    if isinstance(tool_or_fn, Tool):
        return (
            tool_or_fn.bound_fn,
            tool_or_fn.fn,
            tool_or_fn.name,
            tool_or_fn.description,
            set(tool_or_fn.stripped_parameters),
        )
    tool_obj = getattr(tool_or_fn, "__tool_instance__", None)
    raw_fn = tool_or_fn
    orig_fn = getattr(raw_fn, "__func__", raw_fn)
    meta = get_mcp_metadata(raw_fn) or get_mcp_metadata(orig_fn) or {}
    tool_name = str(meta.get("name") or getattr(orig_fn, "__name__", "tool"))
    description = str(meta.get("description") or inspect.cleandoc(getattr(orig_fn, "__doc__", "") or ""))
    stripped = set(tool_obj.stripped_parameters) if isinstance(tool_obj, Tool) else set()
    return raw_fn, orig_fn, tool_name, description, stripped


def _build_public_tool_signature(
    orig_fn: Any,
    stripped_params: set[str],
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build a public inspect.Signature and annotation map with self and ToolContext stripped."""
    try:
        hints = get_type_hints(orig_fn, include_extras=True)
    except (NameError, TypeError, AttributeError, ValueError):
        hints = {}
    sig = inspect.signature(orig_fn)
    public_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if pname in stripped_params or _is_context_parameter(pname, param):
            stripped_params.add(pname)
            continue
        resolved_ann = hints.get(pname, param.annotation)
        if resolved_ann is inspect.Parameter.empty or isinstance(resolved_ann, str):
            resolved_ann = str
        public_params.append(
            inspect.Parameter(
                name=pname,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=param.default,
                annotation=resolved_ann,
            )
        )
        annotations[pname] = resolved_ann

    public_params.append(
        inspect.Parameter(
            name="request",
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=Request,
        )
    )
    annotations["request"] = Request
    annotations["return"] = Response
    return inspect.Signature(parameters=public_params, return_annotation=Response), annotations


def build_tool_route_handler(
    tool_or_fn: Any,
    *,
    base_path: str = "/mcp",
    opt_keys: MCPOptKeys | None = None,
    guards: Sequence[Any] | None = None,
) -> tuple[str, BaseRouteHandler]:
    """Wrap a Tool instance or callable into an internal Litestar BaseRouteHandler."""
    keys = opt_keys or MCPOptKeys()
    if isinstance(tool_or_fn, BaseRouteHandler):
        meta = get_mcp_metadata(tool_or_fn) or {}
        opt = getattr(tool_or_fn, "opt", None) or {}
        existing_name = str(meta.get("name") or opt.get(keys.tool) or getattr(tool_or_fn, "handler_name", "tool"))
        return existing_name, tool_or_fn

    raw_fn, orig_fn, tool_name, description, stripped_params = _extract_tool_callable_info(tool_or_fn)
    public_sig, annotations = _build_public_tool_signature(orig_fn, stripped_params)

    async def _tool_endpoint(**kwargs: Any) -> Response[Any]:
        req = kwargs.pop("request", None)
        ctx = resolve_tool_context(req)
        if isinstance(tool_or_fn, Tool):
            res = await tool_or_fn(**kwargs, __tool_context__=ctx)
        else:
            call_kwargs = dict(kwargs)
            for p in stripped_params:
                if p not in call_kwargs:
                    if p in {"ctx", "context", "tool_context"}:
                        call_kwargs[p] = ctx
                    elif p == "request":
                        call_kwargs[p] = req
                    elif p == "user":
                        call_kwargs[p] = ctx.user
                    elif p in {"tenant_id", "tenant"}:
                        call_kwargs[p] = ctx.tenant_id
                    elif p == "state":
                        call_kwargs[p] = ctx.state
            with RunContextRegistry.bound(ctx):
                res = await raw_fn(**call_kwargs) if inspect.iscoroutinefunction(raw_fn) else raw_fn(**call_kwargs)
        return _to_json_response(res)

    _tool_endpoint.__name__ = tool_name
    _tool_endpoint.__qualname__ = tool_name
    _tool_endpoint.__doc__ = description or getattr(orig_fn, "__doc__", None)
    _tool_endpoint.__annotations__ = annotations
    setattr(_tool_endpoint, "__signature__", public_sig)

    path = "/".join((base_path.rstrip("/"), "internal", "tools", tool_name))
    route_guards = [_require_internal_dispatch, *(guards or [])]
    opt_dict: dict[str, Any] = {keys.tool: tool_name}
    if description:
        opt_dict[keys.description] = description

    handler = post(
        path=path,
        guards=route_guards,
        opt=opt_dict,
        include_in_schema=False,
    )(_tool_endpoint)
    return tool_name, handler


class SkillController(PromptController):
    """Domain controller unifying tools, prompts, instructions, and guards.

    Provides a cohesive boundary for agent skills that can be registered
    directly on LitestarMCP, embedded into an Agent, or published to A2A.
    """

    name: str = ""
    description: str = ""
    instructions: str | None = ""
    prefix: str | None = ""
    examples: Sequence[str] | None = None

    def get_instructions(self) -> str:
        """Return the grounding rules or system instructions for this skill."""
        return self.instructions or ""

    def _resolve_prompt_prefix(self) -> str:
        """Resolve the namespace prefix for prompts declared on this skill controller."""
        return self.prefix or self.name or ""

    def get_tools(self) -> list[Any]:
        """Discover and return all callable tools declared on this skill controller."""
        tools: list[Any] = []
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            attr = getattr(self, attr_name, None)
            if not callable(attr):
                continue
            meta = get_mcp_metadata(attr)
            is_agent_tool = getattr(attr, "__agent_tool__", False) or isinstance(attr, Tool)
            if (meta and meta.get("type") == "tool") or is_agent_tool:
                tools.append(attr)
        return tools

    def to_agent_skill(self) -> dict[str, Any]:
        """Convert this controller's metadata to an A2A AgentSkill wire model."""
        skill_name = self.name or self.__class__.__name__
        skill_id = self.name or skill_name.lower()
        skill_desc = self.description or inspect.cleandoc(self.__class__.__doc__ or "")
        tags = getattr(self, "tags", None) or []
        resolved_examples: list[str] = list(self.examples) if self.examples else []
        if not resolved_examples:
            for t in self.get_tools():
                doc = getattr(t, "description", None) or getattr(t, "__doc__", None)
                if doc:
                    resolved_examples.append(inspect.cleandoc(str(doc)))
        return {
            "id": skill_id,
            "name": skill_name,
            "description": skill_desc,
            "tags": list(tags),
            "examples": resolved_examples,
        }

    def to_a2a_skill(self) -> dict[str, Any]:
        """Project this controller's metadata to an A2A AgentCard AgentSkill dictionary."""
        return self.to_agent_skill()


__all__ = (
    "SkillController",
    "build_tool_route_handler",
)
