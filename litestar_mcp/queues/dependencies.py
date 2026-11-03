"""Task dependency wiring between litestar-mcp integrations and ``QueuePlugin``."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from litestar.exceptions import ImproperlyConfiguredException
from litestar_queues import QueuePlugin
from litestar_queues.config import TaskDependencyResolver

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

__all__ = ("TaskDependencyChain",)


class TaskDependencyChain:
    """Ordered task dependency resolvers merged into one resolver.

    Later resolvers win on key collisions. ``QueueConfig`` accepts a single
    ``task_dependency_resolver``, so integrations append themselves to the
    application's queue configuration through :meth:`attach` instead of asking
    users to combine resolvers by hand.
    """

    __slots__ = ("resolvers",)

    def __init__(self, *resolvers: TaskDependencyResolver) -> None:
        """Initialize the chain.

        Args:
            resolvers: Resolvers invoked in order for every task attempt.
        """
        self.resolvers = resolvers

    async def __call__(self, task: Any, record: Any, task_context: Any) -> Mapping[str, object]:
        """Invoke every resolver and merge the keyword arguments they supply.

        Returns:
            The merged keyword arguments for the task attempt.
        """
        merged: dict[str, object] = {}
        for resolver in self.resolvers:
            merged.update(await resolver(task, record, task_context))
        return merged

    @classmethod
    def attach(cls, app_config: "AppConfig", resolver: TaskDependencyResolver, *, owner: str) -> QueuePlugin:
        """Add ``resolver`` to the ``QueuePlugin`` registered on ``app_config``.

        The queue reads ``task_dependency_resolver`` on every attempt, so the
        order in which plugins initialize does not matter. Attaching the same
        resolver twice is a no-op. An existing user resolver is kept and runs
        before ``resolver``.

        Args:
            app_config: Application configuration being initialized.
            resolver: Resolver supplying the integration's task keyword arguments.
            owner: Integration name used in configuration error messages.

        Returns:
            The application's ``QueuePlugin``.

        Raises:
            ImproperlyConfiguredException: If no ``QueuePlugin`` is registered, or
                the queue uses a ``task_dependency_provider``, which litestar-queues
                does not allow alongside a resolver.
        """
        queue_plugin = next((plugin for plugin in app_config.plugins if isinstance(plugin, QueuePlugin)), None)
        if queue_plugin is None:
            msg = f"{owner} requires QueuePlugin in the same Litestar(plugins=[...]) list"
            raise ImproperlyConfiguredException(msg)
        config = queue_plugin.config
        if config.task_dependency_provider is not None:
            msg = (
                f"{owner} supplies task dependencies through QueueConfig.task_dependency_resolver, "
                "which litestar-queues does not allow together with task_dependency_provider; "
                "yield the provider's values from a task_dependency_resolver instead"
            )
            raise ImproperlyConfiguredException(msg)
        existing = config.task_dependency_resolver
        if existing is None:
            config.task_dependency_resolver = resolver
        elif existing != resolver and not (isinstance(existing, cls) and resolver in existing.resolvers):
            config.task_dependency_resolver = cls(existing, resolver)
        return queue_plugin
