# actions/cmd_control.py
# Natural-language → CMD/PowerShell executor for ATLAS.
# Enhancements: command cache, AI output summarizer, fix double-execution,
# expanded hardcoded map, configurable timeout, smarter PS detection.

import subprocess
import sys
import json
import re
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_platform() -> str:
    if sys.platform == "win32":  return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"


# ─── Hardcoded command map ────────────────────────────────────────────────────
# Keeps common tasks instant — zero API calls.

_HOME = Path.home()

WIN_COMMAND_MAP = [
    # Storage
    (["disk space", "disk usage", "storage", "free space", "c drive space"],
     "wmic logicaldisk get caption,freespace,size /format:list", False),
    (["large files", "biggest files", "largest files"],
     'powershell Get-ChildItem C:\\ -Recurse -ErrorAction SilentlyContinue '
     '| Sort-Object Length -Descending | Select-Object -First 10 FullName,Length | Format-Table', False),

    # Processes / resources
    (["running processes", "list processes", "show processes", "active processes", "tasklist"],
     "tasklist /fo table", False),
    (["cpu usage", "processor usage"],
     "wmic cpu get loadpercentage", False),
    (["memory usage", "ram usage"],
     "wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", False),
    (["gpu", "graphics card", "video card"],
     "wmic path win32_VideoController get caption,AdapterRAM,DriverVersion", False),

    # Network
    (["ip address", "my ip", "network info", "ipconfig"],
     "ipconfig /all", False),
    (["ping", "internet connection", "connected to internet", "check internet"],
     "ping -n 4 google.com", False),
    (["open ports", "listening ports", "netstat"],
     "netstat -an | findstr LISTENING", False),
    (["wifi networks", "available wifi", "wireless networks"],
     "netsh wlan show networks", False),
    (["wifi password", "saved wifi"],
     'powershell (netsh wlan show profiles) -match "All User Profile" '
     '| % {netsh wlan show profile ($_ -split ":")[1].Trim() key=clear}', False),
    (["external ip", "public ip", "wan ip"],
     "powershell (Invoke-WebRequest -Uri 'https://api.ipify.org').Content", False),

    # System info
    (["system info", "computer info", "hardware info", "pc info", "specs"],
     "systeminfo", False),
    (["windows version", "os version", "build number"],
     "ver", False),
    (["battery", "battery level", "power status"],
     "powershell (Get-WmiObject Win32_Battery).EstimatedChargeRemaining", False),
    (["uptime", "how long running", "system uptime"],
     "powershell (Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime", False),
    (["environment variables", "env vars", "path variable"],
     "set", False),

    # Files / folders
    (["desktop files", "files on desktop"],
     f'dir "{_HOME / "Desktop"}"', False),
    (["downloads", "files in downloads"],
     f'dir "{_HOME / "Downloads"}"', False),
    (["documents", "files in documents"],
     f'dir "{_HOME / "Documents"}"', False),
    (["temp files", "temp folder"],
     "dir %TEMP%", False),

    # Software
    (["installed programs", "installed software", "installed apps"],
     "wmic product get name,version /format:table", False),

    # Time / date
    (["current time", "what time", "system time"],
     "time /t", False),
    (["current date", "what date", "system date"],
     "date /t", False),
]


def _find_hardcoded(task: str) -> "str | None":
    task_lower = task.lower()

    # Notepad / text files
    if "notepad" in task_lower or any(ext in task_lower for ext in [".txt", ".log", ".md", ".csv"]):
        file_match = re.search(
            r'["\']?([\S]+\.(?:txt|log|md|csv|json|xml))["\']?', task, re.IGNORECASE
        )
        if file_match:
            filename = file_match.group(1)
            filepath = Path(filename) if Path(filename).is_absolute() else _HOME / "Desktop" / filename
            return f'notepad "{filepath}"'
        if "notepad" in task_lower:
            return "notepad"

    # pip install
    pip_match = re.search(r"install\s+([\w\-]+)", task_lower)
    if pip_match:
        return f"pip install {pip_match.group(1)}"

    # Kill / end process
    kill_match = re.search(r"(?:kill|end|close|terminate)\s+(?:process\s+)?(\w[\w.]+)", task_lower)
    if kill_match:
        proc = kill_match.group(1)
        return f"taskkill /F /IM {proc}.exe"

    # Keyword map
    for keywords, command, _ in WIN_COMMAND_MAP:
        if any(kw in task_lower for kw in keywords):
            return command

    return None


# ─── AI command generation with cache ────────────────────────────────────────
# Same task string → same command, no repeated API call.

_CMD_CACHE: dict[str, str] = {}


def _ask_gemini(task: str) -> str:
    if task in _CMD_CACHE:
        print(f"[CMD] 💾 Cache hit for: {task[:60]}")
        return _CMD_CACHE[task]

    try:
        import google.generativeai as genai
        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-2.5-flash-lite")

        prompt = (
            f"Convert this request to a SINGLE Windows CMD or PowerShell command.\n"
            f"Output ONLY the raw command — no explanation, no markdown, no backticks.\n"
            f"Prefer CMD over PowerShell where possible.\n"
            f"If the request is unsafe or impossible, output exactly: UNSAFE\n\n"
            f"Request: {task}\n\nCommand:"
        )
        response = model.generate_content(prompt)
        command  = response.text.strip().strip("`").strip()

        # Strip accidental markdown fences
        if command.startswith("```"):
            lines   = command.split("\n")
            command = "\n".join(lines[1:-1]).strip()

        if command and command != "UNSAFE" and not command.startswith("ERROR"):
            _CMD_CACHE[task] = command

        return command

    except Exception as e:
        return f"ERROR: {e}"


# ─── Safety filter ────────────────────────────────────────────────────────────

BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b", r"\brmdir\s+/s\b", r"\bdel\s+/[fqs]",
    r"\bformat\b", r"\bdiskpart\b", r"\bfdisk\b",
    r"\breg\s+(delete|add)\b", r"\bbcdedit\b",
    r"\bnet\s+localgroup\b",
    r"\bshutdown\b", r"\brestart-computer\b",
    r"\bstop-process\b", r"\bkill\s+-9\b", r"\btaskkill\b",
    r"\beval\b", r"\b__import__\b",
]
_BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)


def _is_safe(command: str) -> "tuple[bool, str]":
    match = _BLOCKED_RE.search(command)
    if match:
        return False, f"Blocked pattern: '{match.group()}'"
    return True, "OK"


# ─── Execution helpers ────────────────────────────────────────────────────────

def _is_powershell(cmd: str) -> bool:
    low = cmd.strip().lower()
    return (
        low.startswith("powershell")
        or low.startswith("ps ")
        or "invoke-" in low
        or "get-" in low
        or "set-" in low
        or "| select" in low
        or "| sort" in low
        or "| where" in low
    )


def _run_silent(command: str, timeout: int = 20) -> str:
    try:
        platform = _get_platform()

        if platform == "windows":
            if _is_powershell(command):
                # Strip leading 'powershell' keyword so we can pass via -Command
                ps_body = re.sub(r"^powershell\s*", "", command, flags=re.IGNORECASE).strip().strip('"')
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_body],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            else:
                result = subprocess.run(
                    ["cmd", "/c", command],
                    capture_output=True, text=True,
                    encoding="cp1252", errors="replace",
                    timeout=timeout, cwd=str(_HOME),
                )
        else:
            shell = "/bin/zsh" if platform == "macos" else "/bin/bash"
            result = subprocess.run(
                command, shell=True, executable=shell,
                capture_output=True, text=True,
                errors="replace", timeout=timeout, cwd=str(_HOME),
            )

        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:  return out[:2000]
        if err:  return f"[stderr]: {err[:500]}"
        return "Command executed with no output."

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Execution error: {e}"


def _run_visible(command: str) -> None:
    """Opens a terminal window — does NOT capture output (avoids double execution)."""
    try:
        platform = _get_platform()
        if platform == "windows":
            subprocess.Popen(
                f'cmd /k "{command}"',
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        elif platform == "macos":
            subprocess.Popen(["osascript", "-e",
                f'tell application "Terminal" to do script "{command}"'])
        else:
            for term in ["gnome-terminal", "xterm", "konsole"]:
                try:
                    subprocess.Popen([term, "--", "bash", "-c", f"{command}; exec bash"])
                    break
                except FileNotFoundError:
                    continue
    except Exception as e:
        print(f"[CMD] ⚠️ Terminal open failed: {e}")


# ─── AI output summarizer ─────────────────────────────────────────────────────
_SUMMARIZE_THRESHOLD = 300      # characters; below this, just return raw output


def _summarize_output(task: str, raw_output: str) -> str:
    """
    When output is long or noisy, ask the AI to distill it into a
    clean, human-readable answer. Falls back to raw output on any error.
    """
    if len(raw_output) < _SUMMARIZE_THRESHOLD:
        return raw_output

    try:
        import google.generativeai as genai
        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-2.5-flash-lite")

        prompt = (
            f"The user asked: \"{task}\"\n\n"
            f"Here is the raw command output:\n{raw_output[:1500]}\n\n"
            f"Summarize ONLY the relevant information in 2–4 clear, concise sentences. "
            f"No markdown. No caveats."
        )
        response = model.generate_content(prompt)
        summary = response.text.strip()
        return summary if summary else raw_output

    except Exception:
        return raw_output


# ─── Public entry point ───────────────────────────────────────────────────────

def cmd_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    task    = params.get("task", "").strip()
    command = params.get("command", "").strip()
    visible = params.get("visible", True)
    timeout = int(params.get("timeout", 20))
    summarize = params.get("summarize", True)      # Set False to always return raw output

    if not task and not command:
        return "Please describe what you want to do, sir."

    # ── Resolve command ───────────────────────────────────────────────────────
    if not command:
        command = _find_hardcoded(task)
        if command:
            print(f"[CMD] ⚡ Hardcoded: {command[:80]}")
        else:
            print(f"[CMD] 🤖 AI generation for: {task}")
            command = _ask_gemini(task)
            print(f"[CMD] ✅ Generated: {command[:80]}")

            if command == "UNSAFE":
                return "I cannot generate a safe command for that request, sir."
            if command.startswith("ERROR:"):
                return f"Could not generate command: {command}"

    # ── Safety check ──────────────────────────────────────────────────────────
    safe, reason = _is_safe(command)
    if not safe:
        return f"Blocked for safety: {reason}"

    if player:
        player.write_log(f"[CMD] {command[:60]}")

    # ── GUI launchers — fire-and-forget, no output ────────────────────────────
    if any(x in command.lower() for x in ["notepad", "explorer", "start "]):
        subprocess.Popen(command, shell=True)
        return f"Opened: {command}"

    # ── Visible mode — show terminal; capture separately for Atlas ────────────
    if visible:
        _run_visible(command)
        output = _run_silent(command, timeout=timeout)
        if summarize and task:
            output = _summarize_output(task, output)
        return f"Terminal opened.\n\n{output}"

    # ── Silent mode — return output directly ──────────────────────────────────
    output = _run_silent(command, timeout=timeout)
    if summarize and task:
        output = _summarize_output(task, output)
    return output