"""Unit tests for agent streaming primitives, frame serialization, and SSE decoding."""

from typing import Any, cast

import httpx2
import msgspec
import pytest
from litestar.exceptions import SerializationException
from litestar.serialization import decode_json

from litestar_mcp.agent.streaming import (
    TERMINAL_EVENTS,
    AgentStreamFrame,
    iter_agent_stream,
)
from litestar_mcp.core.serialization import to_json


def test_frame_round_trip() -> None:
    """AgentStreamFrame serializes and deserializes accurately with default omitting."""
    frame = AgentStreamFrame(
        event_type="delta",
        turn_id="turn_42",
        seq=3,
        agent_name="tester",
        text="partial text",
        payload={"confidence": 0.99},
    )
    encoded = to_json(frame)
    decoded_dict = decode_json(encoded, target_type=dict)
    assert "tool_name" not in decoded_dict
    assert "call_id" not in decoded_dict
    assert decoded_dict["event_type"] == "delta"
    assert decoded_dict["turn_id"] == "turn_42"
    assert decoded_dict["seq"] == 3
    assert decoded_dict["agent_name"] == "tester"
    assert decoded_dict["text"] == "partial text"
    assert decoded_dict["payload"] == {"confidence": 0.99}

    reconstructed = decode_json(encoded, target_type=AgentStreamFrame)
    assert reconstructed == frame


def test_frame_rejects_unknown_event_type() -> None:
    """AgentStreamFrame rejects invalid event_type literals during msgspec decoding."""
    invalid_raw = b'{"event_type": "bogus_event", "turn_id": "turn_1"}'
    with pytest.raises((SerializationException, msgspec.ValidationError)):
        decode_json(invalid_raw, target_type=AgentStreamFrame)


def test_to_sse_message_uses_seq_and_event() -> None:
    """to_sse_message formats ServerSentEventMessage with seq as id and event_type as event."""
    frame_with_seq = AgentStreamFrame(
        event_type="thought",
        turn_id="t1",
        seq=5,
        text="thinking deeply",
    )
    sse_msg = frame_with_seq.to_sse_message()
    assert sse_msg.id == "5"
    assert sse_msg.event == "thought"

    frame_without_seq = AgentStreamFrame(
        event_type="ping",
        turn_id="t1",
        seq=0,
    )
    sse_ping = frame_without_seq.to_sse_message()
    assert sse_ping.id is None
    assert sse_ping.event == "ping"


def test_from_sse_takes_seq_from_event_id() -> None:
    """from_sse restores seq from SSE event id when missing or zero in json payload."""
    event = httpx2.ServerSentEvent(
        event="delta",
        data='{"turn_id": "t1", "event_type": "delta", "text": "hello"}',
        id="12",
    )
    frame = AgentStreamFrame.from_sse(event)
    assert frame.seq == 12
    assert frame.event_type == "delta"
    assert frame.text == "hello"

    event_with_seq = httpx2.ServerSentEvent(
        event="delta",
        data='{"turn_id": "t1", "event_type": "delta", "seq": 99, "text": "hello"}',
        id="12",
    )
    frame_preserved = AgentStreamFrame.from_sse(event_with_seq)
    assert frame_preserved.seq == 99


def test_terminal_events_constant() -> None:
    """TERMINAL_EVENTS includes complete and error."""
    assert "complete" in TERMINAL_EVENTS
    assert "error" in TERMINAL_EVENTS
    assert "delta" not in TERMINAL_EVENTS


@pytest.mark.anyio
async def test_iter_agent_stream_with_httpx2() -> None:
    """iter_agent_stream yields AgentStreamFrames until terminal event."""
    sse_lines = [
        b'event: session\r\nid: 1\r\ndata: {"event_type": "session", "turn_id": "t1", "seq": 1}\r\n\r\n',
        b'event: delta\r\nid: 2\r\ndata: {"event_type": "delta", "turn_id": "t1", "seq": 2, "text": "chunk"}\r\n\r\n',
        b'event: complete\r\nid: 3\r\ndata: {"event_type": "complete", "turn_id": "t1", "seq": 3}\r\n\r\n',
    ]

    response = httpx2.Response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        content=b"".join(sse_lines),
        request=httpx2.Request("GET", "http://testserver"),
    )
    frames = [f async for f in iter_agent_stream(cast("Any", response))]
    assert len(frames) == 3
    assert frames[0].event_type == "session"
    assert frames[1].event_type == "delta"
    assert frames[1].text == "chunk"
    assert frames[2].event_type == "complete"
