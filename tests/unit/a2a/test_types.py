"""Unit tests for canonical A2A v1.0 data models and serialization."""

from litestar.serialization import decode_json, encode_json

from litestar_mcp.a2a.types import (
    TERMINAL_A2A_STATES,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Artifact,
    DataPart,
    FilePart,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    ThoughtPart,
)


def test_part_tagged_union_serialization() -> None:
    """Verify Part tagged union serialization and deserialization."""
    text_part = TextPart(text="hello world", metadata={"author": "user"})
    data_part = DataPart(data={"count": 42})
    file_part = FilePart(mime_type="text/plain", uri="https://example.com/file.txt", name="file.txt")
    thought_part = ThoughtPart(thought="analyzing request...")

    assert text_part.type == "text"
    assert data_part.type == "data"
    assert file_part.type == "file"
    assert thought_part.type == "thought"

    encoded = encode_json([text_part, data_part, file_part, thought_part])
    decoded = decode_json(encoded)

    assert len(decoded) == 4
    assert decoded[0]["type"] == "text"
    assert decoded[0]["text"] == "hello world"
    assert decoded[1]["type"] == "data"
    assert decoded[1]["data"] == {"count": 42}
    assert decoded[2]["type"] == "file"
    assert decoded[2]["mimeType"] == "text/plain"
    assert decoded[3]["type"] == "thought"
    assert decoded[3]["thought"] == "analyzing request..."


def test_message_model() -> None:
    """Test Message construction and serialization."""
    msg = Message(
        role="user",
        parts=[TextPart(text="Calculate sum")],
        message_id="msg-1",
        task_id="task-100",
    )
    assert msg.role == "user"
    assert len(msg.parts) == 1
    assert msg.message_id == "msg-1"

    encoded = encode_json(msg)
    decoded = decode_json(encoded)
    assert decoded["role"] == "user"
    assert decoded["messageId"] == "msg-1"
    assert decoded["taskId"] == "task-100"
    assert decoded["parts"][0]["text"] == "Calculate sum"


def test_artifact_model() -> None:
    """Test Artifact model and chunk streaming attributes."""
    art = Artifact(
        artifact_id="art-1",
        name="output.json",
        mime_type="application/json",
        parts=[DataPart(data={"result": 100})],
        append=False,
        last_chunk=True,
    )
    assert art.name == "output.json"
    assert art.last_chunk is True

    encoded = encode_json(art)
    decoded = decode_json(encoded)
    assert decoded["artifactId"] == "art-1"
    assert decoded["name"] == "output.json"
    assert decoded["mimeType"] == "application/json"
    assert decoded["append"] is False
    assert decoded["lastChunk"] is True


def test_agent_card_and_skills() -> None:
    """Test AgentCard schema and nested skill models."""
    skill = AgentSkill(
        id="calc",
        name="Calculator",
        description="Performs basic arithmetic",
        input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
        output_schema={"type": "object", "properties": {"result": {"type": "number"}}},
        tags=["math", "utility"],
        examples=["Add 2 and 2"],
    )

    card = AgentCard(
        name="MathAgent",
        description="Agent for mathematical calculations",
        version="1.0.0",
        url="https://agent.example.com/a2a",
        capabilities=AgentCapabilities(streaming=True, stateful=True),
        skills=[skill],
        provider=AgentProvider(name="Acme Corp", url="https://acme.example.com"),
    )

    assert card.protocol_version == "1.0"
    assert len(card.skills) == 1
    assert card.skills[0].id == "calc"
    assert card.capabilities.streaming is True

    encoded = encode_json(card)
    decoded = decode_json(encoded)
    assert decoded["name"] == "MathAgent"
    assert decoded["protocolVersion"] == "1.0"
    assert decoded["url"] == "https://agent.example.com/a2a"
    assert decoded["capabilities"]["streaming"] is True
    assert decoded["skills"][0]["name"] == "Calculator"
    assert decoded["skills"][0]["inputSchema"]["type"] == "object"
    assert decoded["provider"]["name"] == "Acme Corp"


def test_task_lifecycle_and_terminal_states() -> None:
    """Test Task model state checks."""
    t_working = Task(
        id="task-1",
        status=TaskStatus(state="working", message="in progress"),
    )
    assert not t_working.is_terminal()
    assert t_working.status.state == "working"

    t_completed = Task(
        id="task-2",
        status=TaskStatus(state="completed"),
        artifacts=[Artifact(name="res", parts=[TextPart(text="done")])],
    )
    assert t_completed.is_terminal()

    for terminal in ("completed", "failed", "canceled", "rejected"):
        assert terminal in TERMINAL_A2A_STATES
        t = Task(id="t", status=TaskStatus(state=terminal))
        assert t.is_terminal()


def test_sse_streaming_event_models() -> None:
    """Test TaskStatusUpdateEvent and TaskArtifactUpdateEvent."""
    status_event = TaskStatusUpdateEvent(
        task_id="t-1",
        status=TaskStatus(state="working", message="Running step 2"),
        final=False,
    )
    art_event = TaskArtifactUpdateEvent(
        task_id="t-1",
        artifact=Artifact(name="chunk.txt", parts=[TextPart(text="partial data")]),
    )

    assert not status_event.final
    encoded_status = encode_json(status_event)
    decoded_status = decode_json(encoded_status)
    assert decoded_status["taskId"] == "t-1"
    assert decoded_status["status"]["state"] == "working"
    assert decoded_status["final"] is False

    encoded_art = encode_json(art_event)
    decoded_art = decode_json(encoded_art)
    assert decoded_art["taskId"] == "t-1"
    assert decoded_art["artifact"]["name"] == "chunk.txt"
