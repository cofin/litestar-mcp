"""Tests for SSE (Server-Sent Events) utilities."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from litestar_mcp.sse import format_sse_event, sse_heartbeat, stream_with_backpressure, stream_with_heartbeat


class TestSSEFormatting:
    """Tests for SSE event formatting."""

    def test_format_sse_event_basic(self) -> None:
        """Test basic SSE event formatting."""
        result = format_sse_event("result", {"status": "ok"})
        assert result.startswith("event: result\n")
        assert "data:" in result
        assert '"status":"ok"' in result or '"status": "ok"' in result
        assert result.endswith("\n\n")

    def test_format_sse_event_complex_data(self) -> None:
        """Test SSE formatting with complex nested data."""
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "metadata": {"timestamp": 12345},
        }
        result = format_sse_event("progress", data)
        assert "event: progress\n" in result
        assert "content" in result
        assert "Hello" in result

    def test_format_sse_event_done(self) -> None:
        """Test SSE done event."""
        result = format_sse_event("done", {})
        assert result == "event: done\ndata: {}\n\n"


class TestSSEHeartbeat:
    """Tests for SSE heartbeat generation."""

    @pytest.mark.asyncio
    async def test_sse_heartbeat_generates(self) -> None:
        """Test that heartbeat generates comments."""
        gen = sse_heartbeat(interval=0.01)
        heartbeat = await gen.__anext__()

        assert heartbeat == ": heartbeat\n\n"

    @pytest.mark.asyncio
    async def test_sse_heartbeat_timing(self) -> None:
        """Test heartbeat respects interval."""
        gen = sse_heartbeat(interval=0.05)

        start = asyncio.get_event_loop().time()
        await gen.__anext__()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_sse_heartbeat_multiple(self) -> None:
        """Test multiple heartbeats generate correctly."""
        gen = sse_heartbeat(interval=0.01)

        heartbeat1 = await gen.__anext__()
        heartbeat2 = await gen.__anext__()

        assert heartbeat1 == ": heartbeat\n\n"
        assert heartbeat2 == ": heartbeat\n\n"


class TestStreamWithHeartbeat:
    """Tests for stream with heartbeat merging."""

    @pytest.mark.asyncio
    async def test_stream_with_heartbeat_basic(self) -> None:
        """Test streaming with heartbeat merging."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            yield {"value": 1}
            yield {"value": 2}

        events = [event async for event in stream_with_heartbeat(data_gen(), heartbeat_interval=10)]

        assert len(events) >= 3
        assert any("event: result" in e for e in events)
        assert any("event: done" in e for e in events)

    @pytest.mark.asyncio
    async def test_stream_with_heartbeat_includes_done(self) -> None:
        """Test that done event is sent."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            yield {"chunk": "first"}

        events = [event async for event in stream_with_heartbeat(data_gen(), heartbeat_interval=10)]

        assert events[-1] == "event: done\ndata: {}\n\n"

    @pytest.mark.asyncio
    async def test_stream_with_heartbeat_formats_correctly(self) -> None:
        """Test that data events are formatted correctly."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            yield {"test": "data"}

        events = [
            event
            async for event in stream_with_heartbeat(data_gen(), heartbeat_interval=10)
            if "event: result" in event
        ]

        assert len(events) == 1
        assert "event: result\n" in events[0]
        assert '"test":"data"' in events[0] or '"test": "data"' in events[0]


class TestStreamWithBackpressure:
    """Tests for stream with backpressure handling."""

    @pytest.mark.asyncio
    async def test_stream_with_backpressure_batches(self) -> None:
        """Test that events are batched."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            for i in range(15):
                yield {"value": i}

        events = [event async for event in stream_with_backpressure(data_gen(), batch_size=5, flush_interval=10.0)]

        batch_events = [e for e in events if "event: batch" in e]
        assert len(batch_events) >= 3

        assert any("event: done" in e for e in events)

    @pytest.mark.asyncio
    async def test_stream_with_backpressure_flush_interval(self) -> None:
        """Test that batches flush based on time interval."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            yield {"value": 1}
            await asyncio.sleep(0.15)
            yield {"value": 2}

        events = [event async for event in stream_with_backpressure(data_gen(), batch_size=10, flush_interval=0.1)]

        batch_events = [e for e in events if "event: batch" in e]
        assert len(batch_events) >= 1

    @pytest.mark.asyncio
    async def test_stream_with_backpressure_includes_done(self) -> None:
        """Test that done event is sent."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            yield {"chunk": 1}

        events = [event async for event in stream_with_backpressure(data_gen(), batch_size=5)]

        assert events[-1] == "event: done\ndata: {}\n\n"

    @pytest.mark.asyncio
    async def test_stream_with_backpressure_empty_stream(self) -> None:
        """Test backpressure with empty stream."""

        async def data_gen() -> AsyncGenerator["dict[str, Any]", None]:
            return
            yield

        events = [event async for event in stream_with_backpressure(data_gen(), batch_size=5)]

        assert events[-1] == "event: done\ndata: {}\n\n"
