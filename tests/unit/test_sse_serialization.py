"""Serialization tests for SSEManager payload encoding."""

import asyncio

from litestar.serialization import decode_json

from litestar_mcp.sse import SSEManager


def test_published_payload_is_valid_json_decodable_by_litestar() -> None:
    """An enqueued message should be encoded as UTF-8 JSON that Litestar can decode."""

    async def _run() -> None:
        manager = SSEManager()
        stream_id, gen = await manager.open_stream(session_id="session-a")
        primer = await gen.__anext__()
        assert primer.data == ""
        assert primer.id == f"{stream_id}:0"

        payload = {"jsonrpc": "2.0", "method": "ping", "params": {"n": 1, "ok": True}}
        await manager.publish(payload, session_id="session-a")
        message = await gen.__anext__()

        assert isinstance(message.data, str)
        decoded = decode_json(message.data.encode("utf-8"))
        assert decoded == payload
        manager.disconnect(stream_id)

    asyncio.run(_run())
