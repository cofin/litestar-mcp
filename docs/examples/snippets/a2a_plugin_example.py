"""Litestar application with A2APlugin snippet."""

from litestar import Litestar, get

from litestar_mcp import A2AConfig, A2APlugin


@get("/skills/greet", opt={"a2a_skill": "greet", "a2a_description": "Greet a user"})
def greet_user(name: str) -> str:
    """Return a personalized greeting."""
    return f"Hello, {name}!"


config = A2AConfig(name="GreetingAgent", base_path="/a2a")
app = Litestar(route_handlers=[greet_user], plugins=[A2APlugin(config=config)])
