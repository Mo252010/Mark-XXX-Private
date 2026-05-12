# actions/youtube_video.py
# ATLAS — YouTube Controller
# Uses webbrowser.open() for reliable navigation (no Start Menu tricks).
# pyautogui is used ONLY for in-page keyboard controls (pause, fullscreen, etc.)

import json
import re
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import pyautogui
import pyperclip

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _safe_write(text: str) -> None:
    """Clipboard paste — works with Arabic, emoji, URLs, any Unicode."""
    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)


# ─── Browser / navigation ─────────────────────────────────────────────────────

def open_url(url: str, wait: float = 5.0) -> None:
    print(f"[YouTube] 🌐 Opening: {url}")
    subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    time.sleep(wait)


def skip_ads() -> None:
    print("[YouTube] 🔍 Attempting ad skip...")
    screen_w, screen_h = pyautogui.size()
    for _ in range(2):
        pyautogui.click(int(screen_w * 0.86), int(screen_h * 0.83))
        time.sleep(0.6)


def toggle_fullscreen() -> None:
    pyautogui.press("f")
    time.sleep(0.4)


# ─── Video selection ──────────────────────────────────────────────────────────

def _scrape_first_video_url(query: str) -> "str | None":
    """Scrapes the first video ID from YouTube search results — no API needed."""
    if not _REQUESTS_OK:
        return None
    try:
        search_url = "https://www.youtube.com/results?search_query=" + query.replace(" ", "+")
        r    = requests.get(search_url, headers=HEADERS, timeout=10)
        html = r.text
        match = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        if match:
            vid_id = match.group(1)
            print(f"[YouTube] 🎯 First video ID: {vid_id}")
            return f"https://www.youtube.com/watch?v={vid_id}"
        return None
    except Exception as e:
        print(f"[YouTube] ⚠️ Scrape first video failed: {e}")
        return None


def _click_first_video() -> None:
    """Fallback: Tab-based first video selection."""
    time.sleep(2.5)
    screen_w, screen_h = pyautogui.size()
    pyautogui.click(screen_w // 2, screen_h // 2)
    time.sleep(0.5)
    for _ in range(10):
        pyautogui.press("tab")
        time.sleep(0.18)
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(3.0)


# ─── YouTube URL helpers ──────────────────────────────────────────────────────

def _extract_video_id(url: str) -> "str | None":
    match = re.search(
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url
    )
    return match.group(1) if match else None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> "str | None":
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()
        url = simpledialog.askstring("ATLAS", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ URL dialog failed: {e}")
        return None


# ─── Transcript & summarize ───────────────────────────────────────────────────

def _get_transcript(video_id: str) -> "str | None":
    if not _TRANSCRIPT_OK:
        return None
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        languages  = ["en", "tr", "ar", "de", "fr", "es", "it", "pt", "ru", "ja", "ko", "zh"]

        for finder in [
            lambda: transcript_list.find_manually_created_transcript(languages),
            lambda: transcript_list.find_generated_transcript(languages),
        ]:
            try:
                transcript = finder()
                break
            except Exception:
                pass

        if transcript is None:
            for t in transcript_list:
                transcript = t
                break

        if transcript is None:
            return None

        fetched = transcript.fetch()
        text    = " ".join(entry["text"] for entry in fetched)
        print(f"[YouTube] 📝 Transcript: {len(text)} chars")
        return text

    except Exception as e:
        print(f"[YouTube] ⚠️ Transcript fetch failed: {e}")
        return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are ATLAS, an advanced AI assistant. "
            "Summarize YouTube video transcripts clearly and concisely. "
            "Structure: 1-sentence overview, then 3-5 key points. "
            "Be direct. Address the user as 'sir'. "
            "Match the language of the transcript."
        ),
    )
    truncated = transcript[:80000] + ("..." if len(transcript) > 80000 else "")
    response  = model.generate_content(
        f"Please summarize this YouTube video transcript:\n\n{truncated}"
    )
    return response.text.strip()


def _save_to_notepad(content: str, video_url: str) -> str:
    import platform
    from datetime import datetime

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / f"youtube_summary_{ts}.txt"

    header = (
        f"ATLAS — YouTube Summary\n"
        f"{'─' * 50}\n"
        f"URL  : {video_url}\n"
        f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")
    print(f"[YouTube] 💾 Summary saved: {filepath}")

    openers = {
        "Windows": ["notepad.exe", str(filepath)],
        "Darwin":  ["open", "-t", str(filepath)],
        "Linux":   ["xdg-open", str(filepath)],
    }
    cmd = openers.get(platform.system())
    if cmd:
        subprocess.Popen(cmd)

    return str(filepath)


# ─── Scraping helpers ─────────────────────────────────────────────────────────

def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text
        info = {}

        for key, pattern in [
            ("title",    r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel",  r'"ownerChannelName":"([^"]+)"'),
            ("views",    r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
            ("likes",    r'"label":"([0-9,]+ likes)"'),
        ]:
            m = re.search(pattern, html)
            if m:
                val = m.group(1)
                if key == "views":
                    val = f"{int(val):,}"
                elif key == "duration":
                    s   = int(val)
                    val = f"{s // 60}:{s % 60:02d}"
                info[key] = val

        return info
    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "TR", max_results: int = 8) -> list:
    if not _REQUESTS_OK:
        return []
    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r        = requests.get(url, headers=HEADERS, timeout=12)
        html     = r.text
        titles   = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)

        results, seen = [], set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            results.append({
                "rank":    len(results) + 1,
                "title":   title,
                "channel": channels[i] if i < len(channels) else "Unknown",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []


# ─── Action handlers ──────────────────────────────────────────────────────────

def _handle_play(parameters: dict, player) -> str:
    query = parameters.get("query", "").strip()
    if not query:
        return "Please tell me what you'd like to watch, sir."

    if player:
        player.write_log(f"[YouTube] Playing: {query}")

    # Direct URL
    if _is_valid_youtube_url(query):
        print(f"[YouTube] 🔗 Direct URL: {query}")
        open_url(query, wait=5.0)
        skip_ads()
        toggle_fullscreen()
        return f"Playing video: {query}"

    # Scrape first video ID directly — no tab counting
    print(f"[YouTube] 🔍 Searching: {query}")
    direct_url = _scrape_first_video_url(query)
    if direct_url:
        open_url(direct_url, wait=5.0)
        skip_ads()
        toggle_fullscreen()
        return f"Playing YouTube video for: {query}"

    # Fallback: open search page and tab to first video
    search_url = "https://www.youtube.com/results?search_query=" + query.replace(" ", "+")
    open_url(search_url, wait=5.0)
    _click_first_video()
    time.sleep(2.0)
    skip_ads()
    toggle_fullscreen()
    return f"Playing YouTube video for: {query}"


def _handle_pause(parameters: dict, player, speak) -> str:
    pyautogui.press("k")
    return "Toggled playback, sir."


def _handle_fullscreen(parameters: dict, player, speak) -> str:
    toggle_fullscreen()
    return "Toggled fullscreen, sir."


def _handle_skip_ad(parameters: dict, player, speak) -> str:
    skip_ads()
    return "Attempted to skip the ad, sir."


def _handle_volume_up(parameters: dict, player, speak) -> str:
    for _ in range(5):
        pyautogui.press("up")
        time.sleep(0.05)
    return "Volume increased, sir."


def _handle_volume_down(parameters: dict, player, speak) -> str:
    for _ in range(5):
        pyautogui.press("down")
        time.sleep(0.05)
    return "Volume decreased, sir."


def _handle_next(parameters: dict, player, speak) -> str:
    pyautogui.hotkey("shift", "n")
    return "Skipped to next video, sir."


def _handle_summarize(parameters: dict, player, speak) -> str:
    if not _TRANSCRIPT_OK:
        return (
            "youtube-transcript-api is not installed, sir. "
            "Run: pip install youtube-transcript-api"
        )

    url = parameters.get("url", "").strip() or _ask_for_url("Paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "No valid YouTube URL provided, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID from that URL, sir."

    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Fetching the transcript now, sir. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return (
            "I couldn't retrieve a transcript for that video, sir. "
            "It may not have subtitles available."
        )

    if speak:
        speak("Transcript retrieved. Generating summary now.")

    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as e:
        return f"Summary generation failed, sir: {e}"

    if speak:
        speak(summary)

    if parameters.get("save", False):
        saved_path = _save_to_notepad(summary, url)
        return f"Summary saved to Desktop: {saved_path}"

    return summary


def _handle_get_info(parameters: dict, player, speak) -> str:
    url = parameters.get("url", "").strip() or _ask_for_url("Paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID, sir."

    if player:
        player.write_log(f"[YouTube] Getting info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video information, sir."

    lines = [
        f"{k.capitalize()}: {v}"
        for k in ("title", "channel", "views", "duration", "likes")
        if k in info
    ]
    result = "\n".join(lines)

    if speak:
        speak(f"Here's the video info, sir. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(parameters: dict, player, speak) -> str:
    region = parameters.get("region", "TR").upper()

    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not fetch trending videos for region {region}, sir."

    lines = [f"Top trending in {region}:"] + [
        f"{v['rank']}. {v['title']} — {v['channel']}" for v in trending
    ]
    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = "Here are the top trending videos, sir. " + ". ".join(
            f"Number {v['rank']}: {v['title']} by {v['channel']}" for v in top3
        )
        speak(spoken)

    return result


# ─── Dispatch ─────────────────────────────────────────────────────────────────

_ACTION_MAP = {
    "play":        (_handle_play,        False),
    "summarize":   (_handle_summarize,   True),
    "get_info":    (_handle_get_info,    True),
    "trending":    (_handle_trending,    True),
    "pause":       (_handle_pause,       True),
    "resume":      (_handle_pause,       True),
    "fullscreen":  (_handle_fullscreen,  True),
    "skip_ad":     (_handle_skip_ad,     True),
    "volume_up":   (_handle_volume_up,   True),
    "volume_down": (_handle_volume_down, True),
    "next":        (_handle_next,        True),
}


def youtube_video(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action}  Params: {params}")

    entry = _ACTION_MAP.get(action)
    if entry is None:
        available = ", ".join(_ACTION_MAP)
        return f"Unknown YouTube action: '{action}'. Available: {available}."

    handler, needs_speak = entry
    try:
        if needs_speak:
            return handler(params, player, speak) or "Done."
        return handler(params, player) or "Done."
    except Exception as e:
        print(f"[YouTube] ❌ Error in {action}: {e}")
        return f"YouTube {action} failed, sir: {e}"