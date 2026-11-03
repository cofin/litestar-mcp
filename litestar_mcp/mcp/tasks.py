"""Durable support for the ``io.modelcontextprotocol/tasks`` extension."""

import asyncio
import contextlib
import dataclasses
import importlib
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import msgspec
from litestar.serialization import decode_json, encode_json
from litestar.stores.base import Store
from litestar.stores.memory import MemoryStore

from litestar_mcp.core.jsonrpc import JSONRPCError

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.config.app import AppConfig

DEFAULT_PROTOCOL_VERSION: str = "2026-07-28"
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
_PRINCIPAL_ATTR_KEYS: tuple[str, ...] = (
    "id",
    "sub",
    "user_id",
    "account_id",
    "username",
    "name",
    "email",
    "is_active",
    "roles",
    "scopes",
    "permissions",
    "hives",
    "tenant",
    "tenant_id",
)


class PrincipalProxy(dict[str, Any]):
    """Dictionary-backed principal snapshot supporting both key and attribute lookup."""

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        if data is not None:
            for key, value in data.items():
                self[key] = _wrap_proxy_value(value)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _wrap_proxy_value(value: Any) -> Any:
    if isinstance(value, PrincipalProxy):
        return value
    if isinstance(value, Mapping):
        return PrincipalProxy(value)
    if isinstance(value, list):
        return [_wrap_proxy_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_wrap_proxy_value(item) for item in value)
    return value


class TaskLookupError(ValueError):
    """Raised when a task cannot be found or accessed."""


class TaskStateError(ValueError):
    """Raised when a task transition is invalid."""


class ToolTaskCall(msgspec.Struct, kw_only=True):
    """Serializable dispatch envelope for one task-augmented ``tools/call`` attempt."""

    task_id: str
    """Unique identifier of the task record in `MCPTaskStore`."""
    tool_name: str
    """Registered name of the MCP tool being executed."""
    arguments: dict[str, Any]
    """Tool invocation arguments supplied by the caller."""
    owner_id: str | None = None
    """Optional task-owner identifier scoping access to the task record."""
    principal: dict[str, Any] | None = None
    """Serialized principal snapshot captured from the creating request."""
    queue: str | None = None
    """Optional target `litestar-queues` queue name override."""
    client_id: str = "queue"
    """Identifier of the originating MCP client."""
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    """Negotiated MCP protocol version for the task execution."""
    client_capabilities: dict[str, Any] | None = None
    """Client capabilities dictionary captured from the creating request."""
    client_info: dict[str, Any] | None = None
    """Client implementation metadata captured from the creating request."""
    input_responses: dict[str, Any] | None = None
    """Accumulated interactive input responses supplied via `tasks/update`."""
    request_state: str | None = None
    """Opaque round-tripped state token accompanying `input_responses`."""
    attempt: int = 1
    """1-based execution attempt counter incremented on each `tasks/update` resume."""


@dataclass
class MCPTaskRuntime:
    """Collaborators an executor needs once the application has started."""

    app: "Litestar"
    """The running Litestar application instance."""
    task_store: "MCPTaskStore"
    """The active `MCPTaskStore` managing persisted task records."""
    run_attempt: Callable[[ToolTaskCall, Any], Awaitable[None]]
    """Async callback that executes a single `ToolTaskCall` attempt against the handler service."""


class MCPTaskExecutor(Protocol):
    """Runs task-augmented tool calls outside the request that created them.

    ``LitestarMCP`` calls `configure` during app init so the executor can
    wire itself into other plugins, and `bind` at startup with the
    collaborators that need the running application.
    """

    def configure(self, app_config: "AppConfig") -> None:
        """Register executor hooks or dependencies on the Litestar `AppConfig` during initialization."""
        ...

    def bind(self, runtime: MCPTaskRuntime) -> None:
        """Bind the running application, task store, and attempt runner callback at startup."""
        ...

    async def submit(self, record: "TaskRecord", call: ToolTaskCall) -> None:
        """Enqueue or schedule initial execution of a newly created task-augmented tool call."""
        ...

    async def resume(self, call: ToolTaskCall) -> None:
        """Schedule a resumed execution attempt after client input responses are received."""
        ...

    async def cancel(self, record: "TaskRecord") -> None:
        """Cancel an in-flight task execution backed by this executor."""
        ...


@dataclass
class TaskRecord:
    """Persisted Tasks extension state."""

    task_id: str
    """Unique task identifier (`taskId`)."""
    owner_id: str | None
    """Optional owner identifier restricting task access to the creating principal."""
    status: str
    """Current MCP task lifecycle status (`working`, `input_required`, `completed`, `failed`, or `cancelled`)."""
    created_at: datetime
    """UTC timestamp when the task record was created."""
    last_updated_at: datetime
    """UTC timestamp of the most recent task state transition."""
    ttl_ms: int | None
    """Time-to-live in milliseconds for the persisted task record."""
    poll_interval_ms: int | None
    """Suggested client polling interval in milliseconds."""
    status_message: str | None = None
    """Optional human-readable status message describing current task progress."""
    input_requests: dict[str, dict[str, Any]] | None = None
    """Pending multi-round-trip input request descriptors when `status == "input_required"`."""
    request_state: str | None = None
    """Opaque round-tripped state token preserved across `input_required` pauses."""
    result: dict[str, Any] | None = None
    """Final MCP tool result dictionary when `status == "completed"`."""
    error: JSONRPCError | None = None
    """Terminal JSON-RPC error object when `status == "failed"`."""
    dispatch: dict[str, Any] | None = None
    """Serialized `ToolTaskCall` payload persisted for external executor resume."""
    input_responses: dict[str, Any] | None = None
    """Most recent client input responses submitted via `tasks/update`."""

    def is_terminal(self) -> bool:
        """Return whether the task has reached a terminal state."""
        return self.status in TERMINAL_TASK_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Return the extension wire representation."""
        payload: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status,
            "createdAt": _format_datetime(self.created_at),
            "lastUpdatedAt": _format_datetime(self.last_updated_at),
            "ttlMs": self.ttl_ms,
        }
        if self.poll_interval_ms is not None:
            payload["pollIntervalMs"] = self.poll_interval_ms
        if self.status_message is not None:
            payload["statusMessage"] = self.status_message
        if self.input_requests is not None:
            payload["inputRequests"] = self.input_requests
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload["error"] = {
                "code": self.error.code,
                "message": self.error.message,
                **({"data": self.error.data} if self.error.data is not None else {}),
            }
        return payload


class MCPTaskStore:
    """Task records backed by a Litestar `Store`.

    Only serializable records are persisted. Runner tasks, cancellation flags,
    and input queues are process-local coordination primitives; production
    deployments use a shared Store for retrieval and a shared notification
    backend for fan-out.
    """

    def __init__(
        self,
        store: Store | None = None,
        default_ttl_ms: int = 300_000,
        max_ttl_ms: int = 3_600_000,
        poll_interval_ms: int = 1_000,
        status_callback: Callable[[TaskRecord], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store or MemoryStore()
        self.default_ttl_ms = default_ttl_ms
        self.max_ttl_ms = max_ttl_ms
        self.poll_interval_ms = poll_interval_ms
        self.status_callback = status_callback
        self._lock = asyncio.Lock()
        self._runners: dict[str, asyncio.Task[Any]] = {}
        self._input_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def set_status_callback(self, callback: Callable[[TaskRecord], Awaitable[None]] | None) -> None:
        """Set the task-state notification callback."""
        self.status_callback = callback

    async def create(
        self,
        owner_id: str | None,
        ttl_ms: int | None = None,
        *,
        local: bool = True,
    ) -> TaskRecord:
        """Durably create a task before returning its handle."""
        resolved_ttl = self._resolve_ttl(ttl_ms)
        now = _utc_now()
        record = TaskRecord(
            task_id=uuid4().hex,
            owner_id=owner_id,
            status="working",
            created_at=now,
            last_updated_at=now,
            ttl_ms=resolved_ttl,
            poll_interval_ms=self.poll_interval_ms,
            status_message="The operation is now in progress.",
        )
        async with self._lock:
            if local:
                self._input_queues[record.task_id] = asyncio.Queue()
                self._cancel_events[record.task_id] = asyncio.Event()
            await self._save(record)
        await self._notify(record)
        return record

    async def attach_runner(self, task_id: str, runner: asyncio.Task[Any]) -> None:
        """Attach a process-local runner to a durable task."""
        async with self._lock:
            await self._lookup(task_id, None)
            self._runners[task_id] = runner

    def has_local_runner(self, task_id: str) -> bool:
        """Return whether a process-local runner is attached to ``task_id``."""
        return task_id in self._runners

    async def set_job_id(self, task_id: str, job_id: str) -> None:
        """Persist the executor's job id for the task's current attempt under a separate key."""
        record = await self.get(task_id, None)
        expires_in = None if record.ttl_ms is None else max(1, (record.ttl_ms + 999) // 1000)
        await self.store.set(f"mcp-task:{task_id}:job", job_id.encode("utf-8"), expires_in=expires_in)

    async def get_job_id(self, task_id: str) -> str | None:
        """Retrieve the executor's job id for the task's current attempt."""
        value = await self.store.get(f"mcp-task:{task_id}:job")
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)

    async def set_dispatch(self, task_id: str, call: ToolTaskCall) -> None:
        """Save the serializable dispatch envelope on the record before executor submission."""
        async with self._lock:
            record = await self._lookup(task_id, None)
            record.dispatch = msgspec.to_builtins(call)
            record.last_updated_at = _utc_now()
            await self._save(record)

    async def get_dispatch(self, task_id: str) -> ToolTaskCall | None:
        """Retrieve and decode the serializable dispatch envelope."""
        async with self._lock:
            record = await self._lookup(task_id, None)
            if record.dispatch is None:
                return None
            return msgspec.convert(record.dispatch, ToolTaskCall)

    async def get(self, task_id: str, owner_id: str | None) -> TaskRecord:
        """Retrieve a task, enforcing authenticated ownership when present."""
        async with self._lock:
            return await self._lookup(task_id, owner_id)

    async def complete(self, task_id: str, result: dict[str, Any]) -> TaskRecord:
        """Persist a successful tool result."""
        return await self._transition(task_id, "completed", result=result, status_message=None)

    async def fail(self, task_id: str, error: JSONRPCError, status_message: str | None = None) -> TaskRecord:
        """Persist a failed task."""
        return await self._transition(task_id, "failed", error=error, status_message=status_message or error.message)

    async def require_input(
        self,
        task_id: str,
        input_requests: dict[str, dict[str, Any]] | None,
        request_state: str | None,
    ) -> TaskRecord:
        """Pause a task until the client supplies input via ``tasks/update``."""
        return await self._transition(
            task_id,
            "input_required",
            input_requests=input_requests or {},
            request_state=request_state,
            status_message="Additional client input is required.",
        )

    async def wait_for_input(self, task_id: str) -> dict[str, Any]:
        """Wait for the next input response or cancellation request."""
        queue = self._input_queues.setdefault(task_id, asyncio.Queue())
        cancel_event = self._cancel_events.setdefault(task_id, asyncio.Event())
        input_wait = asyncio.create_task(queue.get())
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait({input_wait, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        for waiter in pending:
            waiter.cancel()
        if cancel_wait in done:
            raise asyncio.CancelledError
        return input_wait.result()

    async def update(
        self,
        task_id: str,
        owner_id: str | None,
        input_responses: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Submit client responses to a task waiting for input."""
        should_resume = False
        resumed_responses: dict[str, Any] | None = None
        async with self._lock:
            record = await self._lookup(task_id, owner_id)
            if record.status != "input_required":
                return None
            outstanding = record.input_requests or {}
            accepted = {key: value for key, value in input_responses.items() if key in outstanding}
            if not accepted:
                return None
            current_responses = dict(record.input_responses or {})
            current_responses.update(accepted)
            record.input_responses = current_responses
            remaining = {key: value for key, value in outstanding.items() if key not in accepted}
            record.last_updated_at = _utc_now()
            if remaining:
                record.input_requests = remaining
                record.status_message = "Additional client input is required."
            else:
                record.status = "working"
                record.input_requests = None
                record.input_responses = None
                record.status_message = "Client input received; processing resumed."
                should_resume = True
                resumed_responses = dict(current_responses)
            await self._save(record)
        await self._notify(record)
        if should_resume and task_id in self._input_queues:
            await self._input_queues[task_id].put(resumed_responses or {})
        return resumed_responses

    async def cancel(self, task_id: str, owner_id: str | None) -> None:
        """Request cooperative cancellation and acknowledge immediately."""
        record = await self.get(task_id, owner_id)
        if record.is_terminal():
            return
        self._cancel_events.setdefault(task_id, asyncio.Event()).set()
        runner = self._runners.get(task_id)
        if runner is not None:
            runner.cancel()

    async def mark_cancelled(self, task_id: str) -> TaskRecord:
        """Persist cancellation after the runner cooperates."""
        return await self._transition(task_id, "cancelled", status_message="The task was cancelled.")

    async def close(self) -> None:
        """Cancel all process-local runners during graceful shutdown."""
        runners = list(self._runners.values())
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    async def _transition(
        self,
        task_id: str,
        status: str,
        *,
        status_message: str | None = None,
        input_requests: dict[str, dict[str, Any]] | None = None,
        request_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: JSONRPCError | None = None,
    ) -> TaskRecord:
        async with self._lock:
            record = await self._lookup(task_id, None)
            if record.is_terminal():
                return record
            record.status = status
            record.last_updated_at = _utc_now()
            record.status_message = status_message
            record.input_requests = input_requests
            record.request_state = request_state
            record.result = result
            record.error = error
            record.input_responses = None
            await self._save(record)
        await self._notify(record)
        return record

    async def _lookup(self, task_id: str, owner_id: str | None) -> TaskRecord:
        value = await self.store.get(self._key(task_id))
        if value is None:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        record = _decode_record(value)
        if owner_id is not None and record.owner_id is not None and record.owner_id != owner_id:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        return record

    async def _save(self, record: TaskRecord) -> None:
        expires_in = None if record.ttl_ms is None else max(1, (record.ttl_ms + 999) // 1000)
        await self.store.set(self._key(record.task_id), _encode_record(record), expires_in=expires_in)

    async def _notify(self, record: TaskRecord) -> None:
        if self.status_callback is not None:
            await self.status_callback(record)

    def _resolve_ttl(self, ttl_ms: int | None) -> int | None:
        if ttl_ms is None:
            return self.default_ttl_ms
        if ttl_ms < 0:
            msg = "ttlMs must be non-negative or null"
            raise TaskStateError(msg)
        return min(ttl_ms, self.max_ttl_ms)

    @staticmethod
    def _key(task_id: str) -> str:
        return f"mcp-task:{task_id}"


def _encode_record(record: TaskRecord) -> bytes:
    payload = record.to_dict()
    payload["ownerId"] = record.owner_id
    payload["requestStateInternal"] = record.request_state
    payload["dispatchInternal"] = record.dispatch
    payload["inputResponsesInternal"] = record.input_responses
    return encode_json(payload)


def _decode_record(value: bytes) -> TaskRecord:
    payload = decode_json(value)
    error_payload = payload.get("error")
    return TaskRecord(
        task_id=payload["taskId"],
        owner_id=payload.get("ownerId"),
        status=payload["status"],
        created_at=_parse_datetime(payload["createdAt"]),
        last_updated_at=_parse_datetime(payload["lastUpdatedAt"]),
        ttl_ms=payload.get("ttlMs"),
        poll_interval_ms=payload.get("pollIntervalMs"),
        status_message=payload.get("statusMessage"),
        input_requests=payload.get("inputRequests"),
        request_state=payload.get("requestStateInternal"),
        result=payload.get("result"),
        error=(
            JSONRPCError(
                code=error_payload["code"],
                message=error_payload["message"],
                data=error_payload.get("data"),
            )
            if error_payload is not None
            else None
        ),
        dispatch=payload.get("dispatchInternal"),
        input_responses=payload.get("inputResponsesInternal"),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class _FallbackCredentialRestrictions:
    """Fallback credential restrictions value object when litestar_security is not installed."""

    scopes: frozenset[str] | None = None
    tenant_ids: frozenset[str] | None = None
    roles: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class _FallbackAuthorizationSnapshot:
    """Fallback authorization snapshot value object when litestar_security is not installed."""

    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    attributes: Mapping[str, Any] = dataclasses.field(default_factory=lambda: MappingProxyType({}))
    tenant_roles: Mapping[str, frozenset[str]] = dataclasses.field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class _FallbackPrincipal:
    """Fallback principal wrapper value object when litestar_security is not installed."""

    user: Any
    account_id: str
    subject: str
    mechanisms: tuple[str, ...] = ()
    restrictions: tuple[Any, ...] = ()
    attributes: Mapping[str, Any] = dataclasses.field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class _FallbackSecurityContext:
    """Fallback security context value object when litestar_security is not installed."""

    principal: Any = None
    authorization: Any = dataclasses.field(default_factory=_FallbackAuthorizationSnapshot)
    restrictions: tuple[Any, ...] = ()


def _to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_json_safe(item) for item in value]
    converted: Any = None
    with contextlib.suppress(TypeError, ValueError, msgspec.EncodeError, msgspec.ValidationError):
        converted = msgspec.to_builtins(value, str_keys=True)
    if isinstance(converted, (dict, list, bool, int, float, str)):
        return converted
    extracted: dict[str, Any] = {}
    for attr in _PRINCIPAL_ATTR_KEYS:
        attr_val = getattr(value, attr, None)
        if attr_val is not None and not callable(attr_val):
            extracted[attr] = _to_json_safe(attr_val)
    return extracted or str(value)


def _is_principal_wrapper(obj: Any) -> bool:
    if obj is None or isinstance(obj, Mapping):
        return False
    cls_name = type(obj).__name__
    return cls_name in {"Principal", "_FallbackPrincipal"} and hasattr(obj, "user") and hasattr(obj, "account_id")


def _is_security_context_like(obj: Any) -> bool:
    if obj is None or isinstance(obj, Mapping):
        return False
    return hasattr(obj, "authorization") and hasattr(obj, "restrictions")


def _is_auth_snapshot_like(obj: Any) -> bool:
    if obj is None or isinstance(obj, Mapping):
        return False
    return hasattr(obj, "roles") and hasattr(obj, "scopes") and hasattr(obj, "tenant_roles")


def _type_tag(obj: Any) -> str | None:
    if obj is None or isinstance(obj, (Mapping, PrincipalProxy)) or _is_principal_wrapper(obj):
        return None
    cls = type(obj)
    if issubclass(cls, msgspec.Struct) or dataclasses.is_dataclass(cls):
        return f"{cls.__module__}:{cls.__qualname__}"
    return None


def _resolve_loaded_type(tag: str | None) -> type[Any] | None:
    if not tag or ":" not in tag:
        return None
    mod_name, qualname = tag.split(":", 1)
    if "." in qualname:
        return None
    mod = sys.modules.get(mod_name)
    if mod is None:
        return None
    cls = getattr(mod, qualname, None)
    if isinstance(cls, type) and (issubclass(cls, msgspec.Struct) or dataclasses.is_dataclass(cls)):
        return cls
    return None


def _serialize_restrictions(restrictions: Any) -> list[dict[str, Any]]:
    if not isinstance(restrictions, (list, tuple, set, frozenset)):
        return []
    result: list[dict[str, Any]] = []
    for item in restrictions:
        scopes = getattr(item, "scopes", None)
        tenant_ids = getattr(item, "tenant_ids", None)
        roles = getattr(item, "roles", None)
        result.append(
            {
                "scopes": sorted(str(s) for s in scopes) if scopes is not None else None,
                "tenant_ids": sorted(str(t) for t in tenant_ids) if tenant_ids is not None else None,
                "roles": sorted(str(r) for r in roles) if roles is not None else None,
            }
        )
    return result


def _serialize_auth_snapshot(auth_obj: Any) -> dict[str, Any] | None:
    if auth_obj is None:
        return None
    target = getattr(auth_obj, "authorization", auth_obj) if _is_security_context_like(auth_obj) else auth_obj
    if not _is_auth_snapshot_like(target):
        return None
    raw_tenant_roles = getattr(target, "tenant_roles", None) or {}
    tenant_roles = {
        str(k): sorted(str(r) for r in v)
        for k, v in raw_tenant_roles.items()
        if isinstance(v, (set, frozenset, list, tuple))
    }
    raw_attrs = getattr(target, "attributes", None) or {}
    return {
        "roles": sorted(str(r) for r in (getattr(target, "roles", None) or ())),
        "permissions": sorted(str(p) for p in (getattr(target, "permissions", None) or ())),
        "scopes": sorted(str(s) for s in (getattr(target, "scopes", None) or ())),
        "attributes": _to_json_safe(dict(raw_attrs)) if isinstance(raw_attrs, Mapping) else {},
        "tenant_roles": tenant_roles,
    }


def extract_principal_snapshot(
    user: Any,
    auth: Any = None,
    security_context: Any = None,
    owner_id: str | None = None,
) -> dict[str, Any] | None:
    """Capture a JSON-serializable snapshot of the authenticated request principal and security context."""
    if user is None and auth is None and security_context is None and owner_id is None:
        return None

    principal_wrapper = user if _is_principal_wrapper(user) else None
    sec_ctx = (
        security_context
        if _is_security_context_like(security_context)
        else (auth if _is_security_context_like(auth) else None)
    )
    if principal_wrapper is None and sec_ctx is not None:
        candidate_principal = getattr(sec_ctx, "principal", None)
        if _is_principal_wrapper(candidate_principal):
            principal_wrapper = candidate_principal

    inner_user = getattr(principal_wrapper, "user", user) if principal_wrapper is not None else user
    snapshot: dict[str, Any] = {}
    if inner_user is not None:
        converted_user = _to_json_safe(inner_user)
        if isinstance(converted_user, dict):
            snapshot.update(converted_user)
        for attr in _PRINCIPAL_ATTR_KEYS:
            if attr not in snapshot:
                attr_val = getattr(inner_user, attr, None)
                if attr_val is not None and not callable(attr_val):
                    snapshot[attr] = _to_json_safe(attr_val)
        tag = _type_tag(inner_user)
        if tag is not None:
            snapshot["__user_type__"] = tag

    if owner_id is not None:
        raw_sub = owner_id.removeprefix("user:")
        snapshot.setdefault("id", raw_sub)
        snapshot.setdefault("sub", raw_sub)

    auth_snap = _serialize_auth_snapshot(sec_ctx or auth)
    if principal_wrapper is not None or sec_ctx is not None or auth_snap is not None:
        raw_restrictions = (
            getattr(sec_ctx, "restrictions", None)
            if sec_ctx is not None
            else getattr(principal_wrapper, "restrictions", ())
        )
        sec_payload: dict[str, Any] = {
            "has_principal": principal_wrapper is not None,
            "authorization": auth_snap,
            "restrictions": _serialize_restrictions(raw_restrictions),
        }
        if principal_wrapper is not None:
            raw_p_attrs = getattr(principal_wrapper, "attributes", None) or {}
            sec_payload["principal"] = {
                "account_id": str(getattr(principal_wrapper, "account_id", snapshot.get("id", ""))),
                "subject": str(getattr(principal_wrapper, "subject", snapshot.get("sub", ""))),
                "mechanisms": [str(m) for m in (getattr(principal_wrapper, "mechanisms", None) or ())],
                "attributes": _to_json_safe(dict(raw_p_attrs)) if isinstance(raw_p_attrs, Mapping) else {},
                "restrictions": _serialize_restrictions(getattr(principal_wrapper, "restrictions", ())),
            }
        snapshot["__litestar_security__"] = sec_payload
    elif auth is not None:
        snapshot["__auth__"] = _to_json_safe(auth)

    return snapshot or None


def _load_security_context_classes() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    mod = sys.modules.get("litestar_security.context")
    if mod is None:
        with contextlib.suppress(ImportError):
            mod = importlib.import_module("litestar_security.context")
    if mod is not None:
        principal_cls = getattr(mod, "Principal", _FallbackPrincipal)
        auth_snap_cls = getattr(mod, "AuthorizationSnapshot", _FallbackAuthorizationSnapshot)
        restriction_cls = getattr(mod, "CredentialRestrictions", _FallbackCredentialRestrictions)
        sec_ctx_cls = getattr(mod, "SecurityContext", _FallbackSecurityContext)
        return principal_cls, auth_snap_cls, restriction_cls, sec_ctx_cls
    return (
        _FallbackPrincipal,
        _FallbackAuthorizationSnapshot,
        _FallbackCredentialRestrictions,
        _FallbackSecurityContext,
    )


def _reconstruct_restrictions(raw_list: Any, restriction_cls: type[Any]) -> tuple[Any, ...]:
    if not isinstance(raw_list, list):
        return ()
    items: list[Any] = []
    for entry in raw_list:
        if not isinstance(entry, Mapping):
            continue
        raw_scopes = entry.get("scopes")
        raw_tenants = entry.get("tenant_ids")
        raw_roles = entry.get("roles")
        items.append(
            restriction_cls(
                scopes=frozenset(str(s) for s in raw_scopes) if isinstance(raw_scopes, list) else None,
                tenant_ids=frozenset(str(t) for t in raw_tenants) if isinstance(raw_tenants, list) else None,
                roles=frozenset(str(r) for r in raw_roles) if isinstance(raw_roles, list) else None,
            )
        )
    return tuple(items)


def rehydrate_security_scope(
    scope: dict[str, Any],
    snapshot: dict[str, Any] | None,
    owner_id: str | None = None,
) -> Any:
    """Rehydrate ``scope["user"]``, ``scope["auth"]``, and ``scope["litestar_security"]`` from a task snapshot."""
    if snapshot is None and owner_id is None:
        return None

    user_fields = {k: v for k, v in (snapshot or {}).items() if not k.startswith("__")}
    if not user_fields and owner_id is not None:
        raw_sub = owner_id.removeprefix("user:")
        user_fields = {"id": raw_sub, "sub": raw_sub}

    rehydrated_user: Any = PrincipalProxy(user_fields) if user_fields else None
    user_type_tag = (snapshot or {}).get("__user_type__")
    loaded_cls = _resolve_loaded_type(user_type_tag if isinstance(user_type_tag, str) else None)
    if loaded_cls is not None and user_fields:
        with contextlib.suppress(Exception):
            rehydrated_user = msgspec.convert(user_fields, loaded_cls, strict=False)

    sec_meta = (snapshot or {}).get("__litestar_security__")
    if isinstance(sec_meta, Mapping):
        principal_cls, auth_snap_cls, restriction_cls, sec_ctx_cls = _load_security_context_classes()
        ctx_restrictions = _reconstruct_restrictions(sec_meta.get("restrictions"), restriction_cls)
        raw_auth = sec_meta.get("authorization")
        if isinstance(raw_auth, Mapping):
            raw_tenant_roles = raw_auth.get("tenant_roles")
            tenant_roles = (
                MappingProxyType(
                    {str(k): frozenset(str(r) for r in v) for k, v in raw_tenant_roles.items() if isinstance(v, list)}
                )
                if isinstance(raw_tenant_roles, Mapping)
                else MappingProxyType({})
            )
            raw_attrs = raw_auth.get("attributes")
            auth_snapshot = auth_snap_cls(
                roles=frozenset(str(r) for r in (raw_auth.get("roles") or ())),
                permissions=frozenset(str(p) for p in (raw_auth.get("permissions") or ())),
                scopes=frozenset(str(s) for s in (raw_auth.get("scopes") or ())),
                attributes=MappingProxyType(dict(raw_attrs) if isinstance(raw_attrs, Mapping) else {}),
                tenant_roles=tenant_roles,
            )
        else:
            auth_snapshot = auth_snap_cls()

        principal_obj: Any = None
        raw_principal = sec_meta.get("principal")
        if isinstance(raw_principal, Mapping):
            p_restrictions = _reconstruct_restrictions(raw_principal.get("restrictions"), restriction_cls)
            p_attrs = raw_principal.get("attributes")
            principal_obj = principal_cls(
                user=rehydrated_user,
                account_id=str(raw_principal.get("account_id", "")),
                subject=str(raw_principal.get("subject", "")),
                mechanisms=tuple(str(m) for m in (raw_principal.get("mechanisms") or ())),
                restrictions=p_restrictions,
                attributes=MappingProxyType(dict(p_attrs) if isinstance(p_attrs, Mapping) else {}),
            )

        security_ctx = sec_ctx_cls(
            principal=principal_obj,
            authorization=auth_snapshot,
            restrictions=ctx_restrictions,
        )
        resolved_scope_user = principal_obj if principal_obj is not None else rehydrated_user
        if resolved_scope_user is not None:
            scope["user"] = resolved_scope_user
        scope["auth"] = security_ctx
        scope["litestar_security"] = security_ctx
        return resolved_scope_user

    if rehydrated_user is not None:
        scope["user"] = rehydrated_user
    if snapshot is not None and "__auth__" in snapshot:
        scope["auth"] = _wrap_proxy_value(snapshot["__auth__"])
    return rehydrated_user
