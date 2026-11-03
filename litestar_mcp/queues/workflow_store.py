"""Store-backed workflow run and node records with single-writer keys."""

import asyncio
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal

import msgspec
from litestar.serialization import decode_json, encode_json

from litestar_mcp.agent.workflow import StepResult, StepStatus, WorkflowContext

if TYPE_CHECKING:
    from litestar.stores.base import Store

__all__ = ("NodeRecord", "RunStatus", "WorkflowRun", "WorkflowStore")

RunStatus = Literal["running", "completed", "failed"]


class WorkflowRun(msgspec.Struct, kw_only=True):
    """Durable state of one workflow run."""

    run_id: str
    """Unique identifier of the workflow run."""
    workflow: str
    """Name of the `DynamicWorkflow` definition being executed."""
    state: dict[str, Any] = msgspec.field(default_factory=dict)
    """Initial workflow state persisted when the run was created."""
    status: RunStatus = "running"
    """Current lifecycle status of the workflow run (`running`, `completed`, or `failed`)."""
    error: str | None = None
    """Optional error description recorded when `status` transitions to `failed`."""
    created_at: float = msgspec.field(default_factory=time.time)
    """Unix epoch timestamp (seconds) when the workflow run was created."""


class NodeRecord(msgspec.Struct, kw_only=True):
    """Write-once outcome of one node in one run."""

    node: str
    """Name of the workflow node within the DAG."""
    status: StepStatus
    """Terminal step status (`completed`, `skipped`, or `failed`)."""
    output: Any = None
    """JSON-serializable output produced by the node when completed."""
    error: str | None = None
    """Optional error description recorded when the node failed."""


class WorkflowStore:
    """Store-backed run and node records with single-writer keys."""

    def __init__(
        self,
        store: "Store",
        *,
        ttl_seconds: int = 86_400,
        key_prefix: str = "litestar_mcp:workflow",
    ) -> None:
        """Initialize WorkflowStore with backing Store and configuration."""
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix

    def _run_key(self, run_id: str) -> str:
        """Construct the Store key for a workflow run record."""
        return f"{self.key_prefix}:{run_id}:run"

    def _node_key(self, run_id: str, node: str) -> str:
        """Construct the Store key for a node record."""
        return f"{self.key_prefix}:{run_id}:node:{node}"

    async def create_run(self, run: WorkflowRun) -> None:
        """Create a new durable workflow run record.

        Args:
            run: WorkflowRun record to persist.

        Raises:
            ValueError: If a run with the same run_id already exists.
        """
        run_key = self._run_key(run.run_id)
        if await self.store.exists(run_key):
            msg = f"Workflow run '{run.run_id}' already exists"
            raise ValueError(msg)
        payload = encode_json(run)
        await self.store.set(run_key, payload, expires_in=self.ttl_seconds)

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        """Retrieve a workflow run record by run_id.

        Args:
            run_id: Unique workflow run identifier.

        Returns:
            The decoded WorkflowRun record or None if not found.
        """
        run_key = self._run_key(run_id)
        data = await self.store.get(run_key)
        if data is None:
            return None
        return decode_json(data, target_type=WorkflowRun)

    async def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> WorkflowRun:
        """Update the status of a workflow run.

        Terminal statuses ('completed' and 'failed') are monotonic and will not
        be overwritten by subsequent calls.

        Args:
            run_id: Unique workflow run identifier.
            status: New RunStatus for the run.
            error: Optional error message when transitioning to failed.

        Returns:
            The updated (or unchanged terminal) WorkflowRun record.

        Raises:
            KeyError: If the workflow run does not exist.
        """
        run = await self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in ("completed", "failed"):
            return run
        run.status = status
        run.error = error
        payload = encode_json(run)
        await self.store.set(self._run_key(run_id), payload, expires_in=self.ttl_seconds)
        return run

    async def put_node(self, run_id: str, record: NodeRecord) -> None:
        """Persist a write-once node record for a workflow run.

        Encodes output first, propagating serialization errors. Existing node
        records are not overwritten.

        Args:
            run_id: Unique workflow run identifier.
            record: NodeRecord to store.
        """
        payload = encode_json(record)
        node_key = self._node_key(run_id, record.node)
        if await self.store.exists(node_key):
            return
        await self.store.set(node_key, payload, expires_in=self.ttl_seconds)

    async def get_node(self, run_id: str, node: str) -> NodeRecord | None:
        """Retrieve a node record by run_id and node name.

        Args:
            run_id: Unique workflow run identifier.
            node: Node name.

        Returns:
            The decoded NodeRecord or None if not found.
        """
        node_key = self._node_key(run_id, node)
        data = await self.store.get(node_key)
        if data is None:
            return None
        return decode_json(data, target_type=NodeRecord)

    async def get_nodes(self, run_id: str, names: Iterable[str]) -> dict[str, NodeRecord]:
        """Retrieve multiple node records by names.

        Args:
            run_id: Unique workflow run identifier.
            names: Iterable of node names to fetch.

        Returns:
            Dictionary mapping node names to their decoded NodeRecord instances.
        """
        name_list = list(names)
        if not name_list:
            return {}

        async def _fetch(node_name: str) -> tuple[str, NodeRecord | None]:
            return node_name, await self.get_node(run_id, node_name)

        fetched = await asyncio.gather(*(_fetch(n) for n in name_list))
        return {node_name: record for node_name, record in fetched if record is not None}

    def to_context(
        self,
        run: WorkflowRun,
        nodes: Mapping[str, NodeRecord],
    ) -> WorkflowContext:
        """Reconstruct an in-process WorkflowContext from durable run and node records.

        Args:
            run: WorkflowRun record providing run_id and initial state.
            nodes: Mapping of node names to NodeRecord outcomes.

        Returns:
            Reconstructed WorkflowContext instance.
        """
        return WorkflowContext(
            run_id=run.run_id,
            state=dict(run.state),
            node_outputs={n: r.output for n, r in nodes.items() if r.status == "completed"},
            step_results={
                n: StepResult(
                    node_name=n,
                    status=r.status,
                    output=r.output,
                    error=r.error,
                )
                for n, r in nodes.items()
            },
        )
