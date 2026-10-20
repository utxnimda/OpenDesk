"""Explicit PyInstaller entry; packaging is controlled by the project build."""

from nimdadesk_worker.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
