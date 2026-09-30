"""The transport seam: whole messages over a byte stream, and what a bad stream does."""

from __future__ import annotations

import asyncio

import pytest
from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_BYTES,
    ByteStream,
    MessageError,
    RejectionCode,
    client_channel,
    encode_frame,
    encode_message,
    server_channel,
)
from protocol_helpers import FakeStream, input_batch, join_request, snapshot


def test_fake_stream_satisfies_the_published_protocol() -> None:
    stream: ByteStream = FakeStream()
    assert stream.peer == "fake"


def test_a_server_channel_reads_client_messages_in_order() -> None:
    frames = encode_frame(encode_message(join_request())) + encode_frame(
        encode_message(input_batch())
    )
    stream = FakeStream(frames)
    channel = server_channel(stream)

    async def read() -> list[object]:
        return [await channel.receive(), await channel.receive(), await channel.receive()]

    first, second, third = asyncio.run(read())
    assert first == join_request()
    assert second == input_batch()
    assert third is None


def test_a_server_channel_writes_framed_server_messages() -> None:
    stream = FakeStream()
    channel = server_channel(stream)
    asyncio.run(channel.send(snapshot()))
    payload = encode_message(snapshot())
    assert bytes(stream.written) == encode_frame(payload)


def test_a_client_channel_reads_server_messages() -> None:
    stream = FakeStream(encode_frame(encode_message(snapshot())))
    channel = client_channel(stream)
    assert asyncio.run(channel.receive()) == snapshot()


def test_a_client_frame_is_refused_by_a_client_channel() -> None:
    stream = FakeStream(encode_frame(encode_message(input_batch())))
    channel = client_channel(stream)
    with pytest.raises(MessageError) as error:
        asyncio.run(channel.receive())
    assert error.value.code is RejectionCode.UNEXPECTED_MESSAGE


def test_a_truncated_payload_is_refused() -> None:
    frame = encode_frame(encode_message(join_request()))
    stream = FakeStream(frame[: len(frame) - 5])
    channel = server_channel(stream)
    with pytest.raises(MessageError) as error:
        asyncio.run(channel.receive())
    assert error.value.code is RejectionCode.MALFORMED_FRAME


def test_a_truncated_header_reads_as_end_of_stream_only_when_empty() -> None:
    channel = server_channel(FakeStream(b"\x00\x00"))
    with pytest.raises(MessageError) as error:
        asyncio.run(channel.receive())
    assert error.value.code is RejectionCode.MALFORMED_FRAME


def test_an_oversized_declaration_is_refused_before_the_payload_is_read() -> None:
    header = (MAX_FRAME_BYTES + 1).to_bytes(FRAME_HEADER_BYTES, "big")
    stream = FakeStream(header + b"x" * 10)
    channel = server_channel(stream)
    with pytest.raises(MessageError) as error:
        asyncio.run(channel.receive())
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE
    assert len(stream.incoming) == 10


def test_garbage_inside_a_well_formed_frame_is_refused() -> None:
    stream = FakeStream(encode_frame(b"not json at all"))
    channel = server_channel(stream)
    with pytest.raises(MessageError) as error:
        asyncio.run(channel.receive())
    assert error.value.code is RejectionCode.MALFORMED_FRAME


def test_closing_the_channel_closes_the_stream() -> None:
    stream = FakeStream()
    channel = server_channel(stream)
    asyncio.run(channel.close())
    assert stream.closed
    assert channel.closed
