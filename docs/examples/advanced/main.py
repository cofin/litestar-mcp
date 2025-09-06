"""Advanced Litestar MCP Plugin Example - Task Management API.

This example demonstrates a more comprehensive use of the Litestar MCP Plugin
with a simple task management API that exposes various endpoints as MCP tools and resources.

Features:
- Multiple MCP tools for task operations
- MCP resources for schema and API information
- Mix of GET and POST endpoints
- Demonstrates both tools and resources
"""

from typing import Any

from litestar import Litestar, delete, get, post
from litestar.openapi.config import OpenAPIConfig
from litestar.status_codes import HTTP_201_CREATED, HTTP_404_NOT_FOUND
from pydantic import BaseModel

from litestar_mcp import LitestarMCP, MCPConfig


# Pydantic models
class Task(BaseModel):
    id: int
    title: str
    description: str
    completed: bool = False


class CreateTaskRequest(BaseModel):
    title: str
    description: str


# In-memory task storage (for demo purposes)
TASKS: dict[int, Task] = {
    1: Task(id=1, title="Learn Litestar", description="Study the Litestar framework", completed=True),
    2: Task(id=2, title="Integrate MCP", description="Add MCP support to my application", completed=False),
    3: Task(id=3, title="Build API", description="Create a REST API for task management", completed=False),
}
TASK_COUNTER = 3


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


# MCP Tools - Executable operations for AI models
@get("/tasks", mcp_tool="list_tasks")
async def list_tasks(completed: bool | None = None) -> list[Task]:
    """List all tasks with optional filtering by completion status - exposed as MCP tool."""
    if completed is None:
        return list(TASKS.values())

    return [task for task in TASKS.values() if task.completed == completed]


@get("/tasks/{task_id:int}", mcp_tool="get_task")
async def get_task(task_id: int) -> Task:
    """Get a specific task by ID - exposed as MCP tool."""
    if task_id not in TASKS:
        raise HTTP_404_NOT_FOUND
    return TASKS[task_id]


@post("/tasks", status_code=HTTP_201_CREATED, mcp_tool="create_task")
async def create_task(data: CreateTaskRequest) -> Task:
    """Create a new task - exposed as MCP tool."""
    global TASK_COUNTER
    TASK_COUNTER += 1

    new_task = Task(
        id=TASK_COUNTER,
        title=data.title,
        description=data.description,
        completed=False,
    )

    TASKS[TASK_COUNTER] = new_task
    return new_task


@post("/tasks/{task_id:int}/complete", mcp_tool="complete_task")
async def complete_task(task_id: int) -> Task:
    """Mark a task as completed - exposed as MCP tool."""
    if task_id not in TASKS:
        raise HTTP_404_NOT_FOUND

    TASKS[task_id].completed = True
    return TASKS[task_id]


@delete("/tasks/{task_id:int}", mcp_tool="delete_task")
async def delete_task(task_id: int) -> dict[str, str]:
    """Delete a task by ID - exposed as MCP tool."""
    if task_id not in TASKS:
        raise HTTP_404_NOT_FOUND

    del TASKS[task_id]
    return {"message": f"Task {task_id} deleted successfully"}


# Regular API endpoints (not exposed to MCP)
@get("/")
async def root() -> dict[str, str]:
    """Root endpoint - not exposed to MCP."""
    return {"message": "Welcome to the Task Management API with MCP integration!"}


@get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint - not exposed to MCP."""
    return {"status": "healthy", "tasks_count": len(TASKS)}


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
        description="A simple task management system with MCP integration",
    ),
)

if __name__ == "__main__":
    import uvicorn

    print("🚀 Starting Task Management API with MCP integration...")
    print("📊 API Documentation: http://127.0.0.1:8000/schema/swagger")
    print("🔧 MCP Server Info: http://127.0.0.1:8000/mcp/")
    print("📋 MCP Resources: http://127.0.0.1:8000/mcp/resources")
    print("🛠️ MCP Tools: http://127.0.0.1:8000/mcp/tools")

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
