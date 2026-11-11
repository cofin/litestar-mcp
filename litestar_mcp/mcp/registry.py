"""Central registry for MCP tools, resources, and prompts."""

import copy
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar.handlers import BaseRouteHandler  # noqa: TC002

from litestar_mcp.mcp.content import PromptMessage
from litestar_mcp.utils import (
    get_handler_function,
    get_mcp_metadata,
    parse_template,
    render_description,
    should_include_handler,
)
from litestar_mcp.utils.handler_signature import (
    _parse_docstring_args,
    extract_advertised_handler_arguments,
)

if TYPE_CHECKING:
    from litestar_mcp.core.sse import SubscriptionManager
    from litestar_mcp.mcp.config import MCPConfig

_logger = logging.getLogger(__name__)

_PROMPT_CONTENT_REQUIRED_KEYS: "dict[str, frozenset[str]]" = {
    "text": frozenset({"text"}),
    "image": frozenset({"data", "mimeType"}),
    "audio": frozenset({"data", "mimeType"}),
    "resource_link": frozenset({"uri", "name"}),
    "resource": frozenset({"resource"}),
}


def _clean_title(value: "Any") -> "str | None":
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _clean_icons(value: "Any") -> "list[dict[str, Any]] | None":
    if not value or isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    copied = [copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]
    return copied or None


def _clean_meta(value: "Any") -> "dict[str, Any] | None":
    if not value or not isinstance(value, Mapping):
        return None
    copied = copy.deepcopy(dict(value))
    return copied or None


def _same_handler(left: "BaseRouteHandler", right: "BaseRouteHandler") -> "bool":
    """Return whether two Litestar handler objects wrap the same callable or bound controller method."""
    if left is right:
        return True
    fn_left = get_handler_function(left)
    fn_right = get_handler_function(right)
    return fn_left is fn_right or (
        getattr(fn_left, "__func__", None) is not None
        and getattr(fn_left, "__func__", None) is getattr(fn_right, "__func__", None)
        and type(getattr(fn_left, "__self__", None)) is type(getattr(fn_right, "__self__", None))
    )


@dataclass(frozen=True, slots=True)
class ResourceTemplate:
    """A declared RFC 6570 Level 1 URI template bound to a resource handler."""

    name: "str"
    """Logical resource name."""
    template: "str"
    """RFC 6570 Level 1 URI template string."""
    handler: "BaseRouteHandler"
    """Litestar route handler bound to the URI template."""
    title: "str | None" = None
    """Optional human-readable display title."""
    icons: "list[dict[str, Any]] | None" = field(default=None, hash=False)
    """Optional list of icon objects for UI display."""
    meta: "dict[str, Any] | None" = field(default=None, hash=False)
    """Optional MCP `_meta` descriptor mapping emitted in list and read responses."""

    def __post_init__(self) -> "None":
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "icons", _clean_icons(self.icons))
        object.__setattr__(self, "meta", _clean_meta(self.meta))


@dataclass(frozen=True, slots=True)
class PromptRegistration:
    """A registered MCP prompt — either a standalone callable or a route handler.

    Standalone prompts are plain (async) functions decorated with
    ``@mcp_prompt`` and passed to ``LitestarMCP(prompts=[...])``.

    Handler-based prompts are Litestar route handlers discovered via the
    ``mcp_prompt`` opt key, executed through the normal Litestar pipeline.
    """

    name: "str"
    """Unique prompt identifier used in `prompts/get`."""
    fn: "Callable[..., Any] | None" = None
    """The callable to invoke for standalone prompt functions."""
    handler: "BaseRouteHandler | None" = None
    """The Litestar route handler for handler-based prompts."""
    title: "str | None" = None
    """Optional human-readable display name."""
    description: "str | None" = None
    """Optional LLM-facing description."""
    arguments: "list[dict[str, Any]] | None" = field(default=None, hash=False)
    """Explicit argument definitions, or `None` to derive from the function or handler signature."""
    icons: "list[dict[str, Any]] | None" = field(default=None, hash=False)
    """Optional list of icon objects for UI display."""
    meta: "dict[str, Any] | None" = field(default=None, hash=False)
    """Optional MCP `_meta` descriptor mapping emitted in `prompts/list`."""
    instructions: "str | None" = None
    """Optional grounding instructions prepended to rendered prompt messages."""

    def __post_init__(self) -> "None":
        if self.fn is not None and self.handler is not None:
            msg = "PromptRegistration cannot have both fn and handler set"
            raise ValueError(msg)
        if self.fn is None and self.handler is None:
            msg = "PromptRegistration must have either fn or handler set"
            raise ValueError(msg)
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "icons", _clean_icons(self.icons))
        object.__setattr__(self, "meta", _clean_meta(self.meta))

    def get_arguments(self) -> "list[dict[str, Any]]":
        """Return prompt arguments, introspecting from signature if needed.

        When ``arguments`` was set explicitly, returns that list unchanged.

        For standalone prompts, walks ``inspect.signature(fn).parameters``
        (which transparently consults ``fn.__signature__`` when present and
        otherwise builds from ``fn.__code__`` / ``fn.__annotations__``).

        For handler-based prompts, walks the handler's parsed Litestar
        signature, filtering out DI dependencies and framework parameters
        (``request``, ``headers``, …) so the advertised shape matches what
        an MCP caller is expected to supply.

        Both paths enrich each entry with a Google-style docstring
        description when present.
        """
        if self.arguments is not None:
            return self.arguments
        if self.handler is not None:
            return _introspect_handler_arguments(self.handler)
        if self.fn is None:
            return []
        sig = inspect.signature(self.fn)
        doc_descriptions = _parse_docstring_args(getattr(self.fn, "__doc__", None))
        _skip = {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        args: list[dict[str, Any]] = []
        for param_name, param in sig.parameters.items():
            if param.kind in _skip or param_name == "self":
                continue
            arg: dict[str, Any] = {"name": param_name}
            desc = doc_descriptions.get(param_name)
            if desc:
                arg["description"] = desc
            arg["required"] = param.default is inspect.Parameter.empty
            args.append(arg)
        return args


def _introspect_handler_arguments(handler: "BaseRouteHandler") -> "list[dict[str, Any]]":
    """Derive prompt arguments from a route handler's parsed signature.

    DI dependencies and framework-injected names (``request``, ``headers``,
    ``state``, ...) are excluded so the advertised arguments match what
    callers actually supply via ``prompts/get``.
    Descriptions are pulled from a Google-style docstring on the underlying
    function when available.
    """
    return extract_advertised_handler_arguments(handler)


def _normalize_prompt_result(result: "Any") -> "list[dict[str, Any]]":
    """Normalize a prompt's return value to a list of PromptMessage dicts.

    * ``str`` → single user-role text message.
    * ``dict`` with a ``role`` key (presence-only, value not validated
      against the spec's ``user``/``assistant`` enumeration) and a
      ``content`` block recognised by `_PROMPT_CONTENT_REQUIRED_KEYS`
      is returned as-is, wrapped in a list.
    * ``list`` items follow the same dict rules.
    * Any other type — or any dict that doesn't look like a valid
      ``PromptMessage`` content block — is coerced to a user-role text
      message via ``str(item)`` with a ``warning`` log.

    The variant check covers the spec's ``text`` / ``image`` / ``audio`` /
    ``resource_link`` / ``resource`` content types: a content block missing
    only its outer ``role`` (e.g. an unwrapped image) is still recognised
    and not mangled into a stringified dict.
    """
    if isinstance(result, str):
        return [{"role": "user", "content": {"type": "text", "text": result}}]
    if isinstance(result, dict):
        return [_coerce_prompt_message(result, index=None)]
    if isinstance(result, list):
        return [_coerce_prompt_message(item, index=i) for i, item in enumerate(result)]
    _logger.warning("Prompt returned unexpected type %s, coercing to string", type(result).__name__)
    return [{"role": "user", "content": {"type": "text", "text": str(result)}}]


def apply_prompt_instructions(messages: "list[dict[str, Any]]", instructions: "str | None") -> "list[dict[str, Any]]":
    """Prefix instructions to the first user text message, or prepend a user message when none exists."""
    if not instructions:
        return messages
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, dict) and content.get("type") == "text":
            updated = dict(message)
            updated["content"] = {**content, "text": f"{instructions}\n\n{content['text']}"}
            return [*messages[:index], updated, *messages[index + 1 :]]
    return [{"role": "user", "content": {"type": "text", "text": instructions}}, *messages]


def _coerce_prompt_message(item: "Any", *, index: "int | None") -> "dict[str, Any]":
    """Coerce a single result element into a valid ``PromptMessage`` dict.

    Recognises:
      * PromptMessage instances (converted via to_mcp_dict).
      * Already-shaped messages (``role`` + ``content`` where ``content`` is
        a valid content block or list of content blocks).
      * Unwrapped content blocks (``type`` + variant-required keys) — wrapped
        in a ``user``-role envelope.
      * Anything else — stringified with a warning.
    """
    if isinstance(item, PromptMessage):
        return item.to_mcp_dict()

    if not isinstance(item, dict):
        _logger.warning(
            "Prompt result element %sis not a dict (%s), coercing to string",
            f"at index {index} " if index is not None else "",
            type(item).__name__,
        )
        return {"role": "user", "content": {"type": "text", "text": str(item)}}

    if "role" in item and "content" in item:
        content = item["content"]
        if _looks_like_content(content) or _looks_like_content_list(content):
            return item

    if _looks_like_content(item):
        return {"role": "user", "content": item}

    _logger.warning(
        "Prompt result element %sdid not match PromptMessage shape (keys=%s), coercing to string",
        f"at index {index} " if index is not None else "",
        list(item.keys()),
    )
    return {"role": "user", "content": {"type": "text", "text": str(item)}}


def _looks_like_content(value: "Any") -> "bool":
    """True when ``value`` is a dict matching a known content-block variant."""
    if not isinstance(value, dict):
        return False
    variant = value.get("type")
    required = _PROMPT_CONTENT_REQUIRED_KEYS.get(variant) if isinstance(variant, str) else None
    return required is not None and required.issubset(value.keys())


def _looks_like_content_list(value: "Any") -> "bool":
    """True when ``value`` is a non-empty list of valid content-block dicts."""
    return isinstance(value, list) and bool(value) and all(_looks_like_content(item) for item in value)


def resolve_prompt_description(registration: "PromptRegistration", config: "MCPConfig") -> "str | None":
    """Resolve the description string for a registered prompt.

    Handler-based prompts run through ``render_description`` so opt-key
    overrides, structured sections, and docstring fallbacks all apply
    consistently with tools and resources. Standalone prompts use the
    description captured at registration time (decorator value or
    ``fn.__doc__`` fallback) — there's no opt-key plumbing on a bare fn.
    """
    if registration.handler is not None:
        fn = get_handler_function(registration.handler)
        return render_description(
            registration.handler,
            fn,
            kind="prompt",
            fallback_name=registration.name,
            opt_keys=config.opt_keys,
        )
    return registration.description


def render_prompt_entry(registration: "PromptRegistration", config: "MCPConfig") -> "dict[str, Any]":
    """Build a Prompt entry dict for `prompts/list` and the server manifest.

    Single source of truth for the wire shape so route and manifest rendering
    stay aligned. Optional fields (`title`, `description`, `arguments`,
    `icons`, and `_meta`) are omitted when absent rather than emitted as `null`.
    """
    raw_opt = getattr(registration.handler, "opt", None) if registration.handler is not None else None
    handler_opt: Mapping[str, Any] = raw_opt if isinstance(raw_opt, Mapping) else {}
    opt_keys = config.opt_keys

    entry: dict[str, Any] = {"name": registration.name}
    title = _clean_title(handler_opt.get(opt_keys.prompt_title)) or _clean_title(registration.title)
    if title is not None:
        entry["title"] = title
    description = resolve_prompt_description(registration, config)
    if description is not None:
        entry["description"] = description
    arguments = registration.get_arguments()
    if arguments:
        entry["arguments"] = arguments
    icons = _clean_icons(handler_opt.get(opt_keys.prompt_icons)) or _clean_icons(registration.icons)
    if icons is not None:
        entry["icons"] = icons
    merged_meta: dict[str, Any] = {}
    reg_meta = _clean_meta(registration.meta)
    if reg_meta is not None:
        merged_meta.update(reg_meta)
    opt_meta = _clean_meta(handler_opt.get(opt_keys.prompt_meta))
    if opt_meta is not None:
        merged_meta.update(opt_meta)
    if merged_meta:
        entry["_meta"] = merged_meta
    return entry


def should_include_prompt(registration: "PromptRegistration", config: "MCPConfig") -> "bool":
    """Apply ``include/exclude_operations`` and tag filters to a prompt.

    Handler-based prompts get the full filter set (tags + name).
    Standalone (fn-based) prompts skip the tag filters — they have no
    handler tags to test — but ``include_operations`` /
    ``exclude_operations`` name filters still apply because they select
    by prompt name, which fn-based prompts have just like everything else.
    """
    if registration.handler is not None:
        handler_tags = set(getattr(registration.handler, "tags", None) or [])
        return should_include_handler(registration.name, handler_tags, config)
    if config.exclude_operations and registration.name in config.exclude_operations:
        return False
    return not (config.include_operations and registration.name not in config.include_operations)


class Registry:
    """Central registry for MCP tools, resources, and prompts.

    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.

    Note:
        Tools and resources are stored as bare ``BaseRouteHandler`` values
        because every entry has a single underlying handler. Prompts use
        `PromptRegistration` instead — a prompt may originate from
        either a standalone ``@mcp_prompt`` callable *or* a route handler,
        so the dataclass carries the ``fn | handler`` union plus the
        per-prompt metadata (title, description, arguments, icons) that
        can't live on a bare callable.
    """

    def __init__(self) -> "None":
        """Initialize the registry."""
        self._tools: dict[str, BaseRouteHandler] = {}
        self._resources: dict[str, BaseRouteHandler] = {}
        self._templates: dict[str, ResourceTemplate] = {}
        self._prompts: dict[str, PromptRegistration] = {}
        self._subscription_manager: SubscriptionManager | None = None
        self._change_callbacks: list[Callable[[], None]] = []

    def register_change_callback(self, callback: "Callable[[], None]") -> "None":
        """Register a callback to be invoked when the registry changes.

        Args:
            callback: The callback function.
        """
        self._change_callbacks.append(callback)

    def unregister_change_callback(self, callback: "Callable[[], None]") -> "None":
        """Unregister a change callback.

        Args:
            callback: The callback function to remove.
        """
        if callback in self._change_callbacks:
            self._change_callbacks.remove(callback)

    def _trigger_change(self) -> "None":
        """Invoke all registered change callbacks."""
        _logger.debug("Triggering change callbacks, count: %d", len(self._change_callbacks))
        for callback in self._change_callbacks:
            _logger.debug(
                "Invoking callback: %s", callback.__name__ if hasattr(callback, "__name__") else str(callback)
            )
            callback()

    def set_subscription_manager(self, manager: "SubscriptionManager") -> "None":
        """Set the subscription manager for notifications."""
        self._subscription_manager = manager

    @property
    def has_subscription_manager(self) -> "bool":
        """Return whether a subscription manager has been configured on this registry."""
        return self._subscription_manager is not None

    @property
    def subscription_manager(self) -> "SubscriptionManager":
        """Return the configured subscription manager."""
        if self._subscription_manager is None:
            msg = "Subscription manager has not been configured"
            raise RuntimeError(msg)
        return self._subscription_manager

    @property
    def tools(self) -> "dict[str, BaseRouteHandler]":
        """Get registered tools."""
        return self._tools

    @property
    def resources(self) -> "dict[str, BaseRouteHandler]":
        """Get registered resources."""
        return self._resources

    def register_tool(self, name: "str", handler: "BaseRouteHandler") -> "None":
        """Register a tool.

        Args:
            name: The tool name.
            handler: The route handler.
        """
        existing = self._tools.get(name)
        if existing is not None and _same_handler(existing, handler):
            self._tools[name] = handler
            return
        if existing is not None:
            _logger.warning("Overwriting existing tool registration: %s", name)
        self._tools[name] = handler
        self._trigger_change()

    def register_resource(self, name: "str", handler: "BaseRouteHandler") -> "None":
        """Register a resource.

        Args:
            name: The resource name.
            handler: The route handler.
        """
        existing = self._resources.get(name)
        if existing is not None and _same_handler(existing, handler):
            self._resources[name] = handler
            return
        if existing is not None:
            _logger.warning("Overwriting existing resource registration: %s", name)
        self._resources[name] = handler
        self._trigger_change()

    @property
    def templates(self) -> "dict[str, ResourceTemplate]":
        """Get registered resource templates, keyed by resource name."""
        return self._templates

    def register_resource_template(
        self,
        name: "str",
        handler: "BaseRouteHandler",
        template: "str",
        *,
        title: "str | None" = None,
        icons: "list[dict[str, Any]] | None" = None,
        meta: "dict[str, Any] | None" = None,
    ) -> "None":
        """Register an RFC 6570 Level 1 URI template for a resource.

        Args:
            name: The resource name (same key as `register_resource`).
            handler: The route handler bound to the template.
            template: The URI template string. Validated at registration;
                invalid templates raise `ValueError`.
            title: Optional human-readable display title.
            icons: Optional list of icon objects for UI display.
            meta: Optional MCP `_meta` descriptor mapping.
        """
        parse_template(template)
        cleaned_title = _clean_title(title)
        cleaned_icons = _clean_icons(icons)
        cleaned_meta = _clean_meta(meta)
        existing = self._templates.get(name)
        if existing is not None and _same_handler(existing.handler, handler) and existing.template == template:
            merged_meta: dict[str, Any] = {}
            if existing.meta:
                merged_meta.update(copy.deepcopy(existing.meta))
            if cleaned_meta:
                merged_meta.update(cleaned_meta)
            self._templates[name] = ResourceTemplate(
                name=name,
                template=template,
                handler=handler,
                title=cleaned_title or existing.title,
                icons=cleaned_icons or existing.icons,
                meta=merged_meta or None,
            )
            return
        if name in self._templates:
            _logger.warning("Overwriting existing resource template registration: %s", name)
        self._templates[name] = ResourceTemplate(
            name=name,
            template=template,
            handler=handler,
            title=cleaned_title,
            icons=cleaned_icons,
            meta=cleaned_meta,
        )
        self._trigger_change()

    @property
    def prompts(self) -> "dict[str, PromptRegistration]":
        """Get registered prompts."""
        return self._prompts

    def register_prompt(
        self,
        name: "str",
        fn: "Callable[..., Any]",
        *,
        title: "str | None" = None,
        description: "str | None" = None,
        arguments: "list[dict[str, Any]] | None" = None,
        icons: "list[dict[str, Any]] | None" = None,
        meta: "dict[str, Any] | None" = None,
    ) -> "None":
        """Register a standalone prompt function.

        Args:
            name: Unique prompt identifier.
            fn: The callable to invoke on `prompts/get`.
            title: Optional human-readable display name.
            description: Optional description. Falls back to `fn.__doc__`.
            arguments: Explicit argument definitions. When `None`, derived
                from the function signature.
            icons: Optional list of icon objects for UI display.
            meta: Optional MCP `_meta` descriptor mapping emitted in `prompts/list`.
        """
        if name in self._prompts:
            _logger.warning("Overwriting existing prompt registration: %s", name)
        desc = description
        if desc is None:
            doc = getattr(fn, "__doc__", None)
            if isinstance(doc, str) and doc.strip():
                desc = doc.strip()
        self._prompts[name] = PromptRegistration(
            name=name,
            fn=fn,
            title=_clean_title(title),
            description=desc,
            arguments=arguments,
            icons=_clean_icons(icons),
            meta=_clean_meta(meta),
        )
        self._trigger_change()

    def register_prompt_handler(
        self,
        name: "str",
        handler: "BaseRouteHandler",
        *,
        title: "str | None" = None,
        description: "str | None" = None,
        arguments: "list[dict[str, Any]] | None" = None,
        icons: "list[dict[str, Any]] | None" = None,
        meta: "dict[str, Any] | None" = None,
        instructions: "str | None" = None,
    ) -> "None":
        """Register a route-handler-based prompt.

        Storage only — runtime dispatch and the `messages`-passthrough vs.
        normalize-on-return decision live in
        `litestar_mcp.mcp.routes.handle_prompts_get`. This function captures
        the handler reference plus any explicit overrides so the registry can
        render `prompts/list` entries without executing the handler.

        Args:
            name: Unique prompt identifier.
            handler: The Litestar route handler.
            title: Optional human-readable display name.
            description: Optional description.
            arguments: Explicit argument definitions. When `None`, arguments
                are introspected from the handler's parsed signature at
                `prompts/list` render time (DI- and framework-injected
                parameters filtered out). Pass `[]` to advertise no arguments
                explicitly.
            icons: Optional list of icon objects for UI display.
            meta: Optional MCP `_meta` descriptor mapping emitted in `prompts/list`.
            instructions: Grounding instructions prepended to prompt output.
        """
        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler, kind="prompt") or get_mcp_metadata(fn, kind="prompt") or {}
        desc = description if description is not None else metadata.get("description")
        resolved_title = _clean_title(title) or _clean_title(metadata.get("title"))
        resolved_icons = _clean_icons(icons) or _clean_icons(metadata.get("icons"))
        merged_meta: dict[str, Any] = {}
        dec_meta = _clean_meta(metadata.get("meta"))
        if dec_meta is not None:
            merged_meta.update(dec_meta)
        explicit_meta = _clean_meta(meta)
        if explicit_meta is not None:
            merged_meta.update(explicit_meta)
        final_meta = merged_meta or None

        existing = self._prompts.get(name)
        if existing is not None and existing.handler is not None and _same_handler(existing.handler, handler):
            keep_meta: dict[str, Any] = {}
            if existing.meta:
                keep_meta.update(copy.deepcopy(existing.meta))
            if final_meta:
                keep_meta.update(final_meta)
            self._prompts[name] = PromptRegistration(
                name=name,
                handler=handler,
                title=resolved_title or existing.title,
                description=desc if desc is not None else existing.description,
                arguments=arguments if arguments is not None else existing.arguments,
                icons=resolved_icons or existing.icons,
                meta=keep_meta or None,
                instructions=instructions if instructions is not None else existing.instructions,
            )
            return
        if existing is not None:
            _logger.warning("Overwriting existing prompt registration: %s", name)
        self._prompts[name] = PromptRegistration(
            name=name,
            handler=handler,
            title=resolved_title,
            description=desc,
            arguments=arguments if arguments is not None else metadata.get("arguments"),
            icons=resolved_icons,
            meta=final_meta,
            instructions=instructions,
        )
        self._trigger_change()

    async def publish_notification(
        self,
        method: "str",
        params: "dict[str, Any]",
        *,
        owner_id: "str | None" = None,
        session_id: "str | None" = None,
    ) -> "None":
        """Publish a JSON-RPC 2.0 notification to connected clients.

        Args:
            method: The notification method (e.g., `notifications/resources/updated`).
            params: The notification parameters.
            owner_id: Optional authenticated owner identifier restricting delivery to matching streams.
            session_id: Optional session identifier restricting delivery to streams bound to that session.
        """
        if self._subscription_manager is not None:
            kwargs: dict[str, Any] = {}
            if owner_id is not None:
                kwargs["owner_id"] = owner_id
            if session_id is not None:
                kwargs["session_id"] = session_id
            await self._subscription_manager.publish(method, params, **kwargs)

    async def notify_resource_updated(self, uri: "str", *, owner_id: "str | None" = None) -> "None":
        """Notify clients that a resource has been updated.

        Args:
            uri: The URI of the updated resource.
            owner_id: Optional authenticated owner identifier restricting delivery to matching streams.
        """
        await self.publish_notification("notifications/resources/updated", {"uri": uri}, owner_id=owner_id)

    async def notify_resources_list_changed(self) -> "None":
        """Notify clients that the resource list has changed."""
        await self.publish_notification("notifications/resources/list_changed", {})

    async def notify_tools_list_changed(self) -> "None":
        """Notify clients that the tool list has changed."""
        await self.publish_notification("notifications/tools/list_changed", {})

    async def notify_prompts_list_changed(self) -> "None":
        """Notify clients that the prompt list has changed."""
        await self.publish_notification("notifications/prompts/list_changed", {})

    async def notify_skills_list_changed(self) -> "None":
        """Notify clients that the skill and derived resource lists have changed."""
        await self.publish_notification("notifications/skills/list_changed", {})
        await self.publish_notification("notifications/resources/list_changed", {})


__all__ = (
    "PromptRegistration",
    "Registry",
    "ResourceTemplate",
    "apply_prompt_instructions",
)
