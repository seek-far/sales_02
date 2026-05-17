from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 0x1
HEADER_SIZE = 0x1

CLIENT_FULL_REQUEST = 0x1
CLIENT_AUDIO_ONLY_REQUEST = 0x2
SERVER_FULL_RESPONSE = 0x9
SERVER_ERROR_RESPONSE = 0xF

NO_SEQUENCE = 0x0
NEG_SEQUENCE = 0x2

NO_SERIALIZATION = 0x0
JSON_SERIALIZATION = 0x1

GZIP_COMPRESSION = 0x1


@dataclass(slots=True)
class ServerMessage:
    message_type: int
    flags: int
    sequence: int | None
    payload: Any
    error_code: int | None = None


def _header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        [
            (PROTOCOL_VERSION << 4) | HEADER_SIZE,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        ]
    )


def _payload_bytes(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def build_full_client_request(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _header(CLIENT_FULL_REQUEST, NO_SEQUENCE, JSON_SERIALIZATION, GZIP_COMPRESSION) + _payload_bytes(
        gzip.compress(raw)
    )


def build_audio_request(sequence: int, pcm: bytes, *, final: bool = False) -> bytes:
    flags = NEG_SEQUENCE if final else NO_SEQUENCE
    return _header(CLIENT_AUDIO_ONLY_REQUEST, flags, NO_SERIALIZATION, GZIP_COMPRESSION) + _payload_bytes(
        gzip.compress(pcm)
    )


def parse_server_message(data: bytes) -> ServerMessage:
    if len(data) < 4:
        raise ValueError("server frame is shorter than the ASR protocol header")

    header_size = data[0] & 0x0F
    header_bytes = header_size * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_bytes

    sequence: int | None = None
    if flags & 0x1:
        sequence = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4

    error_code: int | None = None
    if message_type == SERVER_ERROR_RESPONSE:
        error_code = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4

    payload: bytes = b""
    if len(data) >= offset + 4:
        payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        payload = data[offset : offset + payload_size]

    if compression == GZIP_COMPRESSION and payload:
        payload = gzip.decompress(payload)

    if serialization == JSON_SERIALIZATION and payload:
        decoded: Any = json.loads(payload.decode("utf-8"))
    elif payload:
        decoded = payload.decode("utf-8", errors="replace")
    else:
        decoded = None

    return ServerMessage(message_type=message_type, flags=flags, sequence=sequence, payload=decoded, error_code=error_code)
