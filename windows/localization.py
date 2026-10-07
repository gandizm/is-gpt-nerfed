"""Small localization bridge for the Windows panel.

The macOS app is the source of truth for UI wording.  The Windows port reads
that same zh-Hans Localizable.strings file when the Windows display language
is Chinese, so the two panels do not slowly grow different translations.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QLocale


_EXTRA = {
    "Is GPT nerfed?": "模型被降配了吗？",
    "Refresh": "刷新",
    "Details": "详情",
    "Save": "保存",
    "Cancel": "取消",
    "Open panel": "打开面板",
    "Codex not found": "未找到 Codex",
    "Setup required": "需要安装",
    "Trust hooks required": "需要允许回调",
    "Backend error": "后端错误",
    "Session details": "会话详情",
    "Last refresh: {}": "上次刷新：{}",
    "Hooks trusted {} / {}": "已允许回调 {} / {}",
    "Windows desktop UI · backend loading": "Windows 桌面界面 · 正在加载后端",
    "Codex was not found on this Windows installation.": "这台 Windows 没找到 Codex。点击“安装”注册插件；如果 Codex 刚安装，请重启它。",
    "The plugin is not registered with Codex yet. Install adds the bundled plugin and records hook trust.": "插件尚未注册到 Codex。点击“安装”添加插件并记录回调授权。",
    "The plugin is not registered with Codex yet. Install it and trust its hooks.": "插件尚未注册到 Codex。点击“安装”并允许插件回调运行。",
    "Codex has not trusted the plugin hooks yet.": "Codex 尚未允许插件回调运行。",
    "The Windows panel shares the same ledger as the CLI.": "Windows 面板与 CLI 共用同一份记录。",
    "Hooks are not trusted by Codex": "Codex 尚未允许插件回调",
    "Codex does not list this plugin's hooks yet": "Codex 尚未列出此插件的回调",
    "Codex has not been told to trust the plugin's hooks.": "Codex 尚未允许插件回调运行。",
    "The backend returned invalid snapshot JSON.": "后端返回的状态数据无效。",
    "snapshot failed": "读取状态失败",
    "Could not save settings": "设置保存失败",
    "Model: {} @ {}": "模型：{} @ {}",
    "Evidence: {}": "证据：{}",
    "Probe: {}": "检测：{}",
    "Details: {}": "详情：{}",
    "Launch at login is managed by Windows startup settings. The backend and ledger are shared with the CLI.": "登录启动由 Windows 启动设置管理。后端和记录与 CLI 共用。",
}


def _strings_path() -> Path | None:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", "")) / "localization" / "Localizable.strings")
    here = Path(__file__).resolve()
    roots.append(here.parents[1] / "macos" / "Sources" / "IsGPTNerfed" / "Resources" / "zh-Hans.lproj" / "Localizable.strings")
    return next((p for p in roots if p.is_file()), None)


def _unescape(value: str) -> str:
    return value.replace(r"\\", "\\").replace(r"\"", '"')


def _load_strings() -> dict[str, str]:
    path = _strings_path()
    if path is None:
        return {}
    pattern = re.compile(r'^\s*"((?:\\.|[^"])*)"\s*=\s*"((?:\\.|[^"])*)";')
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match:
                out[_unescape(match.group(1))] = _unescape(match.group(2))
    except OSError:
        return {}
    return out


_IS_CHINESE = QLocale.system().name().lower().startswith("zh") or any(
    str(os.environ.get(key, "")).lower().startswith("zh") for key in ("LANG", "LANGUAGE", "LC_ALL")
)
_STRINGS = _load_strings()


def is_chinese() -> bool:
    return _IS_CHINESE


def tr(text: str, *args: object) -> str:
    """Translate an app-owned string and substitute Apple's %@ placeholders."""
    result = (_STRINGS | _EXTRA).get(text, text) if _IS_CHINESE else text
    if not args:
        return result
    if "%@" in result:
        for arg in args:
            result = result.replace("%@", str(arg), 1)
        return result
    try:
        return result.format(*args)
    except (IndexError, KeyError, ValueError):
        return result


def backend_text(text: str) -> str:
    """Translate the compact status strings produced by the shared backend."""
    if not _IS_CHINESE:
        return text
    exact = (_STRINGS | _EXTRA).get(text)
    if exact:
        return exact
    patterns = (
        (r"^(\d+) downgraded$", "%@ downgraded"),
        (r"^(\d+) suspicious$", "%@ suspicious"),
        (r"^(\d+) upgraded$", "%@ upgraded"),
        (r"^(\d+) unverified$", "%@ unverified"),
        (r"^(\d+) probe running$", "%@ probe running"),
        (r"^(\d+) probes running$", "%@ probes running"),
        (r"^(\d+) in 48 h$", "%@ in 48 h"),
    )
    for pattern, key in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return tr(key, match.group(1))
    for pattern, key in ((r"^(\d+)s ago$", "%@s ago"), (r"^(\d+)m ago$", "%@m ago"),
                         (r"^(\d+)h ago$", "%@h ago"), (r"^(\d+)d ago$", "%@d ago")):
        match = re.fullmatch(pattern, text)
        if match:
            return tr(key, match.group(1))
    return text
