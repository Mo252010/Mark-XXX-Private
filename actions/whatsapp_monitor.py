# actions/whatsapp_monitor.py
# Monitors for incoming WhatsApp calls and auto-answers with TTS via VB-Cable.

import threading
import time
import asyncio
import tempfile
import os
import re
import ctypes
import ctypes.wintypes

import psutil

try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

_monitor_thread = None
_monitor_running = False
_current_caller = None
_call_detected_time = 0
_speak_fn = None
_ui_ref = None


def _has_runtime_dependencies() -> bool:
    return all(mod is not None for mod in (pyautogui, sd, sf, edge_tts))
_call_active = False  # منع الرد أكتر من مرة على نفس المكالمة

# ─── TTS على VB-Cable ────────────────────────────────────────────────────────

def _get_cable_device_index():
    """يرجع index بتاع CABLE Input في sounddevice."""
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if "cable input" in dev["name"].lower() or "vb-audio" in dev["name"].lower():
            if dev["max_output_channels"] > 0:
                return i
    return None


async def _tts_to_file(text: str, filepath: str, voice: str = "ar-EG-SalmaNeural"):
    """يحول النص لصوت ويحفظه في فايل مؤقت."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filepath)


def speak_on_cable(text: str, voice: str = "ar-EG-SalmaNeural"):
    """
    يتكلم على VB-Cable عشان واتساب يسمعه كـ microphone.
    لو مالقاش الـ CABLE يشغل على الـ default device.
    """
    if not _has_runtime_dependencies():
        print("[WhatsApp Monitor] Optional audio dependencies are missing. Install edge-tts, sounddevice, soundfile, and pyautogui.")
        return

    try:
        # اعمل فايل مؤقت للصوت
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        # TTS
        asyncio.run(_tts_to_file(text, tmp_path, voice))

        # اقرأ الفايل
        data, samplerate = sf.read(tmp_path)
        data = data * 3.0  # boost الصوت 3x
        data = data.clip(-1.0, 1.0)  # منع الـ distortion
        os.unlink(tmp_path)

        # دور على CABLE Input
        cable_idx = _get_cable_device_index()

        if cable_idx is not None:
            print(f"[WhatsApp Monitor] Speaking on CABLE device #{cable_idx}")
            sd.play(data, samplerate=samplerate, device=cable_idx)
            sd.wait()
        else:
            print("[WhatsApp Monitor] CABLE not found, playing on default device")
            sd.play(data, samplerate=samplerate)
            sd.wait()

    except Exception as e:
        print(f"[WhatsApp Monitor] TTS error: {e}")


# ─── Window detection ─────────────────────────────────────────────────────────

def _enum_windows():
    """يجيب كل الـ windows الموجودة."""
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                cls_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, cls_buf, 256)
                cls_name = cls_buf.value
                results.append((hwnd, title, cls_name))
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return results


def _detect_incoming_call():
    """
    يكتشف المكالمة الواردة من WhatsApp.
    يرجع اسم المتصل أو None.
    """
    try:
        windows = _enum_windows()
        for hwnd, title, cls_name in windows:
            title_lower = title.lower()

            call_keywords = [
                "incoming voice call", "incoming video call", "whatsapp call",
                "مكالمة واردة", "مكالمة صوتية", "مكالمة فيديو", "يتصل بك",
                "voice call", "video call"
            ]

            is_whatsapp = "whatsapp" in title_lower or "whatsapp" in cls_name.lower()

            for kw in call_keywords:
                if kw in title_lower:
                    name = _extract_name_from_title(title)
                    return name or "شخص ما"

            if is_whatsapp:
                loose_keywords = ["ringing", "calling", "arama", "مكالمة", "incoming"]
                for kw in loose_keywords:
                    if kw in title_lower:
                        name = _extract_name_from_title(title)
                        return name or "شخص ما"

    except Exception as e:
        print(f"[WhatsApp Monitor] Detection error: {e}")

    return None


def _extract_name_from_title(title):
    """يستخرج اسم المتصل من عنوان الـ window."""
    clean = re.sub(r'\(\d+\)', '', title)

    remove_list = [
        "WhatsApp", "whatsapp", "- ", "Incoming voice call",
        "Incoming video call", "incoming call", "Ringing", "whatsapp call",
        "مكالمة صوتية", "مكالمة فيديو", "مكالمة واردة", "يتصل بك", "voice call"
    ]

    for r in remove_list:
        clean = clean.replace(r, "").replace(r.lower(), "")

    clean = clean.strip(" -·|:()[]")
    return clean if clean and len(clean) > 1 else None


# ─── Auto Answer ──────────────────────────────────────────────────────────────

def _answer_whatsapp_call():
    """
    يرد على المكالمة الواردة في WhatsApp Desktop.
    يدور على زرار الرد ويضغطه.
    """
    try:
        # استخدم pywinauto عشان يضغط زرار الرد
        from pywinauto import Application, findwindows

        # دور على WhatsApp window
        handles = findwindows.find_windows(class_name="WinUIDesktopWin32WindowClass")
        if not handles:
            print("[WhatsApp Monitor] WhatsApp window not found")
            return False

        app = Application(backend="uia").connect(handle=handles[0])
        wa_window = app.window(handle=handles[0])

        # حاول تضغط زرار الرد — WhatsApp بيسميه "Answer" أو "Accept"
        try:
            answer_btn = wa_window.child_window(title_re="(?i)answer|accept|رد|قبول", control_type="Button")
            answer_btn.click_input()
            print("[WhatsApp Monitor] Clicked answer button via pywinauto")
            return True
        except Exception:
            pass

        # Fallback: pyautogui — WhatsApp بيظهر زرار الرد في مكان ثابت تقريباً
        # خد screenshot وحدد مكانه
        import pyautogui
        screen_w, screen_h = pyautogui.size()

        # زرار الرد عادةً في وسط أسفل الشاشة في الـ notification
        # جرب نضغط Enter كـ shortcut للرد
        pyautogui.press("enter")
        print("[WhatsApp Monitor] Pressed Enter to answer call")
        return True

    except Exception as e:
        print(f"[WhatsApp Monitor] Answer error: {e}")
        return False


# ─── Monitor loop ─────────────────────────────────────────────────────────────

def _monitor_loop():
    """Loop بيشتغل في الـ background ويراقب المكالمات كل ثانية."""
    global _current_caller, _call_detected_time, _monitor_running, _call_active

    last_caller = None
    cooldown = 30

    print("[WhatsApp Monitor] Started")

    while _monitor_running:
        try:
            caller = _detect_incoming_call()

            if caller:
                now = time.time()

                if caller != last_caller or (now - _call_detected_time) > cooldown:
                    _current_caller = caller
                    _call_detected_time = now
                    last_caller = caller
                    _call_active = True

                    print(f"[WhatsApp Monitor] Incoming call from: {caller}")

                    if _ui_ref:
                        _ui_ref.write_log(f"SYS: Incoming WhatsApp call from {caller}")

                    # أبلّغ ATLAS
                    if _speak_fn:
                        _speak_fn(f"سيدي، {caller} يتصل بك. هل أرد عليه؟")

            else:
                if last_caller and (time.time() - _call_detected_time) > 45:
                    last_caller = None
                    _current_caller = None
                    _call_active = False

        except Exception as e:
            print(f"[WhatsApp Monitor] Error: {e}")

        time.sleep(1)

    print("[WhatsApp Monitor] Stopped")


# ─── Public API ───────────────────────────────────────────────────────────────

def get_current_caller():
    return _current_caller


def start_monitor(speak_fn=None, ui=None):
    global _monitor_thread, _monitor_running, _speak_fn, _ui_ref

    if _monitor_running:
        return

    if not _has_runtime_dependencies():
        print("[WhatsApp Monitor] Monitor disabled: optional dependencies are missing.")
        return

    _speak_fn = speak_fn
    _ui_ref = ui
    _monitor_running = True

    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()


def stop_monitor():
    global _monitor_running
    _monitor_running = False


def handle_whatsapp_call(parameters, player=None, speak=None):
    """
    Tool handler لـ ATLAS.
    Actions: check, answer, decline_and_message
    """
    params = parameters or {}
    action = params.get("action", "check").lower()
    message = params.get("message", "").strip()

    caller = get_current_caller()

    if action == "check":
        if caller:
            return f"{caller} is currently calling you on WhatsApp."
        return "No incoming WhatsApp call detected."

    elif action == "answer":
        if not caller:
            return "No incoming call detected, sir."

        # رد على المكالمة
        answered = _answer_whatsapp_call()
        if not answered:
            return "Could not answer the call, sir."

        time.sleep(2.0)  # انتظر المكالمة تفتح

        # قول الرسالة على الـ CABLE
        if not message:
            message = f"مرحباً، صاحب الرقم مشغول الآن. سيعاود الاتصال بك لاحقاً."

        speak_on_cable(message)

        if speak:
            speak(f"رديت على {caller} وقلتله الرسالة.")

        return f"Answered call from {caller} and delivered message via VB-Cable."

    elif action in ("decline", "decline_and_message"):
        if not caller:
            caller = params.get("caller_name", "").strip()
        if not caller:
            return "No caller detected, sir."

        if not message:
            message = "Sorry, I'm busy right now. I'll call you back."

        # ارفض المكالمة وابعت رسالة نصية
        try:
            pyautogui.press("esc")
            time.sleep(0.5)
        except Exception:
            pass

        if speak:
            speak(f"Declined call from {caller}.")

        return f"Declined call from {caller}."

    return f"Unknown action: {action}"
