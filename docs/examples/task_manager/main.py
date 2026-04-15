"""Task Manager Litestar MCP Plugin Example.

Demonstrates a broader use of the Litestar MCP Plugin with a small task
management API that exposes multiple endpoints as MCP tools and resources.

Features:

- Multiple MCP tools for task operations.
- MCP resources for schema and API information.
- Mix of GET and POST endpoints.
- Demonstrates both tools and resources.
"""

import copy
from typing import Any

from litestar import Litestar, delete, get, post
from litestar.exceptions import NotFoundException
from litestar.openapi.config import OpenAPIConfig
from litestar.status_codes import HTTP_201_CREATED
from pydantic import BaseModel

from litestar_mcp import LitestarMCP, MCPConfig


class Task(BaseModel):
    id: int
    title: str
    description: str
    completed: bool = False


class CreateTaskRequest(BaseModel):
    title: str
    description: str


DEFAULT_TASKS: "dict[int, Task]" = {
    1: Task(id=1, title="Learn Litestar", description="Study the Litestar framework", completed=True),
    2: Task(id=2, title="Integrate MCP", description="Add MCP support to my application", completed=False),
    3: Task(id=3, title="Build API", description="Create a REST API for task management", completed=False),
}


def register_resources(store: "dict[int, Task]") -> "list[Any]":
    """Return read-only MCP resource handlers bound to ``store``."""

    # start-example
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

    @get("/api/info", mcp_resource="api_info")
    async def get_api_info() -> dict[str, Any]:
        """Get API information and capabilities - exposed as MCP resource."""
        return {
            "name": "Task Management API",
            "version": "1.0.0",
            "description": "Simple task management system with MCP integration",
            "features": ["task_creation", "task_listing", "task_completion", "task_deletion"],
            "endpoints_count": len(["/tasks", "/tasks/{task_id}", "/tasks/schema", "/api/info"]),
            "mcp_integration": True,
        }

    # end-example
    return [get_task_schema, get_api_info]


def register_tools(store: "dict[int, Task]") -> "list[Any]":
    """Return MCP tool handlers (task CRUD) bound to ``store``."""

    # start-example
    @get("/tasks", mcp_tool="list_tasks")
    async def list_tasks(completed: "bool | None" = None) -> "list[Task]":
        """List all tasks, optionally filtered by completion status."""
        if completed is None:
            return list(store.values())
        return [task for task in store.values() if task.completed == completed]

    @get("/tasks/{task_id:int}", mcp_tool="get_task")
    async def get_task(task_id: int) -> Task:
        """Get a specific task by ID."""
        if task_id not in store:
            raise NotFoundException(detail=f"Task {task_id} not found")
        return store[task_id]

    @post("/tasks", status_code=HTTP_201_CREATED, mcp_tool="create_task")
    async def create_task(data: CreateTaskRequest) -> Task:
        """Create a new task."""
        new_id = max(store.keys(), default=0) + 1
        new_task = Task(id=new_id, title=data.title, description=data.description, completed=False)
        store[new_id] = new_task
        return new_task

    @post("/tasks/{task_id:int}/complete", mcp_tool="complete_task")
    async def complete_task(task_id: int) -> Task:
        """Mark a task as completed."""
        if task_id not in store:
            raise NotFoundException(detail=f"Task {task_id} not found")
        store[task_id].completed = True
        return store[task_id]

    @delete("/tasks/{task_id:int}", mcp_tool="delete_task")
    async def delete_task(task_id: int) -> None:
        """Delete a task by ID."""
        if task_id not in store:
            raise NotFoundException(detail=f"Task {task_id} not found")
        del store[task_id]

    # end-example
    return [list_tasks, get_task, create_task, complete_task, delete_task]


@get("/")
async def root() -> dict[str, str]:
    """Root endpoint - not exposed to MCP."""
    return {"message": "Welcome to the Task Management API with MCP integration!"}


def build_app(tasks: "dict[int, Task] | None" = None) -> Litestar:
    """Construct the task-manager Litestar app.

    ``tasks`` seeds the in-memory store. It is deep-copied so tests can pass a
    fresh dict per call without leaking state between runs.
    """
    seed = DEFAULT_TASKS if tasks is None else tasks
    store: dict[int, Task] = copy.deepcopy(seed)

    @get("/health")
    async def health_check() -> "dict[str, str | int]":
        """Health check endpoint - not exposed to MCP."""
        return {"status": "healthy", "tasks_count": len(store)}

    resource_handlers = register_resources(store)
    tool_handlers = register_tools(store)

    # start-example
    mcp_config = MCPConfig(
        name="Task Management API",
        base_path="/mcp",
        include_in_schema=False,
    )
    app = Litestar(
        route_handlers=[
            *resource_handlers,
            *tool_handlers,
            root,
            health_check,
        ],
        plugins=[LitestarMCP(mcp_config)],
        openapi_config=OpenAPIConfig(
            title="Task Management API",
            version="1.0.0",
            description="A simple task management system with MCP integration",
        ),
    )
    # end-example
    return app


app = build_app()


if __name__ == "__main__":
    import logging

    import uvicorn

    logger = logging.getLogger(__name__)

    logger.info("Starting Task Management API with MCP integration...")
    logger.info("API Documentation: http://127.0.0.1:8000/schema/swagger")
    logger.info("MCP Transport: http://127.0.0.1:8000/mcp")
    logger.info("MCP Server Manifest: http://127.0.0.1:8000/.well-known/mcp-server.json")
    logger.info("Agent Card: http://127.0.0.1:8000/.well-known/agent-card.json")

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
