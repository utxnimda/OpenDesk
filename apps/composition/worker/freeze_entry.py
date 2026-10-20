"""Host diagnostic worker. Business tools live in independent module packages."""
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    project_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "apps/framework/worker"))

from opendesk_worker.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
