"""Module test capability; version identity is injected into the frozen entry.

The builder supplies literal version/marker values. They never come from the
user request, an environment variable, or a mutable sidecar configuration.
"""
from __future__ import annotations

import os

from opendesk_worker.protocol import ProtocolError
from opendesk_worker.registry import MethodRegistry, MethodResult

MAX_TEXT_LENGTH = 4096


def register(registry: MethodRegistry, *, version: str, marker: str) -> None:
    if not isinstance(version, str) or not version or not isinstance(marker, str) or not marker:
        raise ValueError("The build must provide a version and marker.")

    def echo(params: dict) -> MethodResult:
        if set(params) != {"text"} or not isinstance(params["text"], str):
            raise ProtocolError("INVALID_PARAMS", "test.echo accepts exactly one string field: text.")
        text = params["text"]
        if len(text) > MAX_TEXT_LENGTH or any(0xD800 <= ord(character) <= 0xDFFF for character in text):
            raise ProtocolError("INVALID_PARAMS", "text must contain at most 4096 Unicode characters without isolated surrogates.")
        return MethodResult({"version": version, "marker": marker, "text": text, "pid": os.getpid()})

    registry.register("test.echo", echo)
