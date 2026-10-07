"""Windowed PySide6 entry point used by the Windows desktop build."""

from __future__ import annotations

import sys
from nerfed_launcher import load_cli, restore_backend_streams


if __name__ == "__main__":
    if sys.argv[1:]:
        restore_backend_streams()
    cli = load_cli()
    args = sys.argv[1:]
    # Frozen backend workers must not open another desktop panel.
    if args and args[0] == cli.DGC_BIN:
        args = args[1:]
    if args and args[0] == "--smoke-test":
        import json
        from pathlib import Path
        from PySide6.QtWidgets import QApplication, QPushButton
        from ui import MainWindow
        app = QApplication([])
        window = MainWindow(cli, auto_refresh=False)
        window.timer.stop()
        window.snapshot = {
            "install": {"codex_found": False, "plugin_enabled": False},
            "hooks": {"state": "missing"},
            "threads": [],
        }
        window.render_snapshot()
        window.show()
        app.processEvents()
        labels = [button.text() for button in window.findChildren(QPushButton)]
        if not any(label in ("Install", "安装") for label in labels):
            raise RuntimeError("Missing installation entry")
        Path(args[1]).write_text(json.dumps({"status": window.status.text(), "buttons": labels},
                                          ensure_ascii=False), encoding="utf-8")
        raise SystemExit(0)
    if args:
        raise SystemExit(cli.main(args))
    from ui import run_ui
    raise SystemExit(run_ui(cli))
