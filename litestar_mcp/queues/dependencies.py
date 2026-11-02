"""Dependency resolution helpers for litestar-queues integration."""

import inspect
from collections.abc import Mapping
from typing import Any

from litestar_queues.config import TaskDependencyResolver

__all__ = ("chain_task_dependencies",)


def chain_task_dependencies(*resolvers: TaskDependencyResolver) -> TaskDependencyResolver:
    """Merge several ``task_dependency_resolver`` callables; later resolvers win on key collisions.

    Args:
        resolvers: One or more dependency resolver callables to combine.

    Returns:
        A combined resolver that invokes each input resolver in sequence and merges their results.
    """

    async def _combined_resolver(
        task: Any,
        record: Any = None,
        task_context: Any = None,
    ) -> Mapping[str, Any]:
        merged: dict[str, Any] = {}
        for resolver in resolvers:
            raw_res: Any
            try:
                raw_res = resolver(task, record, task_context)
            except TypeError:
                raw_res = resolver(task)  # type: ignore[call-arg]
            resolved: Mapping[str, Any] = await raw_res if inspect.isawaitable(raw_res) else raw_res
            if resolved:
                merged.update(resolved)
        return merged

    return _combined_resolver
