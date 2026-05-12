# setup.py — ATLAS one-time setup script
import subprocess
import sys
import os
from pathlib import Path

OK   = "✅"
FAIL = "❌"
INFO = "🔧"

def run(label: str, cmd: list, fatal: bool = True) -> bool:
    print(f"\n{INFO}  {label}...")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"{FAIL}  {label} failed (exit {result.returncode})")
        if fatal:
            sys.exit(result.returncode)
        return False
    print(f"{OK}  {label} done.")
    return True


def check_python_version(required=(3, 10)):
    v = sys.version_info
    print(f"{INFO}  Python {v.major}.{v.minor}.{v.micro}", end="")
    if (v.major, v.minor) < required:
        print(f"\n{FAIL}  Python {required[0]}.{required[1]}+ is required.")
        sys.exit(1)
    print(f"  (>= {required[0]}.{required[1]} ✓)")


def ensure_pip_updated():
    run("Upgrading pip", [sys.executable, "-m", "pip", "install", "--upgrade", "pip"], fatal=False)


def install_requirements():
    req = Path("requirements.txt")
    if not req.exists():
        print(f"{FAIL}  requirements.txt not found — skipping.")
        return
    run("Installing requirements", [sys.executable, "-m", "pip", "install", "-r", str(req)])


def install_playwright():
    # Check if playwright is installed first
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "--version"],
        capture_output=True
    )
    if result.returncode != 0:
        print(f"\n{FAIL}  playwright not found — make sure it is in requirements.txt")
        return
    run("Installing Playwright browsers (Chromium)", [sys.executable, "-m", "playwright", "install", "chromium"])
    run("Installing Playwright system deps",         [sys.executable, "-m", "playwright", "install-deps", "chromium"], fatal=False)


def check_config():
    cfg = Path("config") / "api_keys.json"
    if not cfg.exists():
        print(f"\n{INFO}  Creating config/api_keys.json template...")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            '{\n'
            '    "provider": "gemini",\n'
            '    "gemini_api_key": "YOUR_API_KEY_HERE",\n'
            '    "openrouter_api_key": "",\n'
            '    "openrouter_model": "openai/gpt-4o-mini",\n'
            '    "camera_index": 0\n'
            '}\n',
            encoding="utf-8"
        )
        print(f"{OK}  config/api_keys.json created — fill in your Gemini or OpenRouter API key.")
    else:
        import json
        try:
            data = json.loads(cfg.read_text())
            provider = str(data.get("provider", "gemini")).strip().lower()
            if provider == "openrouter":
                key = str(data.get("openrouter_api_key", "")).strip()
            else:
                key = str(data.get("gemini_api_key", "")).strip()

            if "YOUR_API_KEY" in key or not key.strip():
                print(f"\n⚠️   config/api_keys.json exists but the selected provider API key is not set!")
            else:
                print(f"\n{OK}  {provider} API key found in config/api_keys.json.")
        except Exception:
            print(f"\n⚠️   config/api_keys.json is malformed — please check it.")


def check_launch_assets():
    required = [
        Path("main.py"),
        Path("ui.py"),
        Path("core") / "prompt.txt",
        Path("face_v1.png"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print(f"\n{FAIL}  Missing launch assets: {', '.join(missing)}")
        sys.exit(1)
    print(f"\n{OK}  Launch assets verified.")


def main():
    print("=" * 50)
    print("  ATLAS — Setup")
    print("=" * 50)

    # Change to the script's directory so relative paths work
    os.chdir(Path(__file__).parent)

    check_python_version()
    ensure_pip_updated()
    install_requirements()
    install_playwright()
    check_launch_assets()
    check_config()

    print("\n" + "=" * 50)
    print(f"{OK}  Setup complete!")
    print("   Run:  python main.py")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
