import importlib
import json
import os
import sys
from pathlib import Path


def print_status(component, status, msg=""):
    color = "\033[92m" if status == "OK" else "\033[93m" if status == "WARN" else "\033[91m"
    reset = "\033[0m"
    print(f"[{color}{status}{reset}] {component:<25} {msg}")


def load_api_config(base_dir: Path) -> dict:
    api_path = base_dir / "config" / "api_keys.json"
    if not api_path.exists():
        print_status("API Config", "FAIL", "config/api_keys.json is missing")
        return {}

    try:
        data = json.loads(api_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print_status("API Config", "FAIL", "config/api_keys.json is invalid JSON")
        return {}

    provider = str(data.get("provider", "gemini")).strip().lower()
    key_name = "openrouter_api_key" if provider == "openrouter" else "gemini_api_key"
    key = str(data.get(key_name, "")).strip()
    if not key or "YOUR_API_KEY_HERE" in key:
        print_status("API Config", "FAIL", f"{provider} API key is missing or still set to the template value")
    else:
        print_status("API Config", "OK", f"{provider} API key detected")

    return data


def check_imports(group_name: str, modules: list[tuple[str, str]], fail_on_missing: bool):
    missing = []
    for label, module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(label)

    if not missing:
        print_status(group_name, "OK", "All dependencies available")
        return

    status = "FAIL" if fail_on_missing else "WARN"
    print_status(group_name, status, f"Missing: {', '.join(missing)}")


def check_audio():
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if inputs:
            print_status("Audio Input", "OK", f"Mic: {inputs[0]['name']}")
        else:
            print_status("Audio Input", "FAIL", "No input device found")

        cable = any("cable" in d["name"].lower() or "vb-audio" in d["name"].lower() for d in devices)
        if cable:
            print_status("VB-Cable", "OK", "Virtual audio cable detected")
        else:
            print_status("VB-Cable", "WARN", "Optional, used by WhatsApp call auto-answer")
    except Exception as exc:
        print_status("Audio Systems", "FAIL", str(exc))


def check_camera():
    try:
        import cv2

        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            print_status("Visual Sensor", "OK", "Webcam accessible")
            cap.release()
        else:
            print_status("Visual Sensor", "WARN", "Webcam not detected")
    except Exception as exc:
        print_status("Visual Systems", "WARN", f"OpenCV test failed: {exc}")


def check_assets(base_dir: Path):
    required_paths = [
        base_dir / "main.py",
        base_dir / "ui.py",
        base_dir / "core" / "prompt.txt",
        base_dir / "face_v1.png",
    ]
    missing = [str(path.relative_to(base_dir)) for path in required_paths if not path.exists()]
    if missing:
        print_status("Project Assets", "FAIL", f"Missing: {', '.join(missing)}")
    else:
        print_status("Project Assets", "OK", "Launch assets present")


def run_diagnostics():
    print("=" * 50)
    print("      A.T.L.A.S. SYSTEM DIAGNOSTICS      ")
    print("=" * 50)

    base_dir = Path(__file__).resolve().parent

    load_api_config(base_dir)
    check_assets(base_dir)
    check_imports(
        "Core Dependencies",
        [
            ("sounddevice", "sounddevice"),
            ("google.genai", "google.genai"),
            ("Pillow", "PIL"),
            ("pyautogui", "pyautogui"),
            ("keyboard", "keyboard"),
            ("playwright", "playwright"),
        ],
        fail_on_missing=True,
    )
    check_imports(
        "WhatsApp Extras",
        [
            ("edge-tts", "edge_tts"),
            ("soundfile", "soundfile"),
            ("pywinauto", "pywinauto"),
        ],
        fail_on_missing=False,
    )
    check_audio()
    check_camera()

    print("=" * 50)
    print("Diagnostic complete.")


if __name__ == "__main__":
    if sys.platform == "win32":
        os.system("color")
    run_diagnostics()
