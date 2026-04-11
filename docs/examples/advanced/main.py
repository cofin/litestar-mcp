"""Advanced Litestar MCP Plugin Example - SQLite-backed Task Management API.

Modeled after ``advanced-alchemy``'s official Litestar service example
(``examples/litestar/litestar_service.py`` in the advanced-alchemy repo) and
layered with the Litestar MCP plugin so the same CRUD surface is exposed as
MCP tools and resources.

Highlights:

- Uses ``providers.create_service_dependencies`` — the idiomatic AA path —
  so a per-request ``TaskService`` (and its filter dependencies) is injected
  into both controllers and MCP tool calls.
- ``service.to_schema(...)`` converts ORM records to the public schema; no
  hand-rolled ``to_task`` helper.
- ``error_messages={"not_found": ...}`` replaces the usual ``try/except``
  ceiling around ``NotFoundError``.
- Schemas use Pydantic ``BaseModel`` — matching the upstream AA example.
  Litestar auto-discovers ``PydanticPlugin`` when ``pydantic`` is installed,
  so its type encoders land on ``app.type_encoders`` and the MCP result
  encoder picks them up automatically.
- ``SystemController`` exposes cheap, cacheable MCP resources alongside
  regular health / root endpoints; ``TaskController`` owns task CRUD.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from advanced_alchemy.extensions.litestar import (
    AsyncSessionConfig,
    SQLAlchemyAsyncConfig,
    SQLAlchemyPlugin,
    base,
    filters,
    providers,
    repository,
    service,
)
from advanced_alchemy.extensions.litestar.providers import FieldNameType
from litestar import Controller, Litestar, delete, get, post
from litestar.openapi.config import OpenAPIConfig
from litestar.params import Dependency
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED
from pydantic import BaseModel, Field
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from litestar_mcp import LitestarMCP, MCPConfig

DEFAULT_DATABASE_PATH = Path(__file__).with_name("db.sqlite3")
DATABASE_PATH = Path(os.environ.get("LITESTAR_MCP_ADVANCED_DB", str(DEFAULT_DATABASE_PATH)))


# ── Public schemas (Pydantic — same shape as the upstream AA example)
class Task(BaseModel):
    """A task in the task management system."""

    model_config = {"title": "Task"}

    id: int = Field(description="Unique task identifier")
    title: str = Field(description="Task title", max_length=200)
    description: str = Field(description="Task description", max_length=500)
    completed: bool = Field(default=False, description="Task completion status")


class CreateTask(BaseModel):
    """Payload for creating a task."""

    title: str = Field(description="Task title", max_length=200)
    description: str = Field(description="Task description", max_length=500)


class UpdateTask(BaseModel):
    """Partial update payload for a task."""

    title: str | None = Field(default=None, description="Task title", max_length=200)
    description: str | None = Field(default=None, description="Task description", max_length=500)
    completed: bool | None = Field(default=None, description="Task completion status")


INITIAL_TASKS: list[dict[str, Any]] = [
    {"title": "Learn Litestar", "description": "Study the Litestar framework", "completed": True},
    {"title": "Integrate MCP", "description": "Add MCP support to my application", "completed": False},
    {"title": "Build API", "description": "Create a REST API for task management", "completed": False},
]


# ── Database model
class TaskRecord(base.BigIntAuditBase):
    """SQLite task model stored via advanced-alchemy."""

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500))
    completed: Mapped[bool] = mapped_column(default=False)


# ── Repository service (mirrors the AA ``AuthorService`` pattern)
class TaskService(service.SQLAlchemyAsyncRepositoryService[TaskRecord]):
    """Repository service for tasks."""

    class Repo(repository.SQLAlchemyAsyncRepository[TaskRecord]):
        """Repository for task persistence."""

        model_type = TaskRecord

    repository_type = Repo


# ── Controllers
class TaskController(Controller):
    """Task CRUD — every endpoint is also exposed as an MCP tool."""

    path = "/tasks"
    dependencies = providers.create_service_dependencies(
        TaskService,
        "task_service",
        filters={
            "pagination_type": "limit_offset",
            "id_filter": int,
            "search": "title",
            "search_ignore_case": True,
            "in_fields": {FieldNameType("completed", bool)},
        },
    )

    @get("/schema", opt={"mcp_resource": "task_schema"})
    async def get_task_schema(self) -> dict[str, Any]:
        """Return the task data model JSON Schema — exposed as MCP resource.

        Generated directly from the Pydantic model so the schema can never
        drift from the code.
        """
        return Task.model_json_schema()

    @get("/", opt={"mcp_tool": "list_tasks"})
    async def list_tasks(
        self,
        task_service: TaskService,
        filters: Annotated[list[filters.FilterTypes], Dependency(skip_validation=True)],
    ) -> service.OffsetPagination[Task]:
        """List tasks — exposed as MCP tool."""
        results, total = await task_service.list_and_count(*filters)
        return task_service.to_schema(results, total, filters=filters, schema_type=Task)

    @get("/{task_id:int}", opt={"mcp_tool": "get_task"})
    async def get_task(self, task_service: TaskService, task_id: int) -> Task:
        """Get a specific task by ID — exposed as MCP tool."""
        obj = await task_service.get(
            task_id,
            error_messages={"not_found": f"Task {task_id} not found"},
        )
        return task_service.to_schema(obj, schema_type=Task)

    @post("/", status_code=HTTP_201_CREATED, opt={"mcp_tool": "create_task"})
    async def create_task(self, task_service: TaskService, data: CreateTask) -> Task:
        """Create a new task — exposed as MCP tool."""
        obj = await task_service.create(data)
        return task_service.to_schema(obj, schema_type=Task)

    @post("/{task_id:int}/complete", opt={"mcp_tool": "complete_task"})
    async def complete_task(self, task_service: TaskService, task_id: int) -> Task:
        """Mark a task as completed — exposed as MCP tool."""
        obj = await task_service.update(
            UpdateTask(completed=True),
            item_id=task_id,
            error_messages={"not_found": f"Task {task_id} not found"},
        )
        return task_service.to_schema(obj, schema_type=Task)

    @delete("/{task_id:int}", status_code=HTTP_200_OK, opt={"mcp_tool": "delete_task"})
    async def delete_task(self, task_service: TaskService, task_id: int) -> dict[str, str]:
        """Delete a task by ID — exposed as MCP tool."""
        await task_service.delete(
            task_id,
            error_messages={"not_found": f"Task {task_id} not found"},
        )
        return {"message": f"Task {task_id} deleted successfully"}


class SystemController(Controller):
    """Non-task endpoints: root, health, and the cacheable ``api_info`` resource."""

    @get("/")
    async def root(self) -> dict[str, str]:
        """Root endpoint — not exposed to MCP."""
        return {"message": "Welcome to the SQLite-backed Task Management API with MCP integration!"}

    @get("/api/info", opt={"mcp_resource": "api_info"})
    async def get_api_info(self) -> dict[str, Any]:
        """Return static API metadata — exposed as MCP resource.

        Resources should be cheap and cacheable, so this intentionally avoids
        live database lookups. Live counts belong on ``/health`` and ``/tasks``.
        """
        return {
            "name": "Task Management API",
            "version": "1.0.0",
            "description": "SQLite-backed task management system with MCP integration",
            "features": ["task_creation", "task_listing", "task_completion", "task_deletion"],
            "endpoints_count": 5,
            "mcp_integration": True,
            "storage_backend": "sqlite",
        }

    @get("/health")
    async def health_check(self) -> dict[str, Any]:
        """Health check endpoint — not exposed to MCP."""
        return {"status": "healthy", "storage_backend": "sqlite"}


# ── Database configuration + seeding
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
alchemy_config = SQLAlchemyAsyncConfig(
    connection_string=f"sqlite+aiosqlite:///{DATABASE_PATH}",
    session_config=AsyncSessionConfig(expire_on_commit=False),
    create_all=True,
    before_send_handler="autocommit",
)


async def seed_initial_tasks(app: Litestar) -> None:
    """Populate the demo database on first startup.

    ``TaskService.new(config=...)`` builds its own session from the config
    and tears it down on exit — the same idiom used in advanced-alchemy's
    official examples.
    """
    async with TaskService.new(config=alchemy_config) as task_service:
        if await task_service.count() == 0:
            await task_service.create_many(INITIAL_TASKS, auto_commit=True)


# ── Configuration
app = Litestar(
    route_handlers=[TaskController, SystemController],
    plugins=[
        SQLAlchemyPlugin(config=alchemy_config),
        LitestarMCP(
            MCPConfig(
                name="Task Management API",
                base_path="/mcp",
                include_in_schema=False,
            )
        ),
    ],
    on_startup=[seed_initial_tasks],
    openapi_config=OpenAPIConfig(
        title="Task Management API",
        version="1.0.0",
        description="A SQLite-backed task management system with MCP integration",
    ),
)

