# actions/send_message.py
# Universal messaging — WhatsApp, Instagram, Telegram, Discord, and more
# Uses clipboard-based input for full Unicode / Arabic / emoji support.
# Supports: contact names AND phone numbers (WhatsApp URI scheme).

import time
import subprocess
import urllib.parse
import webbrowser

import psutil
import pyautogui
import pyperclip

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

_MAX_RETRIES = 2

# ─── Name map (Arabic → saved contact name) ───────────────────────────────────

_NAME_MAP = {
    # ماما — common Arabic spellings / أشكال الاسم في الدارجة
    "مامي":  "Mamy",
    "ماما":  "Mamy",
    "امي":   "Mamy",
    "أمي":   "Mamy",
    # معز
    "معز":   "Moza",
    "موزا":  "Moza",
    # بابا
    "دادي":  "Daddy",
    "بابا":  "Daddy",
    "ابويا": "Daddy",
    "أبويا": "Daddy",
    "ابوي": "Daddy",
    "أبوي": "Daddy",
    "والدي": "Daddy",
    # مروان
    "مروان": "maro",
    "مارو":  "maro",
}


# ─── Low-level helpers ────────────────────────────────────────────────────────

def _resolve_receiver(receiver: str) -> str:
    """
    Converts Arabic contact names to their saved English equivalents.
    Arabic key → الاسم المحفوظ في جهات الواتساب؛ إن لم يُعثر على مفتاح نُرجِع النص كما هو.
    """
    return _NAME_MAP.get(receiver.strip(), receiver)


def _safe_write(text: str, delay: float = 0.2) -> None:
    """Clipboard-paste — works with Arabic, emoji, URLs, any Unicode text."""
    pyperclip.copy(text)
    time.sleep(delay)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(delay)


def _wait_for_app(process_name: str, timeout: float = 20.0) -> bool:
    """Waits until the app process is running — more reliable than fixed sleep."""
    start = time.time()
    while time.time() - start < timeout:
        for proc in psutil.process_iter(["name"]):
            if process_name.lower() in proc.info["name"].lower():
                time.sleep(2.5)  # extra wait for UI to fully render
                return True
        time.sleep(0.5)
    return False


def _open_app(app_name: str, wait: float = 2.0) -> bool:
    """Opens an app via Windows search using clipboard (handles any language/name)."""
    try:
        pyautogui.press("esc")
        time.sleep(0.5)
        pyautogui.press("win")
        time.sleep(1.8)
        pyperclip.copy(app_name)
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(1.2)
        pyautogui.press("enter")
        time.sleep(wait)
        return True
    except Exception as e:
        print(f"[SendMessage] Could not open {app_name}: {e}")
        return False


def _search_and_open_contact(receiver: str, use_ctrl_k: bool = False) -> bool:
    """
    Searches for a contact using Ctrl+F (default) or Ctrl+K (Telegram/Discord),
    then selects the first result. Returns True on success.
    """
    try:
        shortcut = ("ctrl", "k") if use_ctrl_k else ("ctrl", "f")
        pyautogui.hotkey(*shortcut)
        time.sleep(1.2)  # انتظر الـ search box يفتح ويكون focused

        # امسح أي نص موجود بهدوء من غير ما تعمل select all
        for _ in range(25):
            pyautogui.press("backspace")
        time.sleep(0.2)

        _safe_write(receiver)
        time.sleep(2.5)  # انتظر النتايج تظهر

        pyautogui.press("enter")
        time.sleep(2.5)  # انتظر المحادثة تفتح بالكامل
        return True
    except Exception as e:
        print(f"[SendMessage] Contact search error: {e}")
        return False


# ─── Per-platform senders ─────────────────────────────────────────────────────

def _send_whatsapp(receiver: str, message: str) -> str:
    clean = receiver.replace(" ", "").replace("-", "").replace("+", "")
    if clean.isdigit():
        try:
            encoded = urllib.parse.quote(message)
            uri = f"whatsapp://send?phone={receiver.replace(' ', '')}&text={encoded}"
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)

            # انتظر WhatsApp يفتح فعلاً بدل sleep ثابت
            if not _wait_for_app("WhatsApp", timeout=20.0):
                return "WhatsApp took too long to open, sir."

            time.sleep(4.0)  # انتظر الـ UI والمحادثة تلود
            pyautogui.press("enter")
            time.sleep(1.0)
            return f"Message sent to {receiver} via WhatsApp, sir."
        except Exception as e:
            print(f"[SendMessage] URI method failed: {e} — falling back to desktop app...")

    # Strategy 2: desktop app + Ctrl+F
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if not _open_app("WhatsApp", wait=2.0):
                return "Could not open WhatsApp, sir."

            if not _wait_for_app("WhatsApp", timeout=20.0):
                return "WhatsApp took too long to open, sir."

            time.sleep(3.0)  # انتظر الـ UI يستقر

            if not _search_and_open_contact(receiver):
                if attempt < _MAX_RETRIES:
                    print(f"[SendMessage] Retry {attempt}/{_MAX_RETRIES}...")
                    continue
                return f"Could not find contact '{receiver}' on WhatsApp, sir."

            # تأكد إن الـ message box active قبل الكتابة
            time.sleep(0.5)
            pyautogui.click()  # click في المكان الحالي عشان يضمن الـ focus
            time.sleep(0.3)

            _safe_write(message)
            time.sleep(0.8)
            pyautogui.press("enter")
            time.sleep(0.8)

            return f"Message sent to {receiver} via WhatsApp, sir."

        except Exception as e:
            if attempt == _MAX_RETRIES:
                return f"WhatsApp error: {e}"

    return "WhatsApp: max retries reached."


def _send_telegram(receiver: str, message: str) -> str:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if not _open_app("Telegram", wait=2.0):
                return "Could not open Telegram, sir."

            if not _wait_for_app("Telegram", timeout=20.0):
                return "Telegram took too long to open, sir."

            time.sleep(2.0)

            if not _search_and_open_contact(receiver, use_ctrl_k=True):
                if attempt < _MAX_RETRIES:
                    print(f"[SendMessage] Retry {attempt}/{_MAX_RETRIES}...")
                    continue
                return f"Could not find '{receiver}' on Telegram, sir."

            time.sleep(0.5)
            pyautogui.click()
            time.sleep(0.3)

            _safe_write(message)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(0.5)

            return f"Message sent to {receiver} via Telegram, sir."

        except Exception as e:
            if attempt == _MAX_RETRIES:
                return f"Telegram error: {e}"

    return "Telegram: max retries reached."


def _send_instagram(receiver: str, message: str) -> str:
    try:
        webbrowser.open("https://www.instagram.com/direct/new/")
        time.sleep(7.0)  # انتظر الصفحة تلود

        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.3)
        for _ in range(3):
            pyautogui.press("tab")
            time.sleep(0.15)

        _safe_write(receiver)
        time.sleep(2.5)

        pyautogui.press("down")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(1.0)

        for _ in range(3):
            pyautogui.press("tab")
            time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(2.5)

        pyautogui.click()
        time.sleep(0.3)
        _safe_write(message)
        time.sleep(0.5)
        pyautogui.press("enter")

        return f"Message sent to {receiver} via Instagram DM, sir."

    except Exception as e:
        return f"Instagram error: {e}"


def _send_discord(receiver: str, message: str) -> str:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if not _open_app("Discord", wait=2.0):
                return "Could not open Discord, sir."

            if not _wait_for_app("Discord", timeout=20.0):
                return "Discord took too long to open, sir."

            time.sleep(2.0)

            if not _search_and_open_contact(receiver, use_ctrl_k=True):
                if attempt < _MAX_RETRIES:
                    print(f"[SendMessage] Retry {attempt}/{_MAX_RETRIES}...")
                    continue
                return f"Could not find '{receiver}' on Discord, sir."

            time.sleep(0.5)
            pyautogui.click()
            time.sleep(0.3)

            _safe_write(message)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(0.5)

            return f"Message sent to {receiver} via Discord, sir."

        except Exception as e:
            if attempt == _MAX_RETRIES:
                return f"Discord error: {e}"

    return "Discord: max retries reached."


def _send_generic(platform: str, receiver: str, message: str) -> str:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if not _open_app(platform, wait=2.0):
                return f"Could not open {platform}, sir."

            if not _wait_for_app(platform, timeout=20.0):
                return f"{platform} took too long to open, sir."

            time.sleep(2.0)

            if not _search_and_open_contact(receiver):
                if attempt < _MAX_RETRIES:
                    print(f"[SendMessage] Retry {attempt}/{_MAX_RETRIES}...")
                    continue
                return f"Could not find '{receiver}' on {platform}, sir."

            time.sleep(0.5)
            pyautogui.click()
            time.sleep(0.3)

            _safe_write(message)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(0.5)

            return f"Message sent to {receiver} via {platform}, sir."

        except Exception as e:
            if attempt == _MAX_RETRIES:
                return f"{platform} error: {e}"

    return f"{platform}: max retries reached."


# ─── Public entry point ───────────────────────────────────────────────────────

def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip().lower()

    if not receiver:
        return "Please specify who to send the message to, sir."
    if not message_text:
        return "Please specify what message to send, sir."

    # Resolve Arabic name → saved contact name
    receiver = _resolve_receiver(receiver)

    print(f"[SendMessage] 📨 {platform} → {receiver}: {message_text[:50]}")
    if player:
        player.write_log(f"[msg] Sending to {receiver} via {platform}...")

    if any(k in platform for k in ("whatsapp", "wp", "wapp")):
        result = _send_whatsapp(receiver, message_text)

    elif any(k in platform for k in ("instagram", "ig", "insta")):
        result = _send_instagram(receiver, message_text)

    elif any(k in platform for k in ("telegram", "tg")):
        result = _send_telegram(receiver, message_text)

    elif any(k in platform for k in ("discord", "dc")):
        result = _send_discord(receiver, message_text)

    else:
        result = _send_generic(platform, receiver, message_text)

    print(f"[SendMessage] ✅ {result}")
    if player:
        player.write_log(f"[msg] {result}")

    if speak:
        if "error" in result.lower() or "could not" in result.lower():
            speak("Sorry sir, I could not send the message.")
        else:
            speak("Message sent successfully, sir.")

    return result


if __name__ == "__main__":
    raise SystemExit("send_message is an ATLAS tool module; launch main.py instead.")