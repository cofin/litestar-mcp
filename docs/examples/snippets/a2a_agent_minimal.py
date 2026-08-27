"""Minimal standalone A2A agent snippet."""

from litestar_mcp import Agent

agent = Agent(name="MathAgent", description="Calculates math operations")


@agent.skill(name="add", description="Add two integers")
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b
