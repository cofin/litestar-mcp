==============
Marking Routes
==============

This guide covers how to mark Litestar route handlers for MCP exposure.

Marking Routes for MCP
-----------------------

Routes are exposed to MCP by adding special keywords to their ``opt`` dictionary:

Tools (Executable Functions)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Mark routes as tools using the ``mcp_tool`` kwarg:

.. code-block:: python

    from litestar import get, post

    @get("/users", mcp_tool="list_users")
    async def get_users() -> list[dict]:
        """List all users - exposed as MCP tool."""
        return [{"id": 1, "name": "Alice"}]

    @post("/users", mcp_tool="create_user")
    async def create_user(data: dict) -> dict:
        """Create a new user - exposed as MCP tool."""
        return {"id": 2, "name": data["name"]}

Resources (Read-only Data)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Mark routes as resources using the ``mcp_resource`` kwarg:

.. code-block:: python

    @get("/schema", mcp_resource="user_schema")
    async def get_user_schema() -> dict:
        """User schema - exposed as MCP resource."""
        return {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"}
            }
        }

Advanced Handler Patterns
--------------------------

Dependency Injection
~~~~~~~~~~~~~~~~~~~~

MCP-marked routes can use Litestar's dependency injection system:

.. code-block:: python

    from litestar import Provide
    from litestar.di import Provide

    async def get_database():
        return {"connection": "active"}

    @get("/data",
         mcp_tool="fetch_data",
         dependencies={"db": Provide(get_database)})
    async def fetch_data(db: dict) -> dict:
        """Fetch data using dependency injection."""
        return {"status": db["connection"], "data": [1, 2, 3]}

Request Context
~~~~~~~~~~~~~~~

Access request context in MCP-exposed routes:

.. code-block:: python

    from litestar import Request

    @get("/request-info", mcp_tool="get_request_info")
    async def get_request_info(request: Request) -> dict:
        """Get information about the current request."""
        return {
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers)
        }

Error Handling
--------------

MCP tools and resources should handle errors gracefully:

.. code-block:: python

    from litestar.exceptions import HTTPException

    @get("/users/{user_id:int}", mcp_tool="get_user")
    async def get_user(user_id: int) -> dict:
        """Get user by ID with error handling."""
        if user_id < 1:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        # Simulate database lookup
        if user_id > 1000:
            raise HTTPException(status_code=404, detail="User not found")

        return {"id": user_id, "name": f"User {user_id}"}

Best Practices
--------------

1. **Clear Naming**: Use descriptive names for ``mcp_tool`` and ``mcp_resource`` values
2. **Documentation**: Include docstrings that explain what the tool/resource does
3. **Type Hints**: Use proper type hints for better API documentation
4. **Error Handling**: Handle edge cases and provide clear error messages
5. **Validation**: Validate input data before processing

Example: Complete Handler
-------------------------

Here's a complete example showing a well-structured MCP handler:

.. code-block:: python

    from typing import Optional
    from litestar import get, post
    from litestar.exceptions import HTTPException
    from pydantic import BaseModel

    class User(BaseModel):
        id: int
        name: str
        email: Optional[str] = None

    # Resource: User schema
    @get("/api/schema/user", mcp_resource="user_schema")
    async def get_user_schema() -> dict:
        """Get the user data schema."""
        return User.model_json_schema()

    # Tool: List users
    @get("/api/users", mcp_tool="list_users")
    async def list_users() -> list[dict]:
        """List all users in the system."""
        # In real applications, this would query a database
        return [
            {"id": 1, "name": "Alice", "email": "alice@example.com"},
            {"id": 2, "name": "Bob", "email": "bob@example.com"}
        ]

    # Tool: Get specific user
    @get("/api/users/{user_id:int}", mcp_tool="get_user")
    async def get_user(user_id: int) -> dict:
        """Get a specific user by ID."""
        if user_id < 1:
            raise HTTPException(status_code=400, detail="User ID must be positive")

        # Simulate database lookup
        users = {
            1: {"id": 1, "name": "Alice", "email": "alice@example.com"},
            2: {"id": 2, "name": "Bob", "email": "bob@example.com"}
        }

        if user_id not in users:
            raise HTTPException(status_code=404, detail="User not found")

        return users[user_id]
