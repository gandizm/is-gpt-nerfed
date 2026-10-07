"""Windowed PySide6 entry point used by the Windows desktop build."""

from __future__ import annotations

from nerfed_launcher import load_cli
from ui import run_ui


if __name__ == "__main__":
    raise SystemExit(run_ui(load_cli()))
