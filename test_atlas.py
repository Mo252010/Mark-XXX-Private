import os
import sys
import time
import json
import traceback

def print_status(name, success, error_msg=""):
    if success:
        print(f" {name:.<35} PASSED")
    else:
        print(f" {name:.<35} FAILED ({error_msg})")

def run_tests():
    print("\n" + "="*50)
    print(" ATLAS System Capabilities Test Suite ")
    print("="*50 + "\n")
    
    # 1. API Keys & Config
    api_path = "config/api_keys.json"
    keys_loaded = False
    tg_token = ""
    try:
        if os.path.exists(api_path):
            with open(api_path, "r", encoding="utf-8") as f:
                keys = json.load(f)
                if "gemini_api_key" in keys and "telegram_bot_token" in keys:
                    print_status("API Config Loaded", True)
                    keys_loaded = True
                    tg_token = keys.get("telegram_bot_token", "").strip()
                else:
                    print_status("API Config Loaded", False, "Missing keys in JSON")
        else:
            print_status("API Config Loaded", False, "config/api_keys.json not found")
    except Exception as e:
        print_status("API Config Loaded", False, str(e))

    # 2. Telegram Bot Connection
    if keys_loaded and tg_token and not tg_token.startswith("YOUR_"):
        try:
            import requests
            resp = requests.get(f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5)
            if resp.status_code == 200:
                bot_name = resp.json().get("result", {}).get("username", "Unknown")
                print_status(f"Telegram API (@{bot_name})", True)
            else:
                print_status("Telegram API", False, f"HTTP {resp.status_code}")
        except Exception as e:
            print_status("Telegram API", False, "Network error")
    else:
        print_status("Telegram API", False, "Skipped (No valid token found)")

    # 3. Google GenAI SDK Import
    try:
        from google import genai
        print_status("Google GenAI Core", True)
    except ImportError:
        print_status("Google GenAI Core", False, "SDK not installed properly")

    # 4. Text-to-Speech Engine (Edge-TTS)
    try:
        import subprocess
        test_file = "test_audio_delete_me.mp3"
        result = subprocess.run(
            [sys.executable, "-m", "edge_tts", "--text", "Test", "--write-media", test_file],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and os.path.exists(test_file):
            print_status("Edge-TTS Voice Engine", True)
            os.remove(test_file)
        else:
            print_status("Edge-TTS Voice Engine", False, "Audio file not created")
    except Exception as e:
        print_status("Edge-TTS Voice Engine", False, str(e))

    # 5. Microphone Hardware Detection
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        has_input = any(d['max_input_channels'] > 0 for d in devices)
        if has_input:
            print_status("Microphone Hardware", True)
        else:
            print_status("Microphone Hardware", False, "No input devices found in OS")
    except Exception as e:
        print_status("Microphone Hardware", False, str(e))

    # 6. Computer Vision & UI Automation Libraries
    try:
        import pyautogui
        import cv2
        import numpy
        import mss
        print_status("Vision & UI Automation", True)
    except ImportError as e:
        print_status("Vision & UI Automation", False, f"Missing {e.name}")

    print("\n" + "="*50)
    print(" Test Suite Completed! ")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_tests()
