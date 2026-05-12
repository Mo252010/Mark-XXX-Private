"""
ATLAS shared utilities — مساعد مشترك
Project-wide helpers: paths, Unicode clipboard paste, basic RTL shaping for Tk.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path


def get_project_root() -> Path:
    """Return frozen-exe folder or repo root directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF]")


def contains_arabic(text: str) -> bool:
    """Return True if *text* has Arabic-script characters."""
    if not text:
        return False
    return _ARABIC_RE.search(text) is not None


def rtl_visual_embed(text: str) -> str:
    """
    Wrap Arabic-heavy strings for better display in LTR Tk Text widgets.

    Uses Unicode embeddings (RLE / PDF); / يستخدم أوامز يونيكود لتثبيت اتجاه العرض في واجهات LTR.

    Tk does not expose full Bidirectional editing; embedding improves mixed-line readability.
    """
    if not text or not contains_arabic(text):
        return text
    return "\u202b" + text + "\u202c"


try:
    import pyautogui
    import pyperclip as _pyperclip_module

    _HAS_INPUT_STACK = True
except ImportError:
    _HAS_INPUT_STACK = False


def paste_unicode_text(text: str, *, pre_delay_s: float = 0.12, post_delay_s: float = 0.18) -> bool:
    """
    Put *text* on the clipboard and send Ctrl+V (Arabic-safe).

    / يكتب العربي أو أي يونيكود عبر الحافظة بدل المحاكي بالحروف الذي يفسد RTL.

    Returns False if pyautogui / pyperclip are missing.
    """
    if not _HAS_INPUT_STACK or not text:
        return False
    try:
        _pyperclip_module.copy(str(text))
        time.sleep(pre_delay_s)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(post_delay_s)
        return True
    except Exception as exc:
        print(f"[utils] clipboard paste failed: {exc}")
        return False


def ascii_only_keystrokes_ok(text: str) -> bool:
    """Latin-only heuristic for pyautogui.write fallback paths."""
    try:
        return bool(text) and all(ord(c) < 128 for c in str(text))
    except Exception:
        return False


def type_with_clipboard_fallback(
    text: str,
    *,
    use_write_for_ascii_only: bool = True,
    write_interval: float = 0.06,
    pre_delay_s: float = 0.1,
    post_delay_s: float = 0.15,
) -> bool:
    """
    Prefer clipboard for any non-ASCII; optional pyautogui.write for ASCII only.

    Mirrors policy for Arabic / emoji / multilingual typing across tools.
    """
    if not text:
        return True
    t = str(text)
    if not _HAS_INPUT_STACK:
        return False
    if ascii_only_keystrokes_ok(t) and use_write_for_ascii_only:
        try:
            pyautogui.write(t, interval=write_interval)
            time.sleep(post_delay_s)
            return True
        except Exception as exc:
            print(f"[utils] pyautogui.write failed, falling back to clipboard: {exc}")
    return paste_unicode_text(t, pre_delay_s=pre_delay_s, post_delay_s=post_delay_s)


if __name__ == "__main__":
    raise SystemExit("utils.py is imported by ATLAS modules; launch main.py.")
