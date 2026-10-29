"""Small Windows user-bound DPAPI vault; callers persist opaque references only.

This is local secret protection, not a sandbox against other user-owned programs.
The vault never returns secret values from list/status operations.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import uuid


class SecretStoreError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "CREDENTIAL_STORE"
        self.message = message


class WindowsSecretStore:
    def __init__(self, directory: Path, namespace: str) -> None:
        self.directory = Path(directory)
        if not self.directory.is_absolute() or not namespace or len(namespace) > 128:
            raise ValueError("An absolute secret directory and namespace are required.")
        self.namespace = namespace

    def _path(self, reference: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise SecretStoreError("Invalid credential reference.")
        return self.directory / (reference + ".dpapi")

    def _crypt(self, value: bytes, reference: str, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise SecretStoreError("System credential storage is only available on Windows.")

        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        def blob(data: bytes):
            buffer = ctypes.create_string_buffer(data)
            return Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

        source, source_buffer = blob(value)
        entropy, entropy_buffer = blob((self.namespace + ":" + reference).encode("utf-8"))
        output = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
        operation.restype = ctypes.c_int
        # CRYPTPROTECT_UI_FORBIDDEN: background use must never display a native prompt.
        if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
            raise SecretStoreError("Windows credential protection failed; re-enter the credential.")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(output.data)

    def put(self, secret: str) -> str:
        if not isinstance(secret, str) or not secret or len(secret) > 8192:
            raise SecretStoreError("Invalid credential value.")
        reference = uuid.uuid4().hex
        encoded = self._crypt(secret.encode("utf-8"), reference, decrypt=False)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(reference)
        with path.open("xb") as output:
            output.write(b"ODSECRET1\0" + encoded)
        return reference

    def get(self, reference: str) -> str:
        try:
            data = self._path(reference).read_bytes()
        except OSError as error:
            raise SecretStoreError("Stored credential is unavailable; re-enter it.") from error
        if not data.startswith(b"ODSECRET1\0"):
            raise SecretStoreError("Stored credential format is invalid; file preserved.")
        try:
            return self._crypt(data[10:], reference, decrypt=True).decode("utf-8")
        except UnicodeDecodeError as error:
            raise SecretStoreError("Stored credential content is invalid; file preserved.") from error

    def has(self, reference: str | None) -> bool:
        return bool(reference) and self._path(reference).is_file()

    def delete(self, reference: str) -> None:
        self._path(reference).unlink(missing_ok=True)
