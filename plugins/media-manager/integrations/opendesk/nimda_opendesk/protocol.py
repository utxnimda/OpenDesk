"""Bounded OpenDesk stdio v1 transport. Stdout is reserved for JSONL."""
import json

MAX_FRAME_BYTES = 65536
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProtocolError(Exception):
    def __init__(self, code, message, request_id=""):
        super().__init__(message)
        self.code, self.message, self.request_id = code, message, request_id


def frames(stream):
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
            yield ProtocolError("FRAME_TOO_LARGE", "Request exceeds 65536 bytes.")
        else:
            yield frame


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite number")


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and bool(value.strip()) and value.isprintable()


def parse(frame):
    try:
        raw = json.loads(frame.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ProtocolError("INVALID_JSON", "Request must be valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or not _identifier(raw.get("id")):
        raise ProtocolError("INVALID_REQUEST", "Request requires a valid id.")
    rid = raw["id"]
    if type(raw.get("protocolVersion")) is not int or raw["protocolVersion"] != 1:
        raise ProtocolError("UNSUPPORTED_PROTOCOL", "Only protocol version 1 is supported.", rid)
    if set(raw) != {"protocolVersion", "id", "method", "params"} or not _identifier(raw["method"]) or not isinstance(raw["params"], dict):
        raise ProtocolError("INVALID_REQUEST", "Invalid request envelope.", rid)
    return raw


def error_response(error):
    return {"protocolVersion": 1, "id": error.request_id, "error": {"code": error.code, "message": error.message}}


def write(stream, payload):
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("Response too large")
    stream.write(encoded + b"\n")
    stream.flush()
