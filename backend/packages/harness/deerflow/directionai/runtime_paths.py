from __future__ import annotations

import os
from pathlib import Path


def get_directionai_repo_root() -> Path:
    return Path(__file__).resolve().parents[4].parent


def get_directionai_data_dir() -> Path:
    env_home = os.getenv("DEER_FLOW_HOME")
    if env_home:
        raw = Path(env_home)
        return raw if raw.is_absolute() else (get_directionai_repo_root() / raw).resolve()
    return (get_directionai_repo_root() / ".deer-flow").resolve()
