"""PySide6 desktop/tray UI for the Windows build.

The macOS app is a thin presentation layer over the Python CLI. This module
keeps that same boundary: every read and write goes through ``snapshot``,
``worker``, ``setup``, ``resume`` or ``config`` so the Windows UI shares the
ledger and behavior with the plugin instead of reimplementing its logic.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, QSize
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from .localization import backend_text, is_chinese, tr
except ImportError:  # PyInstaller loads ui.py as a top-level module.
    from localization import backend_text, is_chinese, tr


def value(obj: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    return (obj or {}).get(key, default)


def pct(number: Any) -> str:
    try:
        return f"{round(float(number) * 100):.0f}%"
    except (TypeError, ValueError):
        return "?"


def probe_word(probe: dict[str, Any] | None) -> str:
    if not probe:
        return tr("Never probed")
    if value(probe, "status") == "failed" or value(probe, "verdict") == "INVALID":
        return tr("Failed")
    verdict = value(probe, "verdict") or "…"
    direction = value(probe, "direction")
    if verdict == "MISMATCH" and direction == "downgrade":
        return tr("Downgrade")
    if verdict == "MISMATCH" and direction == "upgrade":
        return tr("Upgrade")
    return {
        "MATCH": tr("Match"),
        "SUSPICIOUS": tr("Suspicious"),
        "UNLISTED": tr("Unlisted"),
    }.get(verdict, verdict.title())


def probe_detail(probe: dict[str, Any] | None) -> str:
    if not probe:
        return ""
    if value(probe, "status") == "failed":
        return tr((value(probe, "errors") or ["no usable sample"])[0])
    predicted = value(probe, "prediction") or "?"
    probability = pct(value(probe, "probability"))
    expected = value(probe, "expected")
    used = value(probe, "used_outputs")
    queries = value(probe, "queries")
    parts = [f"{predicted} {probability}"]
    if expected and expected != predicted:
        parts.append(tr("declared %@", expected))
    if used and queries and used < queries:
        parts.append(tr("%@ of %@ answers", used, queries))
    if value(probe, "finished_ago"):
        parts.append(str(value(probe, "finished_ago")))
    return " · ".join(parts)


def status_color(snapshot: dict[str, Any] | None) -> str:
    overall = value(snapshot, "overall", {})
    if value(overall, "downgraded", 0) > 0:
        return "#c93636"
    if value(overall, "suspicious", 0) > 0:
        return "#c77700"
    if value(overall, "upgraded", 0) > 0:
        return "#23834b"
    return "#2f6fed"


def localize_widgets(root: QWidget) -> None:
    if not is_chinese():
        return
    for widget in root.findChildren(QWidget):
        if widget.objectName() == "title":
            continue  # session titles are user data, not app-owned labels
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                widget.setItemText(index, tr(widget.itemText(index)))
        elif isinstance(widget, (QLabel, QPushButton, QToolButton, QCheckBox)):
            widget.setText(tr(widget.text()))
    for action in root.findChildren(QAction):
        action.setText(tr(action.text()))


class Backend:
    """Serialize calls into the existing CLI and keep its stdout out of the UI."""

    def __init__(self, cli: Any):
        self.cli = cli
        self._lock = threading.Lock()

    def call(self, args: list[str]) -> tuple[int, str, str]:
        with self._lock:
            stdout, stderr = io.StringIO(), io.StringIO()
            code = 0
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = self.cli.main(args)
                    code = int(result or 0)
            except SystemExit as exc:
                code = int(exc.code or 0) if isinstance(exc.code, int) else 1
            except BaseException:
                code = 1
                stderr.write(traceback.format_exc())
            return code, stdout.getvalue(), stderr.getvalue()


class BackendTask(QThread):
    completed = Signal(object)

    def __init__(self, backend: Backend, args: list[str]):
        super().__init__()
        self.backend = backend
        self.args = args

    def run(self) -> None:
        self.completed.emit(self.backend.call(self.args))


class FunctionTask(QThread):
    completed = Signal(object)

    def __init__(self, function: Callable[[], Any]):
        super().__init__()
        self.function = function

    def run(self) -> None:
        self.completed.emit(self.function())


class SettingsDialog(QDialog):
    saved = Signal()

    def __init__(self, window: "MainWindow", snapshot: dict[str, Any]):
        super().__init__(window)
        self.window = window
        self.snapshot = snapshot
        self.setWindowTitle(tr("Settings"))
        self.setMinimumWidth(520)
        self.setModal(True)
        self.controls: dict[str, Any] = {}
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        cfg = value(snapshot, "config", {})

        self._combo(form, "Probe each active session", "frequency", value(cfg, "frequency", "30m"), [
            ("manual", "Manually"), ("turns:4", "Every 4 turns"), ("turns:8", "Every 8 turns"),
            ("turns:16", "Every 16 turns"), ("15m", "Every 15 min of activity"), ("30m", "Every 30 min of activity"),
            ("1h", "Every hour of activity"), ("2h", "Every 2 hours of activity"),
        ])
        self._combo(form, "Fresh-session heartbeat", "fresh_frequency", value(cfg, "fresh_frequency", "manual"), [
            ("manual", "Manually"), ("15m", "Every 15 minutes"), ("30m", "Every 30 minutes"),
            ("1h", "Every hour"), ("2h", "Every 2 hours"), ("6h", "Every 6 hours"),
        ])
        self._combo(form, "When a probe is due", "mode", value(cfg, "mode", "auto"), [
            ("auto", "Probe in the background"), ("nudge", "Only remind me"),
        ])
        queries = QSpinBox()
        queries.setRange(1, 3)
        queries.setValue(int(value(cfg, "queries", 3)))
        self.controls["queries"] = queries
        form.addRow("Forks per probe", queries)

        for key, label, default in [
            ("parallel", "Run forks in parallel", True),
            ("passive", "Scan rollouts on every turn", True),
            ("notify", "Notifications", True),
            ("notify_on_ok", "Also notify on a match", False),
            ("announce_ok", "Post matches into the session", False),
            ("sound", "Sound on a downgrade", True),
            ("halt_on_mismatch", "Halt the session after a mismatch", False),
            ("hide_titles", "Hide session titles and account", False),
            ("check_updates", "Check for updates", True),
        ]:
            check = QCheckBox()
            check.setChecked(bool(value(cfg, key, default)))
            self.controls[key] = check
            form.addRow(label, check)

        confidence = QComboBox()
        for raw, label in [("0.7", "70%"), ("0.8", "80%"), ("0.9", "90%"), ("0.95", "95%")]:
            confidence.addItem(label, raw)
        current_conf = str(value(cfg, "mismatch_confidence", 0.8))
        index = max(0, confidence.findData(current_conf))
        confidence.setCurrentIndex(index)
        self.controls["mismatch_confidence"] = confidence
        form.addRow("Call a mismatch at", confidence)
        root.addLayout(form)

        note = QLabel("Launch at login is managed by Windows startup settings. The backend and ledger are shared with the CLI.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        localize_widgets(self)

    def _combo(self, form: QFormLayout, label: str, key: str, current: str, options: list[tuple[str, str]]) -> None:
        combo = QComboBox()
        for raw, display in options:
            combo.addItem(display, raw)
        index = max(0, combo.findData(current))
        combo.setCurrentIndex(index)
        self.controls[key] = combo
        form.addRow(label, combo)

    def save(self) -> None:
        changes: dict[str, str] = {}
        for key, control in self.controls.items():
            if isinstance(control, QComboBox):
                changes[key] = str(control.currentData())
            elif isinstance(control, QSpinBox):
                changes[key] = str(control.value())
            else:
                changes[key] = "true" if control.isChecked() else "false"
        self.window.save_settings(changes, self.accept)


class MainWindow(QMainWindow):
    def __init__(self, cli: Any):
        super().__init__()
        self.backend = Backend(cli)
        self.snapshot: dict[str, Any] | None = None
        self.tasks: list[BackendTask] = []
        self.refreshing = False
        self.setWindowTitle(tr("Is GPT nerfed?"))
        self.setMinimumSize(420, 600)
        self.resize(480, 780)
        self.setStyleSheet(self.stylesheet())
        self._build_shell()
        self._build_tray()
        localize_widgets(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(8000)
        QTimer.singleShot(0, self.refresh)

    @staticmethod
    def stylesheet() -> str:
        return """
        QMainWindow, QDialog { background: #f4f6fa; color: #172033; }
        QScrollArea { border: none; background: transparent; }
        QFrame#card { background: white; border: 1px solid #e1e5ec; border-radius: 14px; }
        QFrame#setup { background: #fff8e9; border: 1px solid #f0d48c; border-radius: 14px; }
        QLabel#brand { color: #172033; font-size: 17px; font-weight: 700; }
        QLabel#face { font-size: 34px; color: #2f6fed; }
        QLabel#status { font-size: 20px; font-weight: 700; }
        QLabel#message { color: #566174; font-size: 13px; }
        QLabel#section { color: #667085; font-size: 12px; font-weight: 700; letter-spacing: 1px; }
        QLabel#title { color: #172033; font-size: 14px; font-weight: 700; }
        QLabel#meta, QLabel#muted { color: #748096; font-size: 12px; }
        QLabel#good { color: #23834b; font-size: 13px; font-weight: 600; }
        QLabel#warn { color: #bd7200; font-size: 13px; font-weight: 600; }
        QLabel#bad { color: #c93636; font-size: 13px; font-weight: 600; }
        QPushButton { background: #2f6fed; color: white; border: none; border-radius: 8px; padding: 7px 13px; font-weight: 600; }
        QPushButton:hover { background: #2459c6; }
        QPushButton:disabled { background: #c4cad5; color: #f7f8fb; }
        QPushButton#secondary { background: #e9edf5; color: #344054; }
        QPushButton#secondary:hover { background: #dce3ef; }
        QToolButton { background: transparent; border: none; color: #667085; padding: 5px; }
        QToolButton:hover { color: #2f6fed; }
        QComboBox, QSpinBox { background: white; border: 1px solid #d6dce7; border-radius: 7px; padding: 5px 8px; min-width: 120px; }
        QCheckBox { spacing: 8px; }
        QDialogButtonBox QPushButton { min-width: 90px; }
        """

    def _build_shell(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(12)
        top = QHBoxLayout()
        brand = QLabel("is-gpt-nerfed")
        brand.setObjectName("brand")
        top.addWidget(brand)
        top.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        settings = QPushButton("Settings")
        settings.setObjectName("secondary")
        settings.clicked.connect(self.open_settings)
        top.addWidget(settings)
        outer.addLayout(top)

        self.header = QFrame()
        self.header.setObjectName("card")
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(4)
        self.face = QLabel("(•ᴗ•)")
        self.face.setObjectName("face")
        self.face.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.face)
        self.status = QLabel("Loading…")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.status)
        self.message = QLabel("Reading the ledger…")
        self.message.setObjectName("message")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        header_layout.addWidget(self.message)
        self.hooks = QLabel("")
        self.hooks.setObjectName("muted")
        self.hooks.setAlignment(Qt.AlignCenter)
        self.hooks.setWordWrap(True)
        header_layout.addWidget(self.hooks)
        outer.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.footer = QLabel("Windows desktop UI · backend loading")
        self.footer.setObjectName("muted")
        self.footer.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.footer)
        self.setCentralWidget(root)

    def _build_tray(self) -> None:
        icon_path = Path(os.environ.get("PLUGIN_ROOT", "")) / "assets" / "logo.png"
        icon = QIcon(str(icon_path)) if icon_path.is_file() else self._fallback_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("is-gpt-nerfed")
        menu = QMenu()
        open_action = QAction("Open panel", self)
        open_action.triggered.connect(self.show_panel)
        menu.addAction(open_action)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        menu.addAction(refresh_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_panel() if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    @staticmethod
    def _fallback_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#2f6fed"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 60, 60)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", 24, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "?")
        painter.end()
        return QIcon(pixmap)

    def show_panel(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.start_task(["snapshot", "--json"], self._snapshot_done)

    def _snapshot_done(self, result: tuple[int, str, str]) -> None:
        self.refreshing = False
        code, output, error = result
        if code != 0:
            self.message.setText(tr((error or output or "snapshot failed").strip()[-500:]))
            self.status.setText(tr("Backend error"))
            self.status.setStyleSheet("color: #c93636;")
            return
        try:
            self.snapshot = json.loads(output)
        except json.JSONDecodeError:
            self.message.setText(tr("The backend returned invalid snapshot JSON."))
            return
        self.render_snapshot()

    def render_snapshot(self) -> None:
        snap = self.snapshot or {}
        overall = value(snap, "overall", {})
        install = value(snap, "install", {})
        hooks = value(snap, "hooks", {})
        color = status_color(snap)
        self._set_face(
            bool(value(overall, "downgraded", 0)),
            bool(value(overall, "suspicious", 0)),
            bool(value(overall, "running", 0)),
        )
        self.face.setStyleSheet(f"color: {color};")
        setup_status = None
        if value(install, "codex_found") is False:
            setup_status = ("Codex not found", "#c77700")
        elif value(install, "plugin_enabled") is False or value(hooks, "state") == "missing":
            setup_status = ("Setup required", "#c77700")
        elif value(hooks, "state") == "untrusted":
            setup_status = ("Trust hooks required", "#c77700")
        headline = setup_status[0] if setup_status else backend_text(str(value(overall, "message", "all clear")))
        self.status.setText(tr(headline))
        self.status.setStyleSheet(f"color: {setup_status[1] if setup_status else color};")
        self.message.setText(self._status_detail(snap))
        self.hooks.setText(self._hooks_text(snap))
        self.footer.setText(tr("Last refresh: {}", f"v{value(snap, 'version', '?')} · {value(snap, 'generated', '')}"))

        self._clear_body()
        if value(install, "codex_found") is False:
            self._add_setup("Codex was not found on this Windows installation.", "Install", self.install)
        elif value(install, "plugin_enabled") is False or value(hooks, "state") == "missing":
            self._add_setup("The plugin is not registered with Codex yet. Install it and trust its hooks.", "Install", self.install)
        elif value(hooks, "state") == "untrusted":
            self._add_setup("Codex has not trusted the plugin hooks yet.", "Trust hooks", self.trust_hooks)

        threads = [t for t in value(snap, "threads", []) if value(t, "model") != "codex-auto-review"]
        self._add_section("Active sessions", tr("%@ in 48 h", len(threads)))
        if not threads:
            self._add_empty("No Codex sessions in the last 48 hours.")
        else:
            for thread in threads:
                self._add_thread(thread)

        self._add_section("Fresh session", f"{value(snap, 'default_model', 'default model')} @ {value(snap, 'default_effort', '?')}")
        self._add_fresh(snap)
        self.body_layout.addStretch()
        localize_widgets(self)

    def _set_face(self, alert: bool, warn: bool, running: bool) -> None:
        filename = "face-alert.png" if alert else "face-warn.png" if warn else "face-ok.png"
        roots = []
        if getattr(sys, "frozen", False):
            roots.append(Path(getattr(sys, "_MEIPASS", "")) / "resources")
        roots.append(Path(__file__).resolve().parents[1] / "macos" / "Resources")
        asset = next((root / filename for root in roots if (root / filename).is_file()), None)
        if asset:
            self.face.setText("")
            self.face.setPixmap(QPixmap(str(asset)).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.face.setPixmap(QPixmap())
            self.face.setText("(ಠ_ಠ)" if alert else "(•_•)" if warn else "(•o•)" if running else "(•ᴗ•)")

    def _status_detail(self, snap: dict[str, Any]) -> str:
        account = value(snap, "account", {})
        bits = []
        if value(account, "label"):
            bits.append(str(value(account, "label")))
        last = value(snap, "last_verdict")
        if last:
            bits.append(f"{tr('Last probe')} · {probe_word(last)} · {probe_detail(last)}")
        return " · ".join(bits) or tr("The Windows panel shares the same ledger as the CLI.")

    def _hooks_text(self, snap: dict[str, Any]) -> str:
        hooks = value(snap, "hooks", {})
        state = value(hooks, "state")
        if state == "trusted":
            return tr("Hooks trusted {} / {}", value(hooks, "trusted", 0), value(hooks, "total", 0))
        if state == "untrusted":
            return tr("Hooks are not trusted by Codex")
        if state == "missing":
            return tr("Codex does not list this plugin's hooks yet")
        return ""

    def _add_setup(self, text: str, button: str | None, action: Callable[[], None] | None = None) -> None:
        frame = QFrame()
        frame.setObjectName("setup")
        row = QHBoxLayout(frame)
        dot = QLabel("●")
        dot.setStyleSheet("color: #c77700;")
        row.addWidget(dot)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("muted")
        row.addWidget(label, 1)
        if button and action:
            b = QPushButton(button)
            b.clicked.connect(action)
            row.addWidget(b)
        self.body_layout.addWidget(frame)

    def _add_section(self, title: str, trailing: str = "") -> None:
        row = QHBoxLayout()
        label = QLabel(tr(title))
        label.setObjectName("section")
        row.addWidget(label)
        row.addStretch()
        if trailing:
            tail = QLabel(trailing)
            tail.setObjectName("muted")
            row.addWidget(tail)
        self.body_layout.addLayout(row)

    def _add_empty(self, text: str) -> None:
        frame = self._card()
        layout = QVBoxLayout(frame)
        label = QLabel(text)
        label.setObjectName("muted")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self.body_layout.addWidget(frame)

    def _add_thread(self, thread: dict[str, Any]) -> None:
        frame = self._card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 13, 16, 13)
        top = QHBoxLayout()
        title = QLabel(str(value(thread, "title", value(thread, "id", "session"))))
        title.setObjectName("title")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        probe = value(thread, "last_probe")
        word = QLabel("Probing…" if value(thread, "probe_running") else probe_word(probe))
        word.setObjectName("bad" if value(thread, "alert") else "warn" if value(thread, "suspicious") else "good")
        top.addWidget(word)
        layout.addLayout(top)
        model = value(thread, "model", "unknown model")
        effort = value(thread, "effort")
        meta = QLabel(f"{model}{' @ ' + str(effort) if effort else ''} · {value(thread, 'updated_ago', '')}")
        meta.setObjectName("meta")
        layout.addWidget(meta)
        evidence = value(thread, "last_evidence")
        if evidence:
            ev = QLabel(str(evidence))
            ev.setObjectName("bad" if value(thread, "last_evidence_severity") == "hard" else "warn")
            ev.setWordWrap(True)
            layout.addWidget(ev)
        if probe:
            detail = QLabel(probe_detail(probe))
            detail.setObjectName("meta")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        buttons = QHBoxLayout()
        buttons.addStretch()
        if value(thread, "halted"):
            resume = QPushButton("Resume")
            resume.clicked.connect(lambda _=False, t=thread: self.resume(t))
            buttons.addWidget(resume)
        inspect = QToolButton()
        inspect.setText("Details")
        inspect.setObjectName("secondary")
        inspect.clicked.connect(lambda _=False, t=thread: self.show_thread_details(t))
        buttons.addWidget(inspect)
        action = QPushButton("Probing…" if value(thread, "probe_running") else "Probe")
        action.setEnabled(not value(thread, "probe_running"))
        action.clicked.connect(lambda _=False, t=thread: self.probe_thread(t))
        buttons.addWidget(action)
        layout.addLayout(buttons)
        self.body_layout.addWidget(frame)

    def _add_fresh(self, snap: dict[str, Any]) -> None:
        frame = self._card()
        layout = QVBoxLayout(frame)
        row = QHBoxLayout()
        model = f"{value(snap, 'default_model', 'default model')} @ {value(snap, 'default_effort', '?')}"
        label = QLabel(model)
        label.setObjectName("meta")
        row.addWidget(label, 1)
        last = value(snap, "global_probe")
        if value(snap, "global_running"):
            state = QLabel("Probing…")
            state.setObjectName("warn")
        elif last:
            state = QLabel(f"{probe_word(last)} · {probe_detail(last)}")
            state.setObjectName("good" if value(last, "verdict") == "MATCH" else "warn")
        else:
            state = QLabel("Never probed · brand-new ephemeral session")
            state.setObjectName("muted")
        state.setWordWrap(True)
        row.addWidget(state, 2)
        button = QPushButton("Probe")
        button.setEnabled(not value(snap, "global_running", False))
        button.clicked.connect(self.probe_fresh)
        row.addWidget(button)
        layout.addLayout(row)
        self.body_layout.addWidget(frame)

    def _card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        return frame

    def _clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def start_task(self, args: list[str], callback: Callable[[tuple[int, str, str]], None]) -> None:
        task = BackendTask(self.backend, args)
        self.tasks.append(task)
        def done(result: tuple[int, str, str], task: BackendTask = task) -> None:
            if task in self.tasks:
                self.tasks.remove(task)
            callback(result)
            task.deleteLater()
        task.completed.connect(done)
        task.start()

    def install(self) -> None:
        self.start_task(["setup", "--trust-hooks"], self._action_done)

    def trust_hooks(self) -> None:
        self.start_task(["hooks", "trust"], self._action_done)

    def probe_fresh(self) -> None:
        self.start_task(["worker", "--fresh"], self._action_done)

    def probe_thread(self, thread: dict[str, Any]) -> None:
        self.start_task(["worker", "--thread", str(value(thread, "id"))], self._action_done)

    def resume(self, thread: dict[str, Any]) -> None:
        self.start_task(["resume", "--thread", str(value(thread, "id"))], self._action_done)

    def _action_done(self, result: tuple[int, str, str]) -> None:
        code, output, error = result
        if code != 0 and error:
            QMessageBox.warning(self, "is-gpt-nerfed", error[-1200:])
        self.refresh()

    def show_thread_details(self, thread: dict[str, Any]) -> None:
        title = str(value(thread, "title", "Session"))
        lines = [title, "", f"Model: {value(thread, 'model', '?')} @ {value(thread, 'effort', '?')}"]
        if value(thread, "last_evidence"):
            lines.append(f"Evidence: {value(thread, 'last_evidence')}")
        probe = value(thread, "last_probe")
        if probe:
            lines.extend([f"Probe: {probe_word(probe)}", f"Details: {probe_detail(probe)}"])
        QMessageBox.information(self, "Session details", "\n".join(lines))

    def open_settings(self) -> None:
        if not self.snapshot:
            return
        dialog = SettingsDialog(self, self.snapshot)
        dialog.exec()

    def save_settings(self, changes: dict[str, str], done: Callable[[], None]) -> None:
        def save_all() -> tuple[int, str, str]:
            output, error = [], []
            code = 0
            for key, value_ in changes.items():
                rc, out, err = self.backend.call(["config", "set", key, value_])
                code = max(code, rc)
                output.append(out)
                error.append(err)
            return code, "".join(output), "".join(error)
        task = FunctionTask(save_all)
        self.tasks.append(task)
        def complete(result: tuple[int, str, str]) -> None:
            if task in self.tasks:
                self.tasks.remove(task)
            if result[0] != 0:
                QMessageBox.warning(self, "Settings", result[2][-1200:] or "Could not save settings")
            else:
                done()
                self.refresh()
            task.deleteLater()
        task.completed.connect(complete)
        task.start()


def run_ui(cli: Any) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("is-gpt-nerfed")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(cli)
    window.show()
    return app.exec()
