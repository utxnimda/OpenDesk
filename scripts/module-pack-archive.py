"""Create deterministic ZIP entries for an already built module directory.

Only ordinary files are archived. Directory entries, links, traversal names and
case-insensitive collisions are rejected before the output is replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import zipfile


def collect_files(root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*")):
        entry = path.lstat()
        if stat.S_ISLNK(entry.st_mode) or bool(getattr(entry, "st_file_attributes", 0) & 0x400):
            raise ValueError("Module packages cannot include links or reparse points.")
        if path.is_dir():
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise ValueError("Module packages contain only ordinary files.")
        name = path.relative_to(root).as_posix()
        if any(part in ("", ".", "..") or ":" in part or part.endswith((".", " ")) for part in name.split("/")):
            raise ValueError("Unsafe archive path.")
        folded = name.casefold()
        if folded in seen:
            raise ValueError("Case-insensitive archive path collision.")
        seen.add(folded)
        found.append((name, path))
    if "manifest.json" not in seen:
        raise ValueError("A module package needs its root manifest.json.")
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()
    root = Path(args.source).resolve(strict=True)
    output = Path(args.output).resolve()
    if not root.is_dir() or root == output or root in output.parents:
        raise ValueError("The archive must be outside its source directory.")
    files = collect_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".module-pack-", suffix=".tmp", dir=output.parent, delete=False) as temporary:
            staged = Path(temporary.name)
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in files:
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, path.read_bytes(), compresslevel=6)
        with zipfile.ZipFile(staged) as archive:
            failure = archive.testzip()
            if failure is not None:
                raise ValueError("Archive checksum verification failed.")
            if any(entry.is_dir() for entry in archive.infolist()):
                raise ValueError("Archive unexpectedly contains a directory entry.")
        staged.replace(output)
        staged = None
        with output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(json.dumps({"file": output.name, "files": len(files), "sha256": digest}))
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
