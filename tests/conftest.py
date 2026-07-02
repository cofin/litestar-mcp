"""Global pytest configuration shared across unit and integration suites."""

import anyio
import pytest
from anyio.abc import ByteReceiveStream, ByteSendStream

pytest_plugins = ["pytest_databases.docker.postgres"]


class BridgeBytesSink(ByteSendStream):
    """In-memory byte sink for stdio bridge tests."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    async def send(self, item: bytes) -> None:
        self.buffer.extend(item)

    async def aclose(self) -> None:
        return None


class BridgeBlockingBytesSource(ByteReceiveStream):
    """Byte source that blocks forever after recording that receive started."""

    receive_started: anyio.Event

    def __init__(self) -> None:
        self.receive_started = anyio.Event()

    async def receive(self, max_bytes: int = 65536) -> bytes:
        self.receive_started.set()
        await anyio.sleep_forever()
        return b""

    async def aclose(self) -> None:
        return None


class BridgeQueuedBytesSource(ByteReceiveStream):
    """Byte source that returns queued chunks, then EOF by default."""

    def __init__(self, *chunks: bytes, block_after_chunks: bool = False) -> None:
        self._chunks = list(chunks)
        self._block_after_chunks = block_after_chunks
        self.receive_started = anyio.Event()

    async def receive(self, max_bytes: int = 65536) -> bytes:
        self.receive_started.set()
        if self._chunks:
            return self._chunks.pop(0)
        if self._block_after_chunks:
            await anyio.sleep_forever()
        return b""

    async def aclose(self) -> None:
        return None


def pytest_configure(config: "pytest.Config") -> "None":
    """Register markers for selective unit and integration test runs."""

    config.addinivalue_line("markers", "unit: marks tests that do not require external services")
    config.addinivalue_line("markers", "integration: marks tests that require real service backends")


def pytest_collection_modifyitems(items: "list[pytest.Item]") -> "None":
    """Apply default markers based on the split test tree."""

    for item in items:
        path_parts = set(item.path.parts)
        if "unit" in path_parts:
            item.add_marker(pytest.mark.unit)
        if "integration" in path_parts:
            item.add_marker(pytest.mark.integration)
