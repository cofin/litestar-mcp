"""Configuration for Litestar MCP Plugin."""

from collections.abc import Awaitable, Callable, Sequence  # noqa: TC003
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from litestar.stores.base import Store  # noqa: TC002

from litestar_mcp.core._streaming import DEFAULT_STREAM_CLEANUP_TIMEOUT, validate_stream_cleanup_timeout
from litestar_mcp.mcp.skills import SkillSource
from litestar_mcp.mcp.tasks import MCPTaskExecutor  # noqa: TC001

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2026-07-28", "2025-06-18", "2025-03-26")


if TYPE_CHECKING:
    from litestar import Request


class BeforeToolCallHook(Protocol):
    """Callback invoked before an MCP ``tools/call`` dispatch."""

    def __call__(
        self,
        tool_name: "str",
        arguments: "dict[str, Any]",
        request: "Request[Any, Any, Any]",
        /,
    ) -> "Awaitable[None] | None":
        """Observe a tool call before guards and handler execution."""


class AfterToolCallHook(Protocol):
    """Callback invoked after an MCP ``tools/call`` dispatch."""

    def __call__(
        self,
        tool_name: "str",
        arguments: "dict[str, Any]",
        request: "Request[Any, Any, Any]",
        /,
        *,
        result: "Any",
        exception: "Exception | None",
        duration: "float",
    ) -> "Awaitable[None] | None":
        """Observe a completed, failed, or rejected tool call."""


@dataclass(frozen=True)
class MCPOptKeys:
    """Configurable names for the ``handler.opt`` keys read by the plugin.

    Downstream apps can rename any key to avoid collisions with other plugins
    or app-specific conventions. All fields default to ``mcp_<purpose>`` and
    the pattern mirrors ``litestar.security.jwt.auth.JWTAuth.exclude_opt_key``.
    """

    tool: "str" = "mcp_tool"
    """Opt key that marks a route handler as an MCP tool (`handler.opt[tool] = "<tool-name>"`, or `True` to use the handler function name)."""
    resource: "str" = "mcp_resource"
    """Opt key that marks a route handler as an MCP resource."""
    resource_uri: "str" = "mcp_resource_uri"
    """Opt key that carries a concrete resource URI override."""
    resource_mime_type: "str" = "mcp_resource_mime_type"
    """Opt key that carries the resource MIME type for list responses and binary `resources/read` fallbacks."""
    resource_template: "str" = "mcp_resource_template"
    """Opt key that carries an RFC 6570 Level 1 URI template for the resource."""
    required_client_capabilities: "str" = "mcp_required_client_capabilities"
    """Opt key declaring standard client capabilities required before a tool may be invoked."""
    task_input_before_start: "str" = "mcp_task_input_before_start"
    """Opt key selecting a synchronous MRTR input round before task creation."""
    prompt: "str" = "mcp_prompt"
    """Opt key that marks a route handler as an MCP prompt (`handler.opt[prompt] = "<prompt-name>"`)."""
    description: "str" = "mcp_description"
    """Opt key overriding the tool description (`handler.opt[description] = "LLM prose"`)."""
    resource_description: "str" = "mcp_resource_description"
    """Opt key overriding the resource description, kept distinct from `description` for dual-role handlers."""
    prompt_description: "str" = "mcp_prompt_description"
    """Opt key overriding the prompt description."""
    prompt_title: "str" = "mcp_prompt_title"
    """Opt key overriding the prompt title."""
    prompt_arguments: "str" = "mcp_prompt_arguments"
    """Opt key overriding the prompt argument list (`list[dict]` matching the decorator's `arguments=` param)."""
    prompt_icons: "str" = "mcp_prompt_icons"
    """Opt key overriding the prompt icons list (`list[dict]` matching the decorator's `icons=` param)."""
    agent_instructions: "str" = "mcp_agent_instructions"
    """Opt key for the `## Instructions` section."""
    when_to_use: "str" = "mcp_when_to_use"
    """Opt key for the `## When to use` section."""
    returns: "str" = "mcp_returns"
    """Opt key for the `## Returns` section."""
    annotations: "str" = "mcp_annotations"
    """Opt key carrying MCP `ToolAnnotations` mapping."""
    read_only: "str" = "mcp_read_only"
    """Opt key shorthand setting `annotations.readOnlyHint`."""
    task_support: "str" = "mcp_task_support"
    """Opt key declaring MCP Tasks support mode (`optional`, `required`, `forbidden`, or soft aliases)."""
    task: "str" = "mcp_task"
    """Short alias opt key for `task_support`."""
    queue: "str" = "mcp_queue"
    """Opt key overriding the target `litestar-queues` queue name for task-augmented tool execution (or `False` / `"in_process"` to force in-process execution)."""

    def for_field(self, field_name: "str", kind: "Literal['tool', 'resource', 'prompt']") -> "str":
        """Return the opt key for ``(field_name, kind)``.

        The ``description`` field has kind-specific keys (``description`` for
        tools, ``resource_description`` for resources, ``prompt_description``
        for prompts) so a handler exposing multiple MCP roles on the same
        route can carry distinct override prose. All other fields are
        kind-agnostic.
        """
        if field_name == "description" and kind == "resource":
            return self.resource_description
        if field_name == "description" and kind == "prompt":
            return self.prompt_description
        value: str = getattr(self, field_name)
        return value


@dataclass
class MCPTaskConfig:
    """Configuration for the opt-in MCP Tasks extension."""

    store: "Store | None" = None
    """Litestar `Store` persisting task records; defaults to an in-memory store."""
    default_ttl_ms: "int" = 300_000
    """TTL in milliseconds applied when a client does not request one."""
    max_ttl_ms: "int" = 3_600_000
    """Upper bound in milliseconds for client-requested TTLs."""
    poll_interval_ms: "int" = 1_000
    """Poll interval in milliseconds advertised on task records."""
    status_callback: "Callable[[Any], Awaitable[None]] | None" = None
    """Optional coroutine invoked with every task status transition."""
    if TYPE_CHECKING:
        executor: "MCPTaskExecutor | Literal['in_process'] | bool | None" = None
        """Optional `MCPTaskExecutor` that runs task-augmented tool calls outside the creating request (`None` auto-wires `QueueTaskExecutor` when `QueuePlugin` is registered, or pass `"in_process"` / `False` to force in-process background tasks)."""
    else:
        executor: "MCPTaskExecutor | None" = None
        """Optional `MCPTaskExecutor` that runs task-augmented tool calls outside the creating request (`None` auto-wires `QueueTaskExecutor` when `QueuePlugin` is registered, or pass `"in_process"` / `False` to force in-process background tasks)."""

    def __post_init__(self) -> "None":
        if self.default_ttl_ms < 0:
            msg = "default_ttl_ms must be non-negative"
            raise ValueError(msg)
        if self.max_ttl_ms < self.default_ttl_ms:
            msg = "max_ttl_ms must be greater than or equal to default_ttl_ms"
            raise ValueError(msg)
        if self.poll_interval_ms <= 0:
            msg = "poll_interval_ms must be positive"
            raise ValueError(msg)
        disable_auto = self.executor in (False, "in_process")
        self._disable_auto_executor: bool = disable_auto
        if disable_auto:
            self.executor = None


def normalize_task_config(value: "bool | MCPTaskConfig | None") -> "MCPTaskConfig | None":
    """Normalize task configuration into a concrete config object."""
    if value is False or value is None:
        return None
    if value is True:
        return MCPTaskConfig()
    return value


@dataclass
class MCPSkillsConfig:
    """Configuration for the opt-in Skills over MCP extension."""

    paths: "Sequence[Path]" = ()
    """Directories whose immediate child folders containing a `SKILL.md` file are served as MCP skills (entries are normalized with `pathlib.Path`; optional when `sources` is non-empty)."""
    directory_read: "bool" = True
    """Whether to advertise and serve `resources/directory/read`."""
    max_files_per_skill: "int" = 512
    """Maximum files, including `SKILL.md`, one filesystem skill may contain before it is rejected at startup."""
    max_bytes_per_skill: "int" = 16_777_216
    """Maximum total byte size of one filesystem skill's files before it is rejected at startup."""
    sources: "Sequence[SkillSource]" = ()
    """Async, request-aware `SkillSource` instances composed after any `paths` source."""

    def __post_init__(self) -> "None":
        """Validate and normalize skill directory paths, sources, and size bounds."""
        if _is_bare_string_or_bytes(self.paths):
            msg = "paths must be a sequence of paths, not a single string; pass [path] instead"
            raise ValueError(msg)
        self.paths = tuple(Path(path) for path in self.paths)
        if _is_bare_string_or_bytes(self.sources):
            msg = "sources must be a sequence of SkillSource instances"
            raise TypeError(msg)
        raw_sources: Sequence[object] = self.sources
        for source in raw_sources:
            if not isinstance(source, SkillSource):
                msg = f"Every entry in sources must satisfy the SkillSource protocol, got {source!r}"
                raise TypeError(msg)
        self.sources = tuple(self.sources)
        if not self.paths and not self.sources:
            msg = "MCPSkillsConfig requires at least one entry in paths or sources"
            raise ValueError(msg)
        if self.max_files_per_skill <= 0:
            msg = "max_files_per_skill must be positive"
            raise ValueError(msg)
        if self.max_bytes_per_skill <= 0:
            msg = "max_bytes_per_skill must be positive"
            raise ValueError(msg)


@dataclass
class MCPConfig:
    """Configuration for the Litestar MCP Plugin.

    The plugin uses Litestar's opt attribute to discover routes marked for MCP exposure.
    Server name and version are derived from the Litestar app's OpenAPI configuration.
    """

    base_path: "str" = "/mcp"
    """Base path for MCP API endpoints."""
    include_in_schema: "bool" = False
    """Whether to include MCP routes in OpenAPI schema generation."""
    name: "str | None" = None
    """Optional override for server name; defaults to the OpenAPI title."""
    instructions: "str | None" = None
    """Optional server-level instructions returned in the MCP `initialize` response."""
    guards: "list[Any] | None" = None
    """Optional list of Litestar guards protecting MCP endpoints."""
    allowed_origins: "list[str] | None" = None
    """Exact additional `Origin` values to accept alongside the request origin."""
    include_operations: "list[str] | None" = None
    """Optional allowlist of operation names to expose over MCP."""
    exclude_operations: "list[str] | None" = None
    """Optional denylist of operation names to hide from MCP."""
    include_tags: "list[str] | None" = None
    """Optional allowlist of route handler tags to expose over MCP."""
    exclude_tags: "list[str] | None" = None
    """Optional denylist of route handler tags to hide from MCP."""
    tasks: "bool | MCPTaskConfig | None" = None
    """Optional task configuration or `True` to enable the default in-memory task implementation."""
    skills: "MCPSkillsConfig | None" = None
    """Optional Skills over MCP configuration; `None` leaves the extension disabled."""
    opt_keys: "MCPOptKeys" = field(default_factory=MCPOptKeys)
    """Configurable `handler.opt` key names inspected by the plugin."""
    cache_ttl_ms: "int" = 0
    """Cache duration in milliseconds for list responses (`0` disables caching)."""
    cache_scope: "Literal['private', 'public']" = "private"
    """Cache visibility scope (`"private"` or `"public"`)."""
    subscription_max_streams: "int" = 10_000
    """Maximum concurrent SSE subscription streams permitted by the server."""
    subscription_keepalive_seconds: "float" = 15.0
    """Keep-alive ping interval in seconds for active SSE subscription streams."""
    stream_queue_capacity: "int" = 256
    """Maximum queued notifications per stream before producers await capacity."""
    stream_cleanup_timeout: "float" = DEFAULT_STREAM_CLEANUP_TIMEOUT
    """Positive finite seconds allowed for cooperative progress producer cleanup after its response ends."""
    subscription_channels: "Any | None" = None
    """Optional Litestar `ChannelsPlugin` backend used to fan out subscription notifications."""
    list_page_size: "int" = 100
    """Positive integer page size for `tools/list`, `resources/list`, `resources/templates/list`, `skills/list`, `prompts/list`, and `resources/directory/read`."""
    before_tool_call: "BeforeToolCallHook | None" = None
    """Optional callback invoked once before each `tools/call` dispatch, after the synthesized request is built and before guards run."""
    after_tool_call: "AfterToolCallHook | None" = None
    """Optional callback invoked once after each `tools/call` dispatch with either the result or exception and elapsed duration in seconds."""
    max_blob_bytes: "int | None" = 25 * 1024 * 1024
    """Maximum raw byte length for base64-embedded MCP blobs (`None` disables the cap)."""
    route_opt: "dict[str, Any] | None" = None
    """Optional route `opt` mapping applied to the mounted MCP router and merged into the JSON-RPC handler's `opt`."""
    prompt_controllers: "Sequence[type[Any] | Any]" = ()
    """Optional sequence of `PromptController` classes or instances to register on startup."""
    skill_controllers: "Sequence[type[Any] | Any]" = ()
    """Optional sequence of `SkillController` classes or instances to register on startup."""
    infer_http_method_annotations: "bool" = True
    """Automatically infer default MCP `ToolAnnotations` hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`) from the route handler's HTTP method when not explicitly overridden."""
    max_tool_description_length: "int | None" = None
    """Optional maximum character length for tool descriptions enforced during tool-spec validation."""
    max_param_description_length: "int | None" = None
    """Optional maximum character length for input parameter descriptions enforced during tool-spec validation."""
    max_output_field_description_length: "int | None" = None
    """Optional maximum character length for output schema property descriptions enforced during tool-spec validation."""
    auth: "Any | None" = None
    """Optional route authentication policy merged into the mounted MCP router's `opt["auth"]` (for example `litestar_security.required(...)`)."""

    def __post_init__(self) -> "None":
        validate_stream_cleanup_timeout(self.stream_cleanup_timeout)
        if self.list_page_size <= 0:
            msg = f"list_page_size must be a positive integer, got {self.list_page_size}"
            raise ValueError(msg)
        if self.max_blob_bytes is not None and self.max_blob_bytes < 0:
            msg = f"max_blob_bytes must be non-negative or None, got {self.max_blob_bytes}"
            raise ValueError(msg)
        if self.cache_ttl_ms < 0:
            msg = f"cache_ttl_ms must be non-negative, got {self.cache_ttl_ms}"
            raise ValueError(msg)
        if self.subscription_max_streams <= 0:
            msg = f"subscription_max_streams must be positive, got {self.subscription_max_streams}"
            raise ValueError(msg)
        if self.subscription_keepalive_seconds <= 0:
            msg = f"subscription_keepalive_seconds must be positive, got {self.subscription_keepalive_seconds}"
            raise ValueError(msg)
        if self.stream_queue_capacity <= 0:
            msg = f"stream_queue_capacity must be positive, got {self.stream_queue_capacity}"
            raise ValueError(msg)
        if self.max_tool_description_length is not None and self.max_tool_description_length <= 0:
            msg = f"max_tool_description_length must be positive or None, got {self.max_tool_description_length}"
            raise ValueError(msg)
        if self.max_param_description_length is not None and self.max_param_description_length <= 0:
            msg = f"max_param_description_length must be positive or None, got {self.max_param_description_length}"
            raise ValueError(msg)
        if self.max_output_field_description_length is not None and self.max_output_field_description_length <= 0:
            msg = (
                "max_output_field_description_length must be positive or None, "
                f"got {self.max_output_field_description_length}"
            )
            raise ValueError(msg)
        if self.tasks is True:
            self.tasks = MCPTaskConfig()

    @property
    def task_config(self) -> "MCPTaskConfig | None":
        """Return the normalized task configuration, if task support is enabled."""
        return normalize_task_config(self.tasks)


def _is_bare_string_or_bytes(value: "object") -> "bool":
    return isinstance(value, (str, bytes))
