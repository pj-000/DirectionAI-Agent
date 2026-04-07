from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness"

for candidate in (str(BACKEND_ROOT), str(HARNESS_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

