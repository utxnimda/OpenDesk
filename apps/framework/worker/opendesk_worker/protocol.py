"""Versioned JSONL protocol, with bounded input and explicit validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, BinaryIO, Iterator

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_IDENTIFIER_LENGTH = 128


class ProtocolError(Exception):
    """A safe, public error; messages must never contain input payloads."""

    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


@dataclass(frozen=True)
class Request:
    request_id: str
    method: str
    params: dict[str, Any]


def read_frames(stream: BinaryIO) -> Iterator[bytes | ProtocolError]:
    """Read one bounded frame at a time, draining an oversized line safely.

    LF and CRLF delimiters are excluded from the size limit. A final frame at
    EOF is accepted without a delimiter. Oversized content is never retained.
    """
    while True:
        chunk = stream.readline(MAX_FRAME_BYTES + 2)
        if not chunk:
            return
        ended = chunk.endswith(b"\n")
        frame = chunk[:-1] if ended else chunk
        if ended and frame.endswith(b"\r"):
            frame = frame[:-1]
        if len(frame) > MAX_FRAME_BYTES:
            while not ended:
                chunk = stream.readline(MAX_FRAME_BYTES + 2)
                if not chunk:
                    break
                ended = chunk.endswith(b"\n")
            yield ProtocolError("FRAME_TOO_LARGE", "Request frame exceeds 65536 bytes.")
        else:
            yield frame


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key.")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise ValueError("Non-finite JSON number.")


def _valid_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
        and bool(value.strip())
        and value.isprintable()
    )


def parse_request(frame: bytes) -> Request:
    try:
        payload = json.loads(
            frame.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ProtocolError("INVALID_JSON", "Request must be valid UTF-8 JSON.") from error
    if not isinstance(payload, dict):
        raise ProtocolError("INVALID_REQUEST", "Request must be an object.")

    request_id = payload.get("id")
    if not _valid_identifier(request_id):
        raise ProtocolError("INVALID_REQUEST", "Request id must contain 1 to 128 characters.")
    if type(payload.get("protocolVersion")) is not int or payload["protocolVersion"] != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_PROTOCOL", "Only protocol version 1 is supported.", request_id)
    if set(payload) != {"protocolVersion", "id", "method", "params"}:
        raise ProtocolError("INVALID_REQUEST", "Request fields must be protocolVersion, id, method and params.", request_id)
    if not _valid_identifier(payload["method"]):
        raise ProtocolError("INVALID_REQUEST", "Method must contain 1 to 128 characters.", request_id)
    if not isinstance(payload["params"], dict):
        raise ProtocolError("INVALID_REQUEST", "Request params must be an object.", request_id)
    return Request(request_id=request_id, method=payload["method"], params=payload["params"])


def success_response(request_id: str, result: Any) -> dict[str, Any]:
    return {"protocolVersion": PROTOCOL_VERSION, "id": request_id, "result": result}


def error_response(error: ProtocolError) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "id": error.request_id,
        "error": {"code": error.code, "message": error.message},
    }


def write_response(stream: BinaryIO, response: dict[str, Any]) -> None:
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    frame = encoded.encode("utf-8")
    if len(frame) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeds the bounded transport limit.")
    stream.write(frame + b"\n")
    stream.flush()
