import os
import time
import requests
import threading
import json
import subprocess
import tempfile
from pathlib import Path
import sys

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

API_CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"

def _get_api_keys():
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _get_telegram_token():
    return _get_api_keys().get("telegram_bot_token", "").strip()

def _get_gemini_token():
    return _get_api_keys().get("gemini_api_key", "").strip()

_monitor_active = False
_telegram_thread = None
_last_update_id = 0
_chat_ids = set()
_chat_modes = {}  # Tracks whether the user last sent "text" or "voice"

def start_telegram_monitor(speak_fn, ui):
    global _monitor_active, _telegram_thread
    if _monitor_active:
        return
    token = _get_telegram_token()
    if not token or "YOUR_" in token:
        print("[Telegram] No valid bot token found in config. Skipping Telegram integration.")
        return
    
    _monitor_active = True
    _telegram_thread = threading.Thread(target=_telegram_loop, args=(token, speak_fn, ui), daemon=True)
    _telegram_thread.start()
    print("[Telegram] Monitor started. Ready to control your life 24/7.")

def _transcribe_audio(file_path):
    from google import genai
    gemini_key = _get_gemini_token()
    if not gemini_key:
        return ""
    try:
        client = genai.Client(api_key=gemini_key)
        uploaded_file = client.files.upload(file=file_path)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                "Transcribe this audio clip exactly. Do not add any extra text or answer any questions, just write down what was spoken.",
                uploaded_file
            ]
        )
        return response.text.strip()
    except Exception as e:
        print(f"[Telegram] Transcription error: {e}")
        return ""

def _telegram_loop(token, speak_fn, ui):
    global _last_update_id
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    
    original_write_log = ui.write_log

    def hooked_write_log(text: str, tag=None):
        if tag is not None:
            original_write_log(text, tag)
        else:
            original_write_log(text)
        if text.startswith("Atlas: "):
            msg = text[7:].strip()
            
            # Send to Telegram
            for chat_id in list(_chat_ids):
                # Send text first
                try:
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": msg
                    }, timeout=5)
                except Exception:
                    pass
                
                
                # Send voice memo ONLY if the user's last message was voice
                if _chat_modes.get(chat_id) == "voice":
                    try:
                        fd, temp_path = tempfile.mkstemp(suffix=".mp3")
                        os.close(fd)
                        
                        import re
                        if re.search("[\u0600-\u06FF]", msg):
                            voice = "ar-EG-ShakirNeural"
                        else:
                            lang = getattr(ui, "language", "English")
                            if lang == "Arabic":
                                voice = "ar-EG-ShakirNeural"
                            elif lang == "Turkish":
                                voice = "tr-TR-AhmetNeural"
                            else:
                                voice = "en-US-ChristopherNeural"
                            
                        subprocess.run(
                            [sys.executable, "-m", "edge_tts", "--voice", voice, "--text", msg, "--write-media", temp_path],
                            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        
                        with open(temp_path, "rb") as f:
                            requests.post(f"https://api.telegram.org/bot{token}/sendVoice", data={"chat_id": chat_id}, files={"voice": f}, timeout=15)
                        
                        os.remove(temp_path)
                    except Exception as e:
                        print(f"[Telegram] Failed to send voice: {e}")

    ui.write_log = hooked_write_log
    
    print("[Telegram] Listening for 24/7 commands...")
    while _monitor_active:
        try:
            resp = requests.get(url, params={"offset": _last_update_id + 1, "timeout": 5}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("result", []):
                    _last_update_id = item["update_id"]
                    msg = item.get("message")
                    if not msg:
                        continue
                        
                    chat_id = msg["chat"]["id"]
                    _chat_ids.add(chat_id)
                    
                    user_text = ""
                    
                    if "text" in msg:
                        user_text = msg["text"]
                        _chat_modes[chat_id] = "text"
                    elif "voice" in msg:
                        _chat_modes[chat_id] = "voice"
                        file_id = msg["voice"]["file_id"]
                        file_info_resp = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}).json()
                        if file_info_resp.get("ok"):
                            file_path = file_info_resp["result"]["file_path"]
                            file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                            voice_data = requests.get(file_url).content
                            
                            fd, local_path = tempfile.mkstemp(suffix=".oga")
                            os.close(fd)
                            with open(local_path, "wb") as f:
                                f.write(voice_data)
                                
                            ui.write_log("SYS: Transcribing Telegram voice memo...")
                            user_text = _transcribe_audio(local_path)
                            os.remove(local_path)
                    
                    if user_text:
                        print(f"[Telegram] Received: {user_text}")
                        ui.write_log(f"You (Telegram): {user_text}")
                        speak_fn(user_text)
                        
        except requests.exceptions.RequestException:
            pass
        except Exception as e:
            print(f"[Telegram] Loop error: {e}")
            time.sleep(2)
        
        time.sleep(1)

def stop_telegram_monitor():
    global _monitor_active
    _monitor_active = False
