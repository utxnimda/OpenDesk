"""Composition root for the standard-library-only stdio worker."""

from __future__ import annotations

import logging
import sys
from typing import Callable

from .protocol import ProtocolError, error_response, parse_request, read_frames, success_response, write_response
from .registry import MethodRegistry
from .runtime import register_runtime

LOGGER = logging.getLogger("nimdadesk.worker")


def main(configure: Callable[[MethodRegistry], None] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    registry = MethodRegistry()
    register_runtime(registry)
    if configure is not None:
        configure(registry)
    LOGGER.info("Worker started; protocol version 1.")
    try:
        for frame in read_frames(sys.stdin.buffer):
            request_id = ""
            stop_worker = False
            try:
                if isinstance(frame, ProtocolError):
                    raise frame
                request = parse_request(frame)
                request_id = request.request_id
                result = registry.dispatch(request)
                response = success_response(request_id, result.value)
                stop_worker = result.stop_worker
            except ProtocolError as error:
                response = error_response(error)
                LOGGER.warning("Request rejected (%s).", error.code)
            except Exception:
                response = error_response(ProtocolError("INTERNAL_ERROR", "Worker could not complete the request.", request_id))
                # Do not log exception text or payloads; they may contain secrets.
                LOGGER.error("Registered handler failed.")
            try:
                write_response(sys.stdout.buffer, response)
            except (ValueError, TypeError, RecursionError):
                LOGGER.error("Registered handler produced an invalid response.")
                write_response(sys.stdout.buffer, error_response(ProtocolError("INTERNAL_ERROR", "Worker could not encode the result.", request_id)))
                stop_worker = False
            if stop_worker:
                break
    except BrokenPipeError:
        LOGGER.info("Host output pipe closed.")
    LOGGER.info("Worker stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
