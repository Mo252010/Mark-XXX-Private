"""
Edge TTS output for chat-mode tools — نطق العربية في وضع الدردشة
Heavy imports stay inside the worker thread until first invocation (lazy, saves RAM).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any

from utils import contains_arabic


def _edge_voice(language: str, text: str) -> str:
    """Pick an Edge voice preset from UI language hint and script."""
    lg = (language or "English").strip()
    if contains_arabic(text) or lg == "Arabic":
        return "ar-EG-SalmaNeural"
    if lg == "Turkish":
        return "tr-TR-AhmetNeural"
    return "en-US-ChristopherNeural"


async def _synthesize_mp3(text: str, voice: str, out_path: str) -> None:
    """Generate MP3 bytes via microsoft edge_tts Communicate."""
    import edge_tts

    communicator = edge_tts.Communicate(text.strip(), voice)
    await communicator.save(out_path)


def _notify(player: Any | None, message: str) -> None:
    if player and hasattr(player, "write_log"):
        try:
            player.write_log(message, "warn")
        except TypeError:
            player.write_log(message)


def _play_mp3_blocking(path: str) -> None:
    """
    Prefer ffplay (silent, auto-exit); fallback to WAV+sounddevice via ffmpeg if available.

    لا نفتح تشغيل الوسائط الافتراضي للمستخدم حتى لا تظهر نوافذ إضافية.
    """
    ffplay = shutil.which("ffplay")
    if ffplay:
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        import sounddevice as sd
        import soundfile as sf

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                path,
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                wav_path,
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data, sr = sf.read(wav_path, dtype="float32")
            sd.play(data, sr)
            sd.wait()
        except Exception as exc:
            print(f"[audio.tts] ffmpeg/sounddevice playback failed: {exc}")
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass
        return

    raise RuntimeError(
        "No audio backend: install ffmpeg (includes ffplay) for chat-mode TTS, or use Gemini voice mode."
        " / لم يتوفر ffmpeg أو ffplay للتشغيل."
    )


def _speak_worker(text: str, player: Any | None, voice: str | None) -> None:
    if not text or not text.strip():
        return
    root = getattr(player, "language", "English")
    vn = voice or _edge_voice(str(root), text)
    fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        try:
            asyncio.run(_synthesize_mp3(text, vn, mp3_path))
        except ImportError:
            exe = shutil.which(sys.executable) or sys.executable
            subprocess.run(
                [
                    exe,
                    "-m",
                    "edge_tts",
                    "--voice",
                    vn,
                    "--text",
                    text.strip(),
                    "--write-media",
                    mp3_path,
                ],
                check=True,
                timeout=180,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            _notify(player, f"SYS: Edge TTS synth failed ({exc}). Check network / firewall.")
            print(f"[audio.tts] synthesis error: {exc}")
            return
        try:
            _play_mp3_blocking(mp3_path)
        except Exception as exc:
            _notify(player, f"SYS: TTS playback unavailable ({exc}).")
            print(f"[audio.tts] playback error: {exc}")
    finally:
        try:
            os.remove(mp3_path)
        except OSError:
            pass


_speak_lock = threading.Lock()

def speak(text: str, player=None, voice: str | None = None) -> None:
    """
    Thread-safe, sequential TTS for chat provider tool callbacks.
    Ensures that only one message is spoken at a time.
    """
    def _locked_worker():
        with _speak_lock:
            _speak_worker(text, player, voice)

    threading.Thread(target=_locked_worker, daemon=True).start()


if __name__ == "__main__":
    print("This module is imported by ATLAS main; use voice mode or chat tools to trigger TTS.")
