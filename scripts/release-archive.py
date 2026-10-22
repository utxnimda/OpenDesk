"""Archive only an explicitly staged local App; inspect an existing module package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("zip", "inspect-module"))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if args.operation == "inspect-module":
        if args.output:
            parser.error("inspect-module takes no output")
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(name.casefold() for name in names)):
                raise ValueError("Duplicate package entries")
            for name in names:
                parts = name.replace("\\", "/").split("/")
                if any(part in ("", ".", "..") or ":" in part for part in parts):
                    raise ValueError("Unsafe package entry")
                if name != "manifest.json" and parts[0] not in ("frontend", "worker"):
                    raise ValueError("Unexpected package content")
            if archive.testzip() is not None:
                raise ValueError("Package CRC verification failed")
            manifest = json.loads(archive.read("manifest.json"))
        print(json.dumps(manifest, ensure_ascii=False))
        return
    if not source.is_dir() or args.output is None:
        parser.error("zip requires a directory and output file")
    output = args.output.resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError("Archive must be a new file outside the source directory")
    files: list[tuple[str, Path]] = []
    for path in sorted(source.rglob("*")):
        entry = path.lstat()
        if stat.S_ISLNK(entry.st_mode) or bool(getattr(entry, "st_file_attributes", 0) & 0x400):
            raise ValueError("Release archives cannot include links or reparse points")
        if path.is_file():
            files.append((path.relative_to(source).as_posix(), path))
        elif not path.is_dir():
            raise ValueError("Unexpected filesystem entry")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in files:
            archive.write(path, name)
    with zipfile.ZipFile(output) as archive:
        if archive.namelist() != [name for name, _ in files]:
            raise ValueError("Archive inventory mismatch")
        for name, path in files:
            with archive.open(name) as packed, path.open("rb") as original:
                if hashlib.file_digest(packed, "sha256").digest() != hashlib.file_digest(original, "sha256").digest():
                    raise ValueError("Archive file hash mismatch: " + name)
    print(json.dumps({"files": len(files), "allFileHashesVerified": True}))


if __name__ == "__main__":
    main()
