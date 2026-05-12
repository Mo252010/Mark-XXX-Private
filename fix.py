import re
from pathlib import Path

_root = Path(__file__).resolve().parent
_main = _root / "main.py"
content = _main.read_text(encoding="utf-8")

correct_run = '''
    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),  
            http_options={"api_version": "v1alpha"}
        )

        while True:
            try:
                print("[ATLAS] 🔌 Connecting...")
                config = self._build_config()

                start_monitor(speak_fn=self.speak, ui=self.ui)
                start_telegram_monitor(speak_fn=self.speak, ui=self.ui)

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=200)
                    self.muted          = False  # Safety reset for audio output

                    print("[ATLAS] ✅ Connected.")
                    self.ui.write_log("SYS: Connection established.")
                    
                    # Multilingual Greeting (only once per session)
                    if not getattr(self, "_greeted", False):
                        self.ui.root.after(1000, self._trigger_greeting)
                        self._greeted = True

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except socket.gaierror:
                print("[ATLAS] 📡 Network Error: No internet connection or DNS failure. (getaddrinfo failed)")
            except Exception as e:
                # Handle specific known errors cleanly
                err_msg = str(e).lower()
                if "handshake" in err_msg:
                    print("[ATLAS] 🔍 Handshake timeout detected. This usually means high latency or blocked WebSockets.")
                elif "11001" in err_msg or "getaddrinfo" in err_msg:
                    print("[ATLAS] 📡 Network Error: DNS resolution failed.")
                else:
                    print(f"[ATLAS] ⚠️  Error: {e}")
                    import traceback
                    traceback.print_exc()

            retry_delay = 5  # Increased default delay
            print(f"[ATLAS] 🔄 Reconnecting in {retry_delay}s...")
            import asyncio
            await asyncio.sleep(retry_delay)
'''

new_content = re.sub(
    r'    async def run\(self\):.*?class AtlasOpenRouter:', 
    correct_run + '\n\nclass AtlasOpenRouter:', 
    content, 
    flags=re.DOTALL
)

_main.write_text(new_content, encoding="utf-8")
print("Fixed main.py!")
