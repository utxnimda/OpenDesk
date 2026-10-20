"""Source-mode entry for testing this module without coupling the host worker."""
from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "apps/framework/worker"))

from nimdadesk_worker.__main__ import main
from apps.features.tower_assistant.backend.service import register

if __name__ == "__main__":
    raise SystemExit(main(register))
