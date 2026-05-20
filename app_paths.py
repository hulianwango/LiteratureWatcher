from __future__ import annotations

import os
import sys
from pathlib import Path


def application_root() -> Path:
    """Return the folder that should contain user-editable project files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_config_path(value: str | os.PathLike[str] = "config.yaml") -> Path:
    path = Path(value)
    if path.is_absolute():
        return path

    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    return (application_root() / path).resolve()


def change_to_config_dir(config_path: Path) -> None:
    os.chdir(config_path.resolve().parent)


def setup_utf8_console() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass
