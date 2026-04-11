"""Advanced Litestar MCP Plugin Example - SQLite-backed Task Management API.

This example demonstrates a more comprehensive use of the Litestar MCP Plugin
with a task management API backed by SQLite via advanced-alchemy.

Features:
- Multiple MCP tools for task operations
- MCP resources for schema and API information
- SQLite persistence via advanced-alchemy
- Dependency injection for both MCP tools and resources
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from advanced_alchemy.exceptions import NotFoundError
from advanced_alchemy.extensions.litestar import (
    SQLAlchemyAsyncConfig,
    base,
    repository,
    service,
)
from litestar import Litestar, delete, get, post
from litestar.di import Provide
from litestar.openapi.config import OpenAPIConfig
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED, HTTP_404_NOT_FOUND
from pydantic import BaseModel
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from litestar_mcp import LitestarMCP, MCPConfig

DEFAULT_DATABASE_PATH = Path(__file__).with_name("db.sqlite3")
DATABASE_PATH = Path(os.environ.get("LITESTAR_MCP_ADVANCED_DB", str(DEFAULT_DATABASE_PATH)))


# Pydantic models
class Task(BaseModel):
    id: int
    title: str
    description: str
    completed: bool = False


class CreateTaskRequest(BaseModel):
    title: str
    description: str


INITIAL_TASKS: list[dict[str, Any]] = [
    {"title": "Learn Litestar", "description": "Study the Litestar framework", "completed": True},
    {"title": "Integrate MCP", "description": "Add MCP support to my application", "completed": False},
    {"title": "Build API", "description": "Create a REST API for task management", "completed": False},
]


class TaskRecord(base.BigIntAuditBase):
    """SQLite task model stored via advanced-alchemy."""

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500))
    completed: Mapped[bool] = mapped_column(default=False)


class TaskRepositoryService(service.SQLAlchemyAsyncRepositoryService[TaskRecord]):
    """Service for task persistence operations."""

    class Repo(repository.SQLAlchemyAsyncRepository[TaskRecord]):
        """Repository for task persistence."""

        model_type = TaskRecord

    repository_type = Repo


def to_task(record: TaskRecord) -> Task:
    """Convert a database record to the API model."""
    return Task(
        id=int(record.id),
        title=record.title,
        description=record.description,
        completed=record.completed,
    )


alchemy_config = SQLAlchemyAsyncConfig(connection_string=f"sqlite+aiosqlite:///{DATABASE_PATH}")
session_maker = alchemy_config.create_session_maker()
database_ready = asyncio.Event()
database_lock = asyncio.Lock()


async def ensure_database_ready() -> None:
    """Create tables and seed the demo database once."""
    if database_ready.is_set():
        return

    async with database_lock:
        if database_ready.is_set():
            return

        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

        async with alchemy_config.get_engine().begin() as connection:
            await connection.run_sync(TaskRecord.metadata.create_all)

        async with session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            if await task_store.count() == 0:
                for seed_task in INITIAL_TASKS:
                    await task_store.create(seed_task)
                await session.commit()

        database_ready.set()


class TaskService:
    """MCP-safe dependency wrapper around advanced-alchemy session handling."""

    storage_backend = "sqlite"

    def __init__(self) -> None:
        self._session_maker = session_maker

    async def list_tasks(self, completed: bool | None = None) -> list[Task]:
        """Return all tasks, optionally filtered by completion status."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            records = await task_store.list(completed=completed) if completed is not None else await task_store.list()
            return [to_task(record) for record in records]

    async def count_tasks(self) -> int:
        """Return the number of stored tasks."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            return await task_store.count()

    async def get_task(self, task_id: int) -> Task:
        """Fetch a single task by identifier."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            record = await task_store.get(task_id)
            return to_task(record)

    async def create_task(self, data: CreateTaskRequest) -> Task:
        """Create and persist a new task."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            record = await task_store.create(data.model_dump())
            await session.commit()
            return to_task(record)

    async def complete_task(self, task_id: int) -> Task:
        """Mark an existing task as completed."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            record = await task_store.get(task_id)
            record.completed = True
            await session.commit()
            await session.refresh(record)
            return to_task(record)

    async def delete_task(self, task_id: int) -> None:
        """Delete an existing task."""
        async with self._session_maker() as session:
            task_store = TaskRepositoryService(session=session)
            await task_store.delete(task_id)
            await session.commit()


async def provide_task_service() -> TaskService:
    """Provide a shared SQLite-backed task service for MCP routes."""
    await ensure_database_ready()
    return TaskService()


task_service_dependency = {"task_service": Provide(provide_task_service)}


# MCP Resources - Read-only data for AI models
@get("/tasks/schema", mcp_resource="task_schema")
async def get_task_schema() -> dict[str, Any]:
    """Get the task data model schema - exposed as MCP resource."""
    return {
        "type": "object",
        "required": ["id", "title", "description"],
        "properties": {
            "id": {"type": "integer", "description": "Unique task identifier"},
            "title": {"type": "string", "description": "Task title"},
            "description": {"type": "string", "description": "Task description"},
            "completed": {"type": "boolean", "description": "Task completion status", "default": False},
        },
    }


@get(
    "/api/info",
    mcp_resource="api_info",
    dependencies=task_service_dependency,
)
async def get_api_info(task_service: TaskService) -> dict[str, Any]:
    """Get API information and capabilities using dependency injection."""
    return {
        "name": "Task Management API",
        "version": "1.0.0",
        "description": "SQLite-backed task management system with MCP integration",
        "features": ["task_creation", "task_listing", "task_completion", "task_deletion"],
        "endpoints_count": len(["/tasks", "/tasks/{task_id}", "/tasks/schema", "/api/info"]),
        "mcp_integration": True,
        "storage_backend": task_service.storage_backend,
        "tasks_count": await task_service.count_tasks(),
    }


# MCP Tools - Executable operations for AI models
@get(
    "/tasks",
    mcp_tool="list_tasks",
    dependencies=task_service_dependency,
)
async def list_tasks(task_service: TaskService, completed: bool | None = None) -> list[Task]:
    """List tasks with optional filtering using an injected SQLite service."""
    return await task_service.list_tasks(completed)


@get(
    "/tasks/{task_id:int}",
    mcp_tool="get_task",
    dependencies=task_service_dependency,
)
async def get_task(task_service: TaskService, task_id: int) -> Task:
    """Get a specific task by ID - exposed as MCP tool."""
    try:
        return await task_service.get_task(task_id)
    except NotFoundError:
        raise HTTP_404_NOT_FOUND from None


@post(
    "/tasks",
    status_code=HTTP_201_CREATED,
    mcp_tool="create_task",
    dependencies=task_service_dependency,
)
async def create_task(task_service: TaskService, data: CreateTaskRequest) -> Task:
    """Create a new task - exposed as MCP tool."""
    return await task_service.create_task(data)


@post(
    "/tasks/{task_id:int}/complete",
    mcp_tool="complete_task",
    dependencies=task_service_dependency,
)
async def complete_task(task_service: TaskService, task_id: int) -> Task:
    """Mark a task as completed - exposed as MCP tool."""
    try:
        return await task_service.complete_task(task_id)
    except NotFoundError:
        raise HTTP_404_NOT_FOUND from None


@delete(
    "/tasks/{task_id:int}",
    status_code=HTTP_200_OK,
    mcp_tool="delete_task",
    dependencies=task_service_dependency,
)
async def delete_task(task_service: TaskService, task_id: int) -> dict[str, str]:
    """Delete a task by ID - exposed as MCP tool."""
    try:
        await task_service.delete_task(task_id)
    except NotFoundError:
        raise HTTP_404_NOT_FOUND from None
    return {"message": f"Task {task_id} deleted successfully"}


# Regular API endpoints (not exposed to MCP)
@get("/")
async def root() -> dict[str, str]:
    """Root endpoint - not exposed to MCP."""
    return {"message": "Welcome to the SQLite-backed Task Management API with MCP integration!"}


@get("/health", dependencies=task_service_dependency)
async def health_check(task_service: TaskService) -> dict[str, Any]:
    """Health check endpoint - not exposed to MCP."""
    return {
        "status": "healthy",
        "storage_backend": task_service.storage_backend,
        "tasks_count": await task_service.count_tasks(),
    }


# MCP Configuration
mcp_config = MCPConfig(
    name="Task Management API",
    base_path="/mcp",
    include_in_schema=False,  # Keep MCP endpoints out of main API docs
)

# Create Litestar application
app = Litestar(
    route_handlers=[
        # MCP Resources
        get_task_schema,
        get_api_info,
        # MCP Tools
        list_tasks,
        get_task,
        create_task,
        complete_task,
        delete_task,
        # Regular endpoints
        root,
        health_check,
    ],
    plugins=[LitestarMCP(mcp_config)],
    openapi_config=OpenAPIConfig(
        title="Task Management API",
        version="1.0.0",
        description="A SQLite-backed task management system with MCP integration",
    ),
)

if __name__ == "__main__":
    import logging

    import uvicorn

    logger = logging.getLogger(__name__)

    logger.info("🚀 Starting SQLite-backed Task Management API with MCP integration...")
    logger.info("📊 API Documentation: http://127.0.0.1:8000/schema/swagger")
    logger.info("🔧 MCP Server Info: http://127.0.0.1:8000/mcp/")
    logger.info("📋 MCP Resources: http://127.0.0.1:8000/mcp/resources")
    logger.info("🛠️ MCP Tools: http://127.0.0.1:8000/mcp/tools")

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
