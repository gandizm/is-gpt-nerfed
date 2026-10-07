"""Windows entry point for the PyInstaller build.

The bundled plugin is copied to a persistent Codex-owned directory before the
CLI is loaded. Hooks therefore keep pointing at real files after the one-file
executable's temporary extraction directory is removed.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import sys

# The CLI is loaded from a data file at runtime, so PyInstaller cannot see its
# imports during static analysis. Keep the standard-library modules used by
# the CLI and its vendored menu visible to the freezer.
import argparse  # noqa: F401
import base64  # noqa: F401
import contextlib  # noqa: F401
import copy  # noqa: F401
import ctypes  # noqa: F401
import datetime  # noqa: F401
import glob  # noqa: F401
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import locale  # noqa: F401
import math  # noqa: F401
import platform  # noqa: F401
import plistlib  # noqa: F401
import queue  # noqa: F401
import random  # noqa: F401
import re  # noqa: F401
import shlex  # noqa: F401
import signal  # noqa: F401
import sqlite3  # noqa: F401
import string  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import types  # noqa: F401
import unicodedata  # noqa: F401
import urllib.error  # noqa: F401
import urllib.request  # noqa: F401
import uuid  # noqa: F401
import zipfile  # noqa: F401


PLUGIN_RELATIVE = Path("skills") / "is-gpt-nerfed" / "scripts" / "nerfed"


def bundled_plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    if configured:
        return Path(configured)

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        source = bundle_root / "plugin"
        codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        ledger = Path(os.environ.get("NERFED_HOME") or (codex_home / "is-gpt-nerfed"))
        target = ledger / "plugin-bundle"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        except OSError as exc:
            raise SystemExit(f"cannot prepare the bundled plugin at {target}: {exc}") from exc
        return target

    return Path(__file__).resolve().parents[1] / "plugin"


def load_cli():
    plugin_root = bundled_plugin_root()
    os.environ.setdefault("PLUGIN_ROOT", str(plugin_root))
    script = plugin_root / PLUGIN_RELATIVE
    if not script.is_file():
        raise SystemExit(f"bundled nerfed CLI is missing: {script}")

    loader = importlib.machinery.SourceFileLoader("is_gpt_nerfed_cli", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise SystemExit(f"cannot load bundled nerfed CLI: {script}")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


if __name__ == "__main__":
    cli = load_cli()
    args = sys.argv[1:]
    if args and args[0] == cli.DGC_BIN:
        args = args[1:]
    raise SystemExit(cli.main(args))
