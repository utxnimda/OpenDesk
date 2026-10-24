"""OpenDesk module entry, separate from the standalone desktop launcher."""
from nimda_opendesk.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
