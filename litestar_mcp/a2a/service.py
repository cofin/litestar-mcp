"""A2A JSON-RPC 2.0 handler service."""

import inspect
import uuid
from typing import TYPE_CHECKING, Any, cast

from litestar.serialization import decode_json, encode_json

from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.registry import A2ARegistry
from litestar_mcp.a2a.tasks import A2ATaskStore
from litestar_mcp.a2a.types import (
    Artifact,
    DataPart,
    Task,
    TaskStatus,
    TextPart,
)
from litestar_mcp.shared.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRequest,
    JSONRPCRouter,
)
from litestar_mcp.shared.tasks import TaskLookupError

if TYPE_CHECKING:
    from litestar import Litestar


def _extract_arguments_from_message(raw_msg: Any) -> dict[str, Any]:
    """Extract keyword arguments from message payload parts."""
    kwargs: dict[str, Any] = {}
    if not isinstance(raw_msg, dict):
        return kwargs

    msg_parts: list[dict[str, Any]] = raw_msg.get("parts", [])
    for part in msg_parts:
        if isinstance(part, dict):
            p_type = part.get("type")
            if p_type == "data" and isinstance(part.get("data"), dict):
                kwargs.update(part["data"])
            elif p_type == "text" and "text" in part:
                kwargs.setdefault("query", part["text"])
                kwargs.setdefault("input", part["text"])
    return kwargs


def _convert_result_to_task(
    result: Any,
    task_id: str,
    session_id: str | None,
    emitted_artifacts: list[Artifact],
) -> Task:
    """Format raw callable return into a completed Task model."""
    if isinstance(result, Task):
        return result

    artifacts = list(emitted_artifacts)
    if isinstance(result, Artifact):
        artifacts.append(result)
    elif isinstance(result, dict):
        artifacts.append(Artifact(name="result", parts=[DataPart(data=result)]))
    elif isinstance(result, str):
        artifacts.append(Artifact(name="result", parts=[TextPart(text=result)]))
    elif result is not None:
        artifacts.append(Artifact(name="result", parts=[DataPart(data=result)]))

    return Task(
        id=task_id,
        session_id=session_id,
        status=TaskStatus(state="completed"),
        artifacts=artifacts,
    )


class A2AHandlerService:
    """Service processing standard A2A JSON-RPC 2.0 protocol methods."""

    def __init__(
        self,
        app: "Litestar",
        registry: A2ARegistry,
        task_store: A2ATaskStore,
    ) -> None:
        self.app = app
        self.registry = registry
        self.task_store = task_store
        self.router = JSONRPCRouter()
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.register("tasks/send", self.handle_tasks_send)
        self.router.register("tasks/get", self.handle_tasks_get)
        self.router.register("tasks/cancel", self.handle_tasks_cancel)

    async def dispatch_request(self, request: JSONRPCRequest, context: Any = None) -> dict[str, Any] | None:
        """Dispatch an incoming JSONRPCRequest to registered method handlers."""
        return await self.router.dispatch(request, context or {})

    async def _execute_callable(
        self,
        fn: Any,
        kwargs: dict[str, Any],
        task_ctx: TaskContext,
    ) -> Any:
        """Invoke synchronous or asynchronous skill callable with injected context."""
        unwrapped = getattr(fn, "fn", fn)
        sig = inspect.signature(unwrapped)

        call_kwargs: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("context", "task_context") or (
                param.annotation is not inspect.Parameter.empty and "TaskContext" in str(param.annotation)
            ):
                call_kwargs[param_name] = task_ctx
            elif param_name in kwargs:
                call_kwargs[param_name] = kwargs[param_name]

        if inspect.iscoroutinefunction(unwrapped):
            return await unwrapped(**call_kwargs)
        return unwrapped(**call_kwargs)

    async def handle_tasks_send(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Handle tasks/send invocation."""
        if not isinstance(params, dict):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Params must be a dictionary"))

        skill_id = params.get("skill") or params.get("skillId")
        if not skill_id or not isinstance(skill_id, str):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required 'skill' parameter"))

        skill_reg = self.registry.get(skill_id)
        if skill_reg is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message=f"Skill {skill_id!r} not found"))

        task_id = str(params.get("taskId") or params.get("id") or uuid.uuid4())
        session_id = params.get("sessionId")
        task = Task(
            id=task_id,
            session_id=session_id,
            status=TaskStatus(state="working", message="Processing skill execution"),
        )
        await self.task_store.save_task(task)

        task_ctx = TaskContext(task_id=task_id, session_id=session_id, task_store=self.task_store)
        kwargs = _extract_arguments_from_message(params.get("message"))

        try:
            result = await self._execute_callable(skill_reg.fn, kwargs, task_ctx)
            final_task = _convert_result_to_task(result, task_id, session_id, task_ctx.emitted_artifacts)
            await self.task_store.save_task(final_task)
            return cast("dict[str, Any]", decode_json(encode_json(final_task)))
        except Exception as exc:
            err_task = Task(
                id=task_id,
                session_id=session_id,
                status=TaskStatus(state="failed", message=str(exc)),
                artifacts=list(task_ctx.emitted_artifacts),
            )
            await self.task_store.save_task(err_task)
            raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message=str(exc))) from exc

    async def handle_tasks_get(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Handle tasks/get query."""
        if not isinstance(params, dict):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Params must be a dictionary"))

        task_id = params.get("id") or params.get("taskId")
        if not task_id or not isinstance(task_id, str):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required 'id' parameter"))

        try:
            task = await self.task_store.get_task(task_id)
            return cast("dict[str, Any]", decode_json(encode_json(task)))
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Task {task_id!r} not found")) from exc

    async def handle_tasks_cancel(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Handle tasks/cancel command."""
        if not isinstance(params, dict):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Params must be a dictionary"))

        task_id = params.get("id") or params.get("taskId")
        if not task_id or not isinstance(task_id, str):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required 'id' parameter"))

        try:
            updated_task = await self.task_store.update_task_status(task_id, "canceled", message="Canceled by client")
            return cast("dict[str, Any]", decode_json(encode_json(updated_task)))
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Task {task_id!r} not found")) from exc
