"""Serialization tests for SSEManager payload encoding."""

import asyncio

from litestar.serialization import decode_json

from litestar_mcp.sse import SSEManager


def test_published_payload_is_valid_json_decodable_by_litestar() -> None:
    """An enqueued message should be encoded as UTF-8 JSON that Litestar can decode."""

    async def _run() -> None:
        manager = SSEManager()
        manager.register_client("client-a")
        stream_id, gen = await manager.open_stream("client-a")
        # First yielded message is the primer (empty data string).
        primer = await gen.__anext__()
        assert primer.data == ""
        assert primer.id == f"{stream_id}:0"

        payload = {"jsonrpc": "2.0", "method": "ping", "params": {"n": 1, "ok": True}}
        await manager.publish(payload, client_id="client-a")
        message = await gen.__anext__()

        # Data must be str (UTF-8) and round-trip through Litestar's decoder.
        assert isinstance(message.data, str)
        decoded = decode_json(message.data.encode("utf-8"))
        assert decoded == payload

    asyncio.run(_run())
