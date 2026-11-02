"""Dependency resolution helpers for litestar-queues integration."""

from collections.abc import Mapping
from typing import Any

from litestar_queues.config import TaskDependencyResolver

__all__ = ("chain_task_dependencies",)


def chain_task_dependencies(*resolvers: TaskDependencyResolver) -> TaskDependencyResolver:
    """Merge several ``task_dependency_resolver`` callables; later resolvers win on key collisions.

    Args:
        resolvers: One or more dependency resolver callables to combine.

    Returns:
        A combined resolver that awaits each input resolver in sequence and merges their results.
    """

    async def _combined_resolver(task: Any, record: Any, task_context: Any) -> Mapping[str, object]:
        merged: dict[str, object] = {}
        for resolver in resolvers:
            merged.update(await resolver(task, record, task_context))
        return merged

    return _combined_resolver
