import asyncio
import threading
import json
import re
import sys
import time
import traceback
import socket
import random
import uuid
import keyboard
from pathlib import Path

# Fix Windows console encoding for emoji/unicode in print() calls
sys.stdout.reconfigure(errors='replace')
sys.stderr.reconfigure(errors='replace')

import sounddevice as sd
import requests
from google import genai
from google.genai import types
from ui import AtlasUI
from memory.memory_manager import load_memory, update_memory, format_memory_for_prompt, log_interaction

from agent.task_queue import get_queue

# --- Action modules ---
from actions.flight_finder    import flight_finder
from actions.open_app         import open_app
from actions.weather_report   import weather_action
from actions.send_message     import send_message
from actions.reminder         import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor import screen_process
from actions.youtube_video    import youtube_video
from actions.cmd_control      import cmd_control
from actions.desktop          import desktop_control
from actions.browser_control  import browser_control
from actions.file_controller  import file_controller
from actions.code_helper      import code_helper
from actions.dev_agent        import dev_agent
from actions.web_search       import web_search as web_search_action
from actions.computer_control import computer_control
from actions.steam_control    import steam_action
from actions.routines         import graphic_workspace
from actions.whatsapp_monitor import start_monitor, stop_monitor, handle_whatsapp_call
from actions.whatsapp_checker import check_whatsapp_messages
from actions.telegram_monitor import start_telegram_monitor, stop_telegram_monitor
from actions.workspace_agent import workspace_agent

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

# Gemini Live model for voice mode
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ---------------------------------------------------------------------------
# Swappable AI Provider — set "provider" in config/api_keys.json
#
# Supported values:
#   "gemini"      — Google Gemini Live (voice + chat, default)
#   "openrouter"  — OpenRouter.ai (chat only)
#   "groq"        — Groq (chat only)
#   "ollama"      — Local Ollama (chat only)
#   "custom"      — Any OpenAI-compatible endpoint (chat only)
#
# For non-Gemini providers, set these in api_keys.json:
#   "chat_api_url"   — Full base URL (e.g. "https://api.groq.com/openai/v1/chat/completions")
#   "chat_api_key"   — API key for that provider
#   "chat_model"     — Model name (e.g. "llama-3.3-70b-versatile")
# ---------------------------------------------------------------------------

# Default fallbacks for known providers
_PROVIDER_DEFAULTS = {
    "openrouter": {
        "url":   "https://openrouter.ai/api/v1/chat/completions",
        "model": "openai/gpt-4o-mini",
    },
    "groq": {
        "url":   "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
    },
    "ollama": {
        "url":   "http://localhost:11434/v1/chat/completions",
        "model": "llama3",
    },
}


def _pick_output_device(preferred_idx=None):
    try:
        devices = sd.query_devices()
    except Exception:
        return preferred_idx

    if preferred_idx is not None:
        try:
            info = sd.query_devices(preferred_idx)
            if info.get("max_output_channels", 0) > 0:
                return preferred_idx
        except Exception:
            pass

    try:
        default_out = sd.default.device[1]
        info = sd.query_devices(default_out)
        name = info.get("name", "").lower()
        if info.get("max_output_channels", 0) > 0 and "vb-audio" not in name and "cable" not in name:
            return default_out
    except Exception:
        pass

    preferred_words = ("speaker", "headphone", "headset", "realtek")
    for i, dev in enumerate(devices):
        name = dev.get("name", "").lower()
        if dev.get("max_output_channels", 0) > 0 and any(word in name for word in preferred_words):
            if "vb-audio" not in name and "cable" not in name:
                return i

    for i, dev in enumerate(devices):
        if dev.get("max_output_channels", 0) > 0:
            return i

    return preferred_idx


# ---------------------------------------------------------------------------
# Config loading — cached to avoid repeated disk reads
# ---------------------------------------------------------------------------

_config_cache = None
_config_lock  = threading.Lock()


def _load_api_config(required: bool = True) -> dict:
    """Load and cache config/api_keys.json. Thread-safe, read once."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            result = data if isinstance(data, dict) else {}
    except FileNotFoundError:
        if required:
            raise RuntimeError("Missing config/api_keys.json. Add your provider API key to launch ATLAS.")
        result = {}
    except json.JSONDecodeError:
        if required:
            raise RuntimeError("config/api_keys.json is invalid JSON. Fix it before launching ATLAS.")
        result = {}
    with _config_lock:
        _config_cache = result
    return result


def reload_config():
    """Force re-read of config from disk (e.g. after user edits api_keys.json)."""
    global _config_cache
    with _config_lock:
        _config_cache = None
    return _load_api_config(required=False)


def _get_provider() -> str:
    """Return the active provider name from config."""
    provider = str(_load_api_config(required=False).get("provider", "gemini")).strip().lower()
    valid = {"gemini", "openrouter", "groq", "ollama", "custom"}
    return provider if provider in valid else "gemini"


def _get_api_key() -> str:
    """Return the Gemini API key (required for Gemini provider)."""
    key = str(_load_api_config().get("gemini_api_key", "")).strip()
    if not key or "YOUR_API_KEY_HERE" in key:
        raise RuntimeError("Gemini API key is not configured. Update config/api_keys.json.")
    return key


def _get_chat_config() -> dict:
    """Return unified chat API config for any OpenAI-compatible provider.

    Returns dict with keys: url, key, model
    """
    cfg = _load_api_config()
    provider = _get_provider()
    defaults = _PROVIDER_DEFAULTS.get(provider, {})

    # Allow explicit overrides in config, fall back to provider defaults
    url   = str(cfg.get("chat_api_url", "") or defaults.get("url", "")).strip()
    key   = str(cfg.get("chat_api_key", "") or cfg.get("openrouter_api_key", "")).strip()
    model = str(cfg.get("chat_model", "") or cfg.get("openrouter_model", "") or defaults.get("model", "")).strip()

    if not url:
        raise RuntimeError(f"No API URL configured for provider '{provider}'. Set 'chat_api_url' in api_keys.json.")
    if provider not in ("ollama",) and (not key or "YOUR_API_KEY_HERE" in key):
        raise RuntimeError(f"API key missing for provider '{provider}'. Set 'chat_api_key' in api_keys.json.")
    if not model:
        raise RuntimeError(f"No model configured for provider '{provider}'. Set 'chat_model' in api_keys.json.")

    return {"url": url, "key": key, "model": model}



def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ATLAS, an advanced AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


def _build_system_prompt(language: str) -> str:
    from datetime import datetime

    memory = load_memory()
    mem_str = format_memory_for_prompt(memory)
    sys_prompt = _load_system_prompt()

    now = datetime.now()
    time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
    time_ctx = (
        f"[CURRENT DATE & TIME]\n"
        f"Right now it is: {time_str}\n"
        f"Use this to calculate exact times for reminders. "
        f"If user says 'in 2 minutes', add 2 minutes to this time.\n\n"
    )

    lang_prompt = f"\n\nIMPORTANT: You must speak and write your responses in {language}.\n\n"
    return time_ctx + (mem_str if mem_str else "") + lang_prompt + sys_prompt


def _openrouter_tools():
    type_map = {
        "STRING": "string",
        "OBJECT": "object",
        "ARRAY": "array",
        "BOOLEAN": "boolean",
        "NUMBER": "number",
        "INTEGER": "integer",
    }

    def convert_schema(value):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key == "type" and isinstance(item, str):
                    out[key] = type_map.get(item, item.lower())
                else:
                    out[key] = convert_schema(item)
            return out
        if isinstance(value, list):
            return [convert_schema(item) for item in value]
        return value

    tools = []
    for tool in TOOL_DECLARATIONS:
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": convert_schema(tool.get("parameters", {"type": "OBJECT", "properties": {}})),
            },
        })
    return tools

_memory_turn_counter  = 0
_memory_turn_lock     = threading.Lock()
_MEMORY_EVERY_N_TURNS = 5
_last_memory_input    = ""


def _update_memory_async(user_text: str, atlas_text: str) -> None:
    """
    Multilingual memory updater.
    Model  : gemini-2.5-flash-lite (lowest cost)
    Stage 1: Quick YES/NO check  → ~5 tokens output
    Stage 2: Full extraction     → only if Stage 1 says YES
    Result : ~80% fewer API calls vs original
    """
    global _memory_turn_counter, _last_memory_input

    with _memory_turn_lock:
        _memory_turn_counter += 1
        current_count = _memory_turn_counter

    if current_count % _MEMORY_EVERY_N_TURNS != 0:
        return

    text = user_text.strip()
    if len(text) < 10:
        return
    if text == _last_memory_input:
        return
    _last_memory_input = text

    try:
        # Use the same google.genai SDK as the rest of the project
        mem_client = genai.Client(api_key=_get_api_key())

        check = mem_client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=(
                f"Does this message contain personal facts about the user "
                f"(name, age, city, job, hobby, relationship, birthday, preference)? "
                f"Reply only YES or NO.\n\nMessage: {text[:300]}"
            ),
        )
        if "YES" not in check.text.upper():
            return

        raw = mem_client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=(
                f"Extract personal facts from this message. Any language.\n"
                f"Return ONLY valid JSON or {{}} if nothing found.\n"
                f"Extract: name, age, birthday, city, job, hobbies, preferences, relationships, language.\n"
                f"Skip: weather, reminders, search results, commands.\n\n"
                f"Format:\n"
                f'{{"identity":{{"name":{{"value":"..."}}}}}}, '
                f'"preferences":{{"hobby":{{"value":"..."}}}}, '
                f'"notes":{{"job":{{"value":"..."}}}}}}\n\n'
                f"Message: {text[:500]}\n\nJSON:"
            ),
        ).text.strip()

        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        if not raw or raw == "{}":
            return

        data = json.loads(raw)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ Updated: {list(data.keys())}")

    except json.JSONDecodeError:
        pass
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")


TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gets real-time weather information for a city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver": {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending | pause | resume | fullscreen | skip_ad | volume_up | volume_down | next (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "ALSO use for repeated actions: 'refresh 10 times', 'reload page 5 times' → action: reload_n, value: 10. "
            "Use for ANY single computer control command — even if repeated N times. "
            "NEVER route simple computer commands to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform (if known). For repeated reload: 'reload_n'"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, number of times, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, finding cheapest products, "
            "booking flights, managing tabs, taking screenshots, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | back | forward | reload | new_tab | close_tab | switch_tab | click | type | scroll | fill_form | smart_click | smart_type | wait_for | get_text | get_url | get_title | extract_links | screenshot | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "google | bing | duckduckgo | youtube (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type/wait_for"},
                "text":        {"type": "STRING", "description": "Text to click, type, or wait_for"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "index":       {"type": "INTEGER", "description": "Tab index for switch_tab"},
                "timeout_ms":  {"type": "INTEGER", "description": "Wait timeout in ms (default: 10000)"},
                "filter_text": {"type": "STRING", "description": "Filter links by this text (extract_links)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders. Use for: listing files, creating/deleting/moving/copying "
            "files, reading file contents, finding files by name or extension, checking disk usage, "
            "organizing the desktop, getting file info."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Runs CMD/terminal commands by understanding natural language. "
            "Use when user wants to: find large files, check disk space, list processes, "
            "get system info, navigate folders, check network, find files by name, "
            "or do ANYTHING in the command line they don't know how to do themselves."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task":    {"type": "STRING", "description": "Natural language description of what to do."},
                "visible": {"type": "BOOLEAN", "description": "Open visible CMD window so user can see. Default: true"},
                "command": {"type": "STRING", "description": "Optional: exact command if already known"},
            },
            "required": ["task"]
        }
    },
    {
        "name": "desktop_control",
        "description": (
            "Controls the desktop. Use for: changing wallpaper, organizing desktop files, "
            "cleaning the desktop, listing desktop contents, or ANY other desktop-related task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language description of any desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": (
            "Writes, edits, explains, runs, or self-builds code files. "
            "Use for ANY coding request: writing a script, fixing a file, "
            "editing existing code, running a file, or building and testing automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do, or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file (full path or filename)"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit / explain / run / build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "workspace_agent",
        "description": (
            "Analyzes a project workspace/directory. Use this to understand a new project, "
            "summarize file structure, identify technologies used, and find entry points. "
            "Essential for onboarding to an existing codebase, just like Claude Cowork."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Path to the project directory (default: current)"},
                "deep": {"type": "BOOLEAN", "description": "Perform a deeper scan of all files. Default: false"}
            },
            "required": []
        }
    },
    {
        "name": "dev_agent",
        "description": (
            "Builds complete multi-file projects from scratch. "
            "Plans structure, writes all files, installs dependencies, "
            "opens VSCode, runs the project, and fixes errors automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks that require MULTIPLE DIFFERENT tools. "
            "Always respond to the user in the language they spoke. "
            "DO NOT use for simple computer commands — use computer_settings for those. "
            "DO NOT use if the task can be done with a single tool call."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what needs to be accomplished"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "Direct computer control: type text, click buttons, use keyboard shortcuts, "
            "scroll, move mouse, take screenshots, fill forms, find elements on screen. "
            "Can generate random data for forms or use user's real info from memory."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate for click/move"},
                "y":           {"type": "INTEGER", "description": "Y coordinate for click/move"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key to press e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "Scroll direction: up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER", "description": "Seconds to wait"},
                "title":       {"type": "STRING", "description": "Window title for focus_window"},
                "description": {"type": "STRING", "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING", "description": "Data type for random_data: name|email|username|password|phone|birthday|address"},
                "field":       {"type": "STRING", "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "flight_finder",
        "description": (
            "Searches for flights on Google Flights and speaks the best options. "
            "Use when user asks about flights, plane tickets, uçuş, bilet, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":       {"type": "STRING",  "description": "Departure city or airport code"},
                "destination":  {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":         {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date":  {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":   {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":        {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":         {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "steam_control",
        "description": (
            "Controls Steam game management. Use for: installing a game, running a game, "
            "or opening a game's store page. Always use this instead of open_app for games."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "install | run | open_store (default: install)"},
                "game_name": {"type": "STRING", "description": "Name of the game (e.g. 'PUBG', 'Dota 2')"}
            },
            "required": ["game_name"]
        }
    },
    {
        "name": "manage_memory",
        "description": (
            "Manages persistent memory about the user. Use when the user explicitly asks you to "
            "remember something, update a fact, or delete a preference."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category":   {"type": "STRING", "description": "identity | preferences | relationships | notes"},
                "entry_name": {"type": "STRING", "description": "The name of the fact (e.g. 'favorite_color', 'birthday')"},
                "value":      {"type": "STRING", "description": "The information to remember or update"},
                "action":     {"type": "STRING", "description": "update | delete (default: update)"}
            },
            "required": ["category", "entry_name"]
        }
    },
    {
        "name": "graphic_workspace",
        "description": (
            "Sets up the 'Graphic' workspace environment. "
            "Closes/minimizes all open windows and launches Comet, Photoshop, Spotify, and WhatsApp. "
            "Use when the user mentions 'جرافيك', 'Graphic', or 'setup my workspace'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "whatsapp_call",
        "description": "Handle incoming WhatsApp calls. Actions: 'check' (who is calling), 'decline' (cancel the call), 'answer_message' (cancel the call and send a message).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "What to do: check, decline, answer_message"
                },
                "caller_name": {
                    "type": "STRING",
                    "description": "Name of caller (if action is decline/answer_message and you know it)"
                },
                "message": {
                    "type": "STRING",
                    "description": "Message to send (e.g. 'I am busy right now') if action='answer_message'"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "check_whatsapp_messages",
        "description": "Opens WhatsApp and checks for unread messages from VIP contacts or unknown numbers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    }
]


class AtlasLive:

    def __init__(self, ui: AtlasUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self.is_playing     = False
        self.muted          = False
        self.ui.chat_callback = self.speak
        self.mic_enabled    = True  # Start with mic enabled by default
        self.ui.status_text = "LISTENING"
        
        def toggle_mic():
            self.mic_enabled = not self.mic_enabled
            if self.mic_enabled:
                self.ui.status_text = "LISTENING"
            else:
                self.ui.status_text = "[MUTED]"

        def on_f7(e):
            print(f"[ATLAS] ⌨️ F7 Pressed")
            toggle_mic()

        try:
            keyboard.on_press_key("f7", on_f7)
        except Exception as e:
            print(f"[ATLAS] Warning: global F7 hotkey unavailable: {e}")
            self.ui.write_log("SYS: F7 hotkey unavailable. Use chat mode or run with elevated permissions.")


    def speak(self, text: str):
        """Thread-safe speak — any thread can call this."""
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )
    
    def _trigger_greeting(self):
        """Triggers a localized voice greeting."""
        lang = getattr(self.ui, "language", "English")
        greetings = {
            "English": "Systems online. ATLAS is ready, sir.",
            "Arabic": "النظام متصل. أطلس مستعد يا سيدي.",
            "Turkish": "Sistemler çevrimiçi. ATLAS hazır, efendim."
        }
        text = greetings.get(lang, greetings["English"])
        self.speak(text)

    def _tool_speak(self, text: str):
        self.speak(text)

    def _build_config(self) -> types.LiveConnectConfig:
        lang = getattr(self.ui, "language", "English")
        sys_prompt = _build_system_prompt(lang)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=sys_prompt,
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[ATLAS] 🔧 TOOL: {name}  ARGS: {args}")

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(parameters=args, response=None, player=self.ui)
                )
                result = r or f"Opened {args.get('app_name')} successfully."

            elif name == "weather_report":
                r = await loop.run_in_executor(
                    None, lambda: weather_action(parameters=args, player=self.ui)
                )
                result = r or f"Weather report for {args.get('city')} delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(parameters=args, player=self.ui)
                )
                result = r or "Browser action completed."

            elif name == "file_controller":
                r = await loop.run_in_executor(
                    None, lambda: file_controller(parameters=args, player=self.ui)
                )
                result = r or "File operation completed."

            elif name == "send_message":
                r = await loop.run_in_executor(
                    None, lambda: send_message(
                        parameters=args, response=None,
                        player=self.ui, session_memory=None,
                        speak=self._tool_speak
                    )
                )
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(
                    None, lambda: reminder(parameters=args, response=None, player=self.ui)
                )
                result = r or f"Reminder set for {args.get('date')} at {args.get('time')}."

            elif name == "youtube_video":
                r = await loop.run_in_executor(
                    None, lambda: youtube_video(parameters=args, response=None, player=self.ui)
                )
                result = r or "YouTube action executed, sir."

            elif name == "screen_process":
                self.muted = True

                def _run_vision():
                    try:
                        screen_process(parameters=args, response=None, player=self.ui, session_memory=None)
                    finally:
                        # Un-mute as soon as vision finishes (or fails), not after a fixed delay
                        self.muted = False

                threading.Thread(target=_run_vision, daemon=True).start()
                result = "Processing screen. Do not say anything."

            elif name == "computer_settings":
                r = await loop.run_in_executor(
                    None, lambda: computer_settings(
                        parameters=args, response=None, player=self.ui
                    )
                )
                result = r or "Settings adjusted successfully, sir."

            elif name == "cmd_control":
                r = await loop.run_in_executor(
                    None, lambda: cmd_control(parameters=args, player=self.ui)
                )
                result = r or "Command executed."

            elif name == "desktop_control":
                r = await loop.run_in_executor(
                    None, lambda: desktop_control(parameters=args, player=self.ui)
                )
                result = r or "Desktop action completed."

            elif name == "code_helper":
                r = await loop.run_in_executor(
                    None, lambda: code_helper(
                        parameters=args,
                        player=self.ui,
                        speak=self._tool_speak
                    )
                )
                result = r or "Code helper action completed, sir."

            elif name == "workspace_agent":
                r = await loop.run_in_executor(
                    None, lambda: workspace_agent(parameters=args, player=self.ui)
                )
                result = r or "Workspace analysis complete, sir."

            elif name == "dev_agent":
                r = await loop.run_in_executor(
                    None, lambda: dev_agent(
                        parameters=args,
                        player=self.ui,
                        speak=self._tool_speak
                    )
                )
                result = r or "Development task completed, sir."

            elif name == "agent_task":
                goal         = args.get("goal", "")
                priority_str = args.get("priority", "normal").lower()

                from agent.task_queue import get_queue, TaskPriority
                priority_map = {
                    "low":    TaskPriority.LOW,
                    "normal": TaskPriority.NORMAL,
                    "high":   TaskPriority.HIGH,
                }
                priority = priority_map.get(priority_str, TaskPriority.NORMAL)

                queue   = get_queue()
                task_id = queue.submit(
                    goal=goal,
                    priority=priority,
                    speak=self._tool_speak,
                )
                result = f"Task started (ID: {task_id}). I'll update you as I make progress, sir."

            elif name == "web_search":
                r = await loop.run_in_executor(
                    None, lambda: web_search_action(parameters=args, player=self.ui)
                )
                result = r or "Search completed."

            elif name == "computer_control":
                r = await loop.run_in_executor(
                    None, lambda: computer_control(
                        parameters=args, player=self.ui,
                        speak=self._tool_speak
                    )
                )
                result = r or "Computer action executed, sir."

            elif name == "flight_finder":
                r = await loop.run_in_executor(
                    None, lambda: flight_finder(parameters=args, player=self.ui)
                )
                result = r or "Flight search completed, sir."

            elif name == "steam_control":
                r = await loop.run_in_executor(
                    None, lambda: steam_action(parameters=args, player=self.ui)
                )
                result = r or "Steam action completed."

            elif name == "manage_memory":
                category = args.get("category", "notes")
                key      = args.get("entry_name", "")
                val      = args.get("value", "")
                act      = args.get("action", "update")

                from memory.memory_manager import update_memory, load_memory, save_memory
                
                if act == "delete":
                    mem = load_memory()
                    if category in mem and key in mem[category]:
                        del mem[category][key]
                        save_memory(mem)
                        result = f"Successfully deleted '{key}' from my {category} memory, boss."
                    else:
                        result = f"I couldn't find '{key}' in my {category} memory to delete, sir."
                else:
                    update_memory({category: {key: val}})
                    result = f"I've updated my memory, boss. I'll remember that {key} is {val}."

            elif name == "graphic_workspace":
                r = await loop.run_in_executor(
                    None, lambda: graphic_workspace(player=self.ui)
                )
                result = r or "Graphic workspace ready, sir."

            elif name == "whatsapp_call":
                r = await loop.run_in_executor(
                    None, lambda: handle_whatsapp_call(args, player=self.ui, speak=self._tool_speak)
                )
                result = r or "WhatsApp call handled."

            elif name == "check_whatsapp_messages":
                r = await loop.run_in_executor(
                    None, lambda: check_whatsapp_messages(player=self.ui)
                )
                result = r or "Checking messages."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()

        print(f"[ATLAS] 📤 {name} → {result[:80]}")
        log_interaction(f"{name}: {args}", result)

        return types.FunctionResponse(
            id=fc.id,
            name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        """Send audio chunks to Gemini. Exits if session is lost."""
        while self.session is not None:
            try:
                msg = await self.out_queue.get()
                # If session died while waiting for queue
                if self.session is None: break

                await self.session.send_realtime_input(
                    audio=types.Blob(
                        data=msg["data"],
                        mime_type=msg["mime_type"]
                    )
                )
            except Exception as e:
                if self.session is not None:
                    print(f"[ATLAS] 📤 Send Error: {e}")
                # Clear queue on error to avoid spamming old audio on reconnect
                while not self.out_queue.empty():
                    try: self.out_queue.get_nowait()
                    except: break
                break

    async def _listen_audio(self):
        """Capture microphone audio, feed to Gemini, and handle clap detection."""
        print("[ATLAS] 🎤 Mic started")

        # Selection logic: prefer user setting, then auto-detect real mic
        input_idx = getattr(self.ui, "audio_input_index", None)

        if input_idx is None:
            try:
                devices = sd.query_devices()
                for i, d in enumerate(devices):
                    if d['max_input_channels'] > 0:
                        name = d['name'].lower()
                        if ('mic' in name or 'microphone' in name) and 'cable' not in name:
                            input_idx = i
                            print(f"[ATLAS] 🎤 Auto-detected Mic: {d['name']} (Index {i})")
                            self.ui.write_log(f"SYS: Mic active -> {d['name']}")
                            break
            except Exception:
                print("[ATLAS] ⚠️ Could not enumerate audio devices for auto-detect.")

        # Create input stream with error handling for disconnected devices
        try:
            stream = sd.RawInputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype='int16',
                blocksize=CHUNK_SIZE,
                device=input_idx,
                latency='low'
            )
        except Exception as e:
            print(f"[ATLAS] ❌ Failed to open mic (index={input_idx}): {e}")
            self.ui.write_log(f"SYS: Mic error — {e}. Check Settings.")
            return

        if input_idx is not None:
            try:
                d_info = sd.query_devices(input_idx)
                self.ui.write_log(f"SYS: Using Mic -> {d_info['name']}")
            except Exception:
                pass
        stream.start()

        # Clap detection state
        clap_threshold = 28000  # Increased threshold to reduce false positives (out of 32767)
        last_clap_time = 0.0
        cooldown       = 5.0    # Increased cooldown for stability

        try:
            while self.session is not None:
                data, overflowed = await asyncio.to_thread(stream.read, CHUNK_SIZE)
                if self.session is None: break

                # Convert raw bytes to signed 16-bit samples for amplitude analysis
                samples = memoryview(bytes(data)).cast('h')
                max_amp = max(abs(s) for s in samples) if samples else 0

                # Update UI waveform with real-time mic amplitude
                if self.ui and self.ui.current_mode == "voice" and not self.is_playing:
                    norm_amp = min(1.0, max_amp / 16000.0)
                    if norm_amp > 0.05:
                        self.ui.wave_target = [norm_amp * random.uniform(0.3, 1.0) for _ in range(64)]
                    else:
                        self.ui.wave_target = [0.05 * random.uniform(0.1, 0.4) for _ in range(64)]

                # Check for clap (loud sound spike) to toggle "Power" (Mute/Unmute)
                now = time.time()
                if now - last_clap_time > cooldown and max_amp > clap_threshold:
                    print(f"[ATLAS] 👏 Clap detected! Amplitude: {max_amp}")
                    last_clap_time = now
                    
                    # Toggle Mic (Power On/Off)
                    is_muted = self.toggle_mute()
                    self.ui.set_mute_state(is_muted)
                    state_msg = "POWER OFF (MUTED)" if is_muted else "POWER ON (ACTIVE)"
                    self.ui.write_log(f"SYS: Clap trigger -> {state_msg}")

                # Send audio data when mic is enabled and not playing back
                if self.mic_enabled and not self.is_playing:
                    await self.out_queue.put({
                        "data": bytes(data),
                        "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"
                    })
                elif not getattr(self.ui, "speaking", False) and not self.ui.is_typing:
                    if getattr(self.ui, "status_text", "") not in ["LISTENING", "[MUTED]"]:
                        self.ui.status_text = "LISTENING" if self.mic_enabled else "[MUTED]"
        except Exception as e:
            print(f"[ATLAS] ❌ Mic error: {e}")
            raise
        finally:
            stream.stop()
            stream.close()

    async def _receive_audio(self):
        print("[ATLAS] 👂 Recv started")
        out_buf = []
        in_buf  = []

        try:
            async for response in self.session.receive():

                if response.data:
                    self.audio_in_queue.put_nowait(response.data)

                # Handle nested audio, text, and thought parts in server_content
                if response.server_content and response.server_content.model_turn:
                    for part in response.server_content.model_turn.parts:
                        if part.inline_data:
                            self.audio_in_queue.put_nowait(part.inline_data.data)
                        elif part.text:
                            # Capture transcript even if it's not in the data field
                            out_buf.append(part.text.strip())
                        elif part.thought:
                            # Log thought/reasoning to console (internal)
                            print(f"[ATLAS] 🧠 THOUGHT: {part.thought[:100]}...")

                if response.server_content:
                    sc = response.server_content

                    if sc.input_transcription and sc.input_transcription.text:
                        txt = sc.input_transcription.text.strip()
                        if txt:
                            in_buf.append(txt)

                    if sc.output_transcription and sc.output_transcription.text:
                        txt = sc.output_transcription.text.strip()
                        if txt:
                            out_buf.append(txt)

                    if sc.turn_complete:
                        full_in  = ""
                        full_out = ""

                        if in_buf:
                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                        in_buf = []

                        if out_buf:
                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Atlas: {full_out}")
                        out_buf = []

                        if full_in and len(full_in) > 5:
                            threading.Thread(
                                target=_update_memory_async,
                                args=(full_in, full_out),
                                daemon=True
                            ).start()

                if response.tool_call:
                    for fc in response.tool_call.function_calls:
                        print(f"[ATLAS] 📞 Tool call: {fc.name}")
                    fn_responses = await asyncio.gather(
                        *[self._execute_tool(fc) for fc in response.tool_call.function_calls]
                    )
                    await self.session.send_tool_response(
                        function_responses=list(fn_responses)
                    )

        except Exception as e:
            print(f"[ATLAS] ❌ Recv error: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        """Play audio chunks received from Gemini through the speaker."""
        print("[ATLAS] 🔊 Play started")
        output_idx = _pick_output_device(getattr(self.ui, "audio_output_index", None))
        if output_idx is not None:
            try:
                d_info = sd.query_devices(output_idx)
                self.ui.write_log(f"SYS: Speaker active -> {d_info['name']}")
                print(f"[ATLAS] Speaker output: {d_info['name']} (Index {output_idx})")
            except Exception:
                pass

        # Create output stream with error handling for missing devices
        try:
            stream = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype='int16',
                blocksize=CHUNK_SIZE,
                device=output_idx,
                latency='low'
            )
        except Exception as e:
            print(f"[ATLAS] ❌ Failed to open speaker (index={output_idx}): {e}")
            self.ui.write_log(f"SYS: Speaker error — {e}. Check Settings.")
            return

        stream.start()

        async def reset_playing_flag():
            await asyncio.sleep(0.4)
            if self.audio_in_queue and self.audio_in_queue.empty():
                self.is_playing = False
                self.ui.stop_speaking()

        try:
            while self.session is not None:
                chunk = await self.audio_in_queue.get()
                if self.session is None: break
                if self.muted:
                    continue

                # Chat mode: text only, no audio output
                if getattr(self.ui, "current_mode", "voice") == "chat":
                    continue

                if not self.is_playing:
                    self.ui.start_speaking()
                self.is_playing = True
                if not chunk:
                    continue
                
                # Simple Volume Boost (1.5x) using memoryview for speed without numpy
                try:
                    import struct
                    # 'h' is signed short (16-bit)
                    count = len(chunk) // 2
                    fmt = f"{count}h"
                    samples = list(struct.unpack(fmt, chunk))
                    # Multiply and clip
                    boosted = []
                    for s in samples:
                        ns = int(s * 1.6)
                        if ns > 32767: ns = 32767
                        elif ns < -32768: ns = -32768
                        boosted.append(ns)
                    chunk = struct.pack(fmt, *boosted)
                except Exception:
                    pass

                await asyncio.to_thread(stream.write, chunk)

                if self.audio_in_queue.empty():
                    asyncio.create_task(reset_playing_flag())
        except Exception as e:
            print(f"[ATLAS] ❌ Play error: {e}")
            raise
        finally:
            stream.stop()
            stream.close()



    async def run(self):
        """Main loop for AtlasLive. Handles connection and task orchestration."""
        while True:
            try:
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha"}
                )
            except Exception as e:
                print(f"[ATLAS] ❌ Gemini client init failed: {e}")
                try:
                    self.ui.enter_connect_wait_state()
                except Exception:
                    pass
                await asyncio.sleep(8)
                continue

            try:
                print("[ATLAS] 🔌 Connecting...")
                try:
                    self.ui.enter_connect_wait_state()
                except Exception:
                    pass
                config = self._build_config()

                if not getattr(self, "_monitors_started", False):
                    start_monitor(speak_fn=self.speak, ui=self.ui)
                    start_telegram_monitor(speak_fn=self.speak, ui=self.ui)
                    self._monitors_started = True

                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=100)
                    self.muted          = False
                    
                    print("[ATLAS] ✅ Connected.")
                    self.ui.write_log("SYS: Connection established.")
                    try:
                        self.ui.exit_connect_wait_state()
                    except Exception:
                        pass

                    if not getattr(self, "_greeted", False):
                        self.ui.root.after(1000, self._trigger_greeting)
                        self._greeted = True

                    try:
                        await asyncio.gather(
                            self._send_realtime(),
                            self._listen_audio(),
                            self._receive_audio(),
                            self._play_audio()
                        )
                    except Exception as e:
                        print(f"[ATLAS] 🔌 Session ended: {e}")
                    finally:
                        self.session = None

            except socket.gaierror:
                print("[ATLAS] 📡 Network Error: No internet.")
                try:
                    self.ui.enter_connect_wait_state()
                except Exception:
                    pass
            except Exception as e:
                err_msg = str(e).lower()
                if "handshake" in err_msg:
                    print("[ATLAS] 🔍 Handshake timeout.")
                elif "11001" in err_msg:
                    print("[ATLAS] 📡 DNS failure.")
                else:
                    print(f"[ATLAS] ⚠️ Error: {e}")
                try:
                    self.ui.notify_link_recovery()
                except Exception:
                    pass

            self.session = None
            await asyncio.sleep(5)

    def stop(self):
        """Emergency stop: reset session and clear audio."""
        self.is_playing = False
        self.muted = False
        # If we have an active session, setting it to None will trigger the loops to exit
        self.session = None 

    def toggle_mute(self):
        """Toggle microphone status."""
        self.mic_enabled = not self.mic_enabled
        self.ui.write_log(f"SYS: Microphone {'Muted' if not self.mic_enabled else 'Unmuted'}.", "sys")
        return not self.mic_enabled # Return current state for UI icon update




# ---------------------------------------------------------------------------
# Standalone tool executor for chat providers (no async dependency)
# ---------------------------------------------------------------------------

def _execute_tool_sync(name: str, args: dict, ui, speak_fn) -> str:
    """Execute a tool call synchronously. Used by AtlasChatProvider.
    Returns the result string directly — no async, no fake objects."""
    result = ""
    try:
        if name == "open_app":
            r = open_app(parameters=args, response=None, player=ui)
            result = r or f"Opened {args.get('app_name')} successfully."

        elif name == "weather_report":
            r = weather_action(parameters=args, player=ui)
            result = r or f"Weather report for {args.get('city')} delivered."

        elif name == "browser_control":
            r = browser_control(parameters=args, player=ui)
            result = r or "Browser action completed."

        elif name == "file_controller":
            r = file_controller(parameters=args, player=ui)
            result = r or "File operation completed."

        elif name == "send_message":
            r = send_message(
                parameters=args, response=None,
                player=ui, session_memory=None,
                speak=speak_fn
            )
            result = r or f"Message sent to {args.get('receiver')}."

        elif name == "reminder":
            r = reminder(parameters=args, response=None, player=ui)
            result = r or f"Reminder set for {args.get('date')} at {args.get('time')}."

        elif name == "youtube_video":
            r = youtube_video(parameters=args, response=None, player=ui)
            result = r or "YouTube action executed, sir."

        elif name == "screen_process":
            screen_process(parameters=args, response=None, player=ui, session_memory=None)
            result = "Screen processed."

        elif name == "computer_settings":
            r = computer_settings(parameters=args, response=None, player=ui)
            result = r or "Settings adjusted successfully, sir."

        elif name == "cmd_control":
            r = cmd_control(parameters=args, player=ui)
            result = r or "Command executed."

        elif name == "desktop_control":
            r = desktop_control(parameters=args, player=ui)
            result = r or "Desktop action completed."

        elif name == "code_helper":
            r = code_helper(parameters=args, player=ui, speak=speak_fn)
            result = r or "Code helper action completed, sir."

        elif name == "workspace_agent":
            r = workspace_agent(parameters=args, player=ui)
            result = r or "Workspace analysis complete, sir."

        elif name == "dev_agent":
            r = dev_agent(parameters=args, player=ui, speak=speak_fn)
            result = r or "Development task completed, sir."

        elif name == "agent_task":
            goal = args.get("goal", "")
            priority_str = args.get("priority", "normal").lower()
            from agent.task_queue import get_queue, TaskPriority
            priority_map = {
                "low": TaskPriority.LOW,
                "normal": TaskPriority.NORMAL,
                "high": TaskPriority.HIGH,
            }
            priority = priority_map.get(priority_str, TaskPriority.NORMAL)
            queue = get_queue()
            task_id = queue.submit(goal=goal, priority=priority, speak=speak_fn)
            result = f"Task started (ID: {task_id}). I'll update you as I make progress, sir."

        elif name == "web_search":
            r = web_search_action(parameters=args, player=ui)
            result = r or "Search completed."

        elif name == "computer_control":
            r = computer_control(parameters=args, player=ui, speak=speak_fn)
            result = r or "Computer action executed, sir."

        elif name == "flight_finder":
            r = flight_finder(parameters=args, player=ui)
            result = r or "Flight search completed, sir."

        elif name == "steam_control":
            r = steam_action(parameters=args, player=ui)
            result = r or "Steam action completed."

        elif name == "manage_memory":
            category = args.get("category", "notes")
            key = args.get("entry_name", "")
            val = args.get("value", "")
            act = args.get("action", "update")
            from memory.memory_manager import update_memory, load_memory, save_memory
            if act == "delete":
                mem = load_memory()
                if category in mem and key in mem[category]:
                    del mem[category][key]
                    save_memory(mem)
                    result = f"Successfully deleted '{key}' from my {category} memory, boss."
                else:
                    result = f"I couldn't find '{key}' in my {category} memory to delete, sir."
            else:
                update_memory({category: {key: val}})
                result = f"I've updated my memory, boss. I'll remember that {key} is {val}."

        elif name == "graphic_workspace":
            r = graphic_workspace(player=ui)
            result = r or "Graphic workspace ready, sir."

        elif name == "whatsapp_call":
            r = handle_whatsapp_call(args, player=ui, speak=speak_fn)
            result = r or "WhatsApp call handled."

        elif name == "check_whatsapp_messages":
            r = check_whatsapp_messages(player=ui)
            result = r or "Checking messages."

        else:
            result = f"Unknown tool: {name}"

        log_interaction(f"{name}: {args}", result)
        return result

    except Exception as e:
        traceback.print_exc()
        result = f"Tool '{name}' failed: {e}"
        log_interaction(f"{name}: {args}", result)
        return result


# ---------------------------------------------------------------------------
# AtlasChatProvider — generic OpenAI-compatible chat provider
# Supports: OpenRouter, Groq, Ollama, or any custom endpoint
# ---------------------------------------------------------------------------

class AtlasChatProvider:
    """Chat-based ATLAS provider for any OpenAI-compatible API."""

    def __init__(self, ui: AtlasUI):
        self.ui = ui
        chat_cfg = _get_chat_config()
        self.api_url = chat_cfg["url"]
        self.api_key = chat_cfg["key"]
        self.model = chat_cfg["model"]
        self.provider_name = _get_provider()
        self.history = []
        self.request_lock = threading.Lock()
        self.muted = False
        self.mic_enabled = True # For parity with AtlasLive
        self.ui.chat_callback = self.speak
        self.ui.on_stop_callback = self.stop
        self.ui.on_mute_callback = self.toggle_mute
        self.ui.status_text = "CHAT READY"

    def _tool_speak(self, text):
        """Used by tools to speak while in chat mode."""
        # pyrefly: ignore [missing-import]
        from audio.tts import speak
        speak(text, player=self.ui)

    def stop(self):
        """Emergency stop: Clear history and reset UI."""
        self.history = []
        self.ui.stop_speaking()
        self.ui.write_log("SYS: Conversation history cleared.", "warn")

    def toggle_mute(self):
        """Toggle microphone (used for voice input triggers if added)."""
        self.mic_enabled = not self.mic_enabled
        self.ui.write_log(f"SYS: Microphone {'Muted' if not self.mic_enabled else 'Unmuted'}.", "sys")
        return not self.mic_enabled

    def speak(self, text: str):
        """Send user text to the chat provider in a background thread."""
        if not text or not text.strip():
            return
        threading.Thread(target=self._send_chat, args=(text.strip(),), daemon=True).start()

    def _build_messages(self, user_text: str):
        system_prompt = _build_system_prompt(getattr(self.ui, "language", "English"))
        return [{"role": "system", "content": system_prompt}, *self.history, {"role": "user", "content": user_text}]

    def _post_chat_completion(self, messages):
        """Send a chat completion request to the configured API endpoint."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter-specific headers (harmless for other providers)
            "HTTP-Referer": "https://atlas.local",
            "X-Title": "ATLAS",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": _openrouter_tools(),
            "tool_choice": "auto",
            "temperature": 0.5,
        }
        response = requests.post(self.api_url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()

    def _send_chat(self, user_text: str):
        """Process a user message: call API, handle tool calls, display response."""
        with self.request_lock:
            self.ui.status_text = "RESPONDING"
            messages = self._build_messages(user_text)
            final_text = ""

            try:
                # Allow up to 6 rounds of tool calls before forcing a text response
                for _ in range(6):
                    data = self._post_chat_completion(messages)
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    content = message.get("content") or ""
                    tool_calls = message.get("tool_calls") or []

                    assistant_message = {"role": "assistant", "content": content}
                    if tool_calls:
                        assistant_message["tool_calls"] = tool_calls
                    messages.append(assistant_message)

                    # No tool calls → we have the final response
                    if content:
                        # Append content to final_text so user sees everything
                        if final_text: final_text += "\n"
                        final_text += content.strip()
                        # Immediately log intermediate thoughts/responses
                        self.ui.write_log(f"Atlas: {content.strip()}")

                    if not tool_calls:
                        break

                    # Execute each tool call and feed results back
                    for tool_call in tool_calls:
                        fn = tool_call.get("function") or {}
                        tc_name = fn.get("name", "")
                        raw_args = fn.get("arguments") or "{}"
                        try:
                            tc_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                        except Exception:
                            tc_args = {}

                        print(f"[ATLAS] 🔧 TOOL: {tc_name}  ARGS: {tc_args}")
                        tool_result = _execute_tool_sync(tc_name, tc_args, self.ui, self._tool_speak)
                        print(f"[ATLAS] 📤 {tc_name} → {tool_result[:80]}")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", str(uuid.uuid4())),
                            "name": tc_name,
                            "content": tool_result,
                        })

                # Keep conversation history (skip the system prompt at index 0)
                self.history = messages[1:]

                # Final display only if no intermediate text was shown, or to show accumulated result
                if final_text:
                    # We already logged intermediate parts, so only log final_text if it's different or just for completeness
                    # Actually, self.ui.write_log(f"Atlas: {final_text}") here would double-post if we logged in the loop.
                    # Let's only speak it.
                    self._tool_speak(final_text)
                    # Memory learning update for chat mode
                    threading.Thread(target=_update_memory_async, args=(user_text, final_text), daemon=True).start()
                else:
                    self.ui.write_log(f"SYS: {self.provider_name} returned no final text response.")

            except requests.HTTPError as e:
                body = e.response.text[:400] if e.response is not None else ""
                self.ui.write_log(f"SYS: API error -> {e}. {body}")
            except requests.ConnectionError:
                self.ui.write_log(f"SYS: Cannot connect to {self.provider_name}. Check your network or API URL.")
            except Exception as e:
                self.ui.write_log(f"SYS: {self.provider_name} request failed -> {e}")
                traceback.print_exc()
            finally:
                self.ui.status_text = "CHAT READY"

    async def run(self):
        """Entry point — start monitors and keep the event loop alive."""
        try:
            self.ui.exit_connect_wait_state()
        except Exception:
            pass
        self.ui.write_log(f"SYS: Provider -> {self.provider_name} ({self.model})")
        self.ui.write_log("SYS: Chat mode active. Use Chat Mode for best results.")

        # Ensure monitors are started
        if not getattr(self, "_monitors_started", False):
            start_monitor(speak_fn=self._tool_speak, ui=self.ui)
            start_telegram_monitor(speak_fn=self._tool_speak, ui=self.ui)
            self._monitors_started = True

        while True:
            await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ui = AtlasUI("face_v1.png")

    def runner():
        ui.wait_for_api_key()
        # Trigger boot sequence once before entering the loop
        ui.root.after(100, ui.boot_sequence)

        provider = _get_provider()
        if provider == "gemini":
            atlas = AtlasLive(ui)
        else:
            atlas = AtlasChatProvider(ui)

        # Connect UI buttons to Atlas logic
        ui.on_stop_callback = atlas.stop
        ui.on_mute_callback = atlas.toggle_mute

        try:
            asyncio.run(atlas.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
