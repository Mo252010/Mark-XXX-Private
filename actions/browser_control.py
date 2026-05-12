# actions/browser_control.py
# Playwright-based browser controller for ATLAS.
# Key fixes vs previous version:
#   - Browser launch: proper fallback chain (channel → exe → built-in chromium)
#   - Text input: use element.fill() instead of clipboard Ctrl+V (reliable in Playwright)
#   - viewport=None → no_viewport=True (correct Playwright API)
#   - Corrupted storage state handled gracefully
#   - Better error logging throughout

import asyncio
import threading
import concurrent.futures
import platform
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


# ─── Browser detection ────────────────────────────────────────────────────────

def _get_default_browser_id() -> str:
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            key     = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
            )
            prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
            winreg.CloseKey(key)
            return prog_id
        elif system == "Darwin":
            result = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()
        elif system == "Linux":
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()
    except Exception:
        pass
    return ""


_BROWSER_BINARIES = {
    "Windows": {
        "opera":   ["opera.exe"],
        "brave":   ["brave.exe"],
        "vivaldi": ["vivaldi.exe"],
        "chrome":  ["chrome.exe"],
        "firefox": ["firefox.exe"],
    },
    "Darwin": {
        "opera":   ["opera"],
        "brave":   ["brave browser", "brave"],
        "vivaldi": ["vivaldi"],
        "chrome":  ["google chrome", "google-chrome"],
        "firefox": ["firefox"],
    },
    "Linux": {
        "opera":   ["opera", "opera-stable"],
        "brave":   ["brave-browser", "brave"],
        "vivaldi": ["vivaldi-stable", "vivaldi"],
        "chrome":  ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"],
        "firefox": ["firefox"],
    },
}


def _get_opera_executable() -> "str | None":
    if platform.system() != "Windows":
        return None
    try:
        import winreg
        candidate_keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
        ]
        for key_path in candidate_keys:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(key, None).strip().strip('"')
                    winreg.CloseKey(key)
                    # Extract just the exe path (strip any args)
                    exe = val.split('"')[0].split(" --")[0].strip().strip('"')
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _find_browser_executable(prog_id: str) -> "tuple[str, str | None, str | None]":
    """
    Returns (engine_name, exe_path, channel).
    engine_name : 'chromium' | 'firefox' | 'webkit'
    exe_path    : absolute path to browser binary, or None
    channel     : Playwright channel name ('chrome', 'msedge', etc.), or None
    """
    system  = platform.system()
    os_bins = _BROWSER_BINARIES.get(system, {})

    if any(x in prog_id for x in ["firefox", "mozilla"]):
        return "firefox", None, None
    if "safari" in prog_id:
        return "webkit", None, None
    if "edge" in prog_id:
        return "chromium", None, "msedge"
    if "opera" in prog_id:
        exe = _get_opera_executable()
        if exe:
            return "chromium", exe, None
        for binary in os_bins.get("opera", []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None
    for name, patterns in {"brave": ["brave"], "vivaldi": ["vivaldi"], "chrome": ["chrome"]}.items():
        if not any(p in prog_id for p in patterns):
            continue
        for binary in os_bins.get(name, []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None
    if "chrome" in prog_id:
        return "chromium", None, "chrome"

    # Unknown → let the launch function handle fallback
    return "chromium", None, None


# ─── Persistent profile dir ───────────────────────────────────────────────────
_STORAGE_DIR = Path.home() / ".atlas_browser_profile"
_STORAGE_DIR.mkdir(exist_ok=True)
_STORAGE_FILE = _STORAGE_DIR / "state.json"


# ─── Browser thread ───────────────────────────────────────────────────────────

class _BrowserThread:

    def __init__(self):
        self._loop       = None
        self._thread     = None
        self._ready      = threading.Event()
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._pages: list = []

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BrowserThread"
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 45):
        if not self._loop:
            raise RuntimeError("BrowserThread not started.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    # ── Page management ────────────────────────────────────────────────────

    async def _active_page(self):
        self._pages = [p for p in self._pages if not p.is_closed()]
        if not self._pages:
            await self._launch()
        return self._pages[-1]

    async def _launch(self):
        """
        Tries to launch the system default browser.
        Falls back to Playwright's built-in Chromium if everything else fails.
        """
        prog_id                        = _get_default_browser_id()
        engine_name, exe_path, channel = _find_browser_executable(prog_id)
        engine                         = getattr(self._playwright, engine_name)

        chromium_args = ["--start-maximized", "--disable-blink-features=AutomationControlled"]

        # ── Try system browser ────────────────────────────────────────────
        if self._browser is None or not self._browser.is_connected():
            launch_ok = False

            # Attempt 1: channel or exe
            if channel or exe_path:
                kwargs = {"headless": False}
                if engine_name == "chromium":
                    kwargs["args"] = chromium_args
                if exe_path:
                    kwargs["executable_path"] = exe_path
                elif channel:
                    kwargs["channel"] = channel
                try:
                    self._browser = await engine.launch(**kwargs)
                    label = channel or exe_path or engine_name
                    print(f"[Browser] ✅ Launched: {label}")
                    launch_ok = True
                except Exception as e:
                    print(f"[Browser] ⚠️  System browser failed ({e})")

            # Attempt 2: bare engine (no channel/exe)
            if not launch_ok:
                try:
                    kwargs = {"headless": False}
                    if engine_name == "chromium":
                        kwargs["args"] = chromium_args
                    self._browser = await engine.launch(**kwargs)
                    print(f"[Browser] ✅ Launched bare {engine_name}")
                    launch_ok = True
                except Exception as e:
                    print(f"[Browser] ⚠️  Bare engine failed ({e})")

            # Attempt 3: built-in Playwright Chromium (always available)
            if not launch_ok:
                print("[Browser] 🔄 Falling back to built-in Chromium")
                self._browser = await self._playwright.chromium.launch(
                    headless=False,
                    args=chromium_args,
                )

        # ── Build context ─────────────────────────────────────────────────
        # Load saved storage state (cookies/logins), but ignore if corrupted
        storage_state = None
        if _STORAGE_FILE.exists():
            try:
                import json
                json.loads(_STORAGE_FILE.read_text(encoding="utf-8"))  # validate
                storage_state = str(_STORAGE_FILE)
            except Exception:
                print("[Browser] ⚠️  Corrupted storage state — starting fresh")
                _STORAGE_FILE.unlink(missing_ok=True)

        self._context = await self._browser.new_context(
            no_viewport=True,           # ✅ correct API (not viewport=None)
            storage_state=storage_state,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await self._context.new_page()
        self._pages.append(page)

    async def _save_storage(self):
        if self._context:
            try:
                await self._context.storage_state(path=str(_STORAGE_FILE))
            except Exception:
                pass

    async def _close(self):
        await self._save_storage()
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._pages   = []
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    # ── Text input ────────────────────────────────────────────────────────

    async def _fill_text(self, locator, text: str) -> None:
        """
        Fills text into an element using the most reliable method available.
        Priority: fill() → type() → clipboard inject
        fill() works for <input> / <textarea> and supports any Unicode.
        """
        try:
            await locator.fill(text, timeout=5000)
            return
        except Exception:
            pass
        try:
            await locator.clear()
            await locator.type(text, delay=30)
            return
        except Exception:
            pass
        # Last resort: clipboard inject via JS
        try:
            page = await self._active_page()
            await page.evaluate(
                f"navigator.clipboard.writeText({repr(text)})"
            )
            await locator.focus()
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Control+v")
        except Exception:
            pass

    # ── Navigation ─────────────────────────────────────────────────────────

    async def _go_to(self, url: str, retries: int = 1) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page = await self._active_page()
        for attempt in range(retries + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                return f"Opened: {page.url}"
            except PlaywrightTimeout:
                if attempt < retries:
                    print("[Browser] ⏱ Timeout — retrying...")
                    await asyncio.sleep(1.5)
                else:
                    # Try networkidle fallback
                    try:
                        await page.goto(url, wait_until="load", timeout=25000)
                        return f"Opened (slow): {page.url}"
                    except Exception:
                        return f"Timeout loading: {url}"
            except Exception as e:
                return f"Navigation error: {e}"

    async def _search(self, query: str, engine: str = "google") -> str:
        import urllib.parse
        q = urllib.parse.quote_plus(query)
        engines = {
            "google":     f"https://www.google.com/search?q={q}",
            "bing":       f"https://www.bing.com/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?q={q}",
            "youtube":    f"https://www.youtube.com/results?search_query={q}",
        }
        url = engines.get(engine.lower(), engines["google"])
        return await self._go_to(url)

    async def _go_back(self) -> str:
        page = await self._active_page()
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=10000)
            return f"Back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def _go_forward(self) -> str:
        page = await self._active_page()
        try:
            await page.go_forward(wait_until="domcontentloaded", timeout=10000)
            return f"Forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def _reload(self) -> str:
        page = await self._active_page()
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            return f"Reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    # ── Tab management ─────────────────────────────────────────────────────

    async def _new_tab(self, url: str = "") -> str:
        if not self._context:
            await self._active_page()
        page = await self._context.new_page()
        self._pages.append(page)
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return f"New tab: {page.url}"
        return "New tab opened."

    async def _close_tab(self) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if len(self._pages) <= 1:
            return await self._close_browser()
        page = self._pages.pop()
        await page.close()
        return f"Tab closed. Remaining: {len(self._pages)}"

    async def _switch_tab(self, index: int) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if 0 <= index < len(self._pages):
            await self._pages[index].bring_to_front()
            self._pages.append(self._pages.pop(index))
            return f"Tab {index} active: {self._pages[-1].url}"
        return f"No tab at index {index}. Total: {len(self._pages)}"

    # ── Interaction ────────────────────────────────────────────────────────

    async def _click(self, selector=None, text=None) -> str:
        page = await self._active_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
                return f"Clicked: '{text}'"
            elif selector:
                await page.locator(selector).first.click(timeout=8000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found or not clickable."
        except Exception as e:
            return f"Click error: {e}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True) -> str:
        page = await self._active_page()
        try:
            if selector:
                el = page.locator(selector).first
            else:
                # Find currently focused element
                el = page.locator("*:focus").first
            if clear_first:
                try:
                    await el.clear(timeout=3000)
                except Exception:
                    pass
            await self._fill_text(el, text)
            return "Text entered."
        except Exception as e:
            return f"Type error: {e}"

    async def _scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._active_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _press(self, key: str) -> str:
        page = await self._active_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def _fill_form(self, fields: dict) -> str:
        page    = await self._active_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await self._fill_text(el, str(value))
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    # ── Smart element finders ──────────────────────────────────────────────

    async def _smart_click(self, description: str) -> str:
        page       = await self._active_page()
        desc_lower = description.lower()

        role_map = {
            "button":    ["button", "btn", "submit", "send", "ok", "confirm"],
            "link":      ["link"],
            "searchbox": ["search", "find"],
            "textbox":   ["input", "field", "box", "text"],
            "checkbox":  ["check", "tick"],
        }
        strategies = []
        for role, keywords in role_map.items():
            if any(k in desc_lower for k in keywords):
                strategies.append(("role", role))
        strategies += [
            ("text",        description),
            ("placeholder", description),
            ("label",       description),
            ("title",       description),
        ]

        for strategy, val in strategies:
            try:
                if strategy == "role":
                    await page.get_by_role(val).first.click(timeout=4000)
                elif strategy == "text":
                    await page.get_by_text(val, exact=False).first.click(timeout=4000)
                elif strategy == "placeholder":
                    await page.get_by_placeholder(val, exact=False).first.click(timeout=4000)
                elif strategy == "label":
                    await page.get_by_label(val, exact=False).first.click(timeout=4000)
                elif strategy == "title":
                    await page.locator(f'[title*="{val}"]').first.click(timeout=4000)
                return f"Clicked ({strategy}): '{description}'"
            except Exception:
                continue

        # AI fallback
        try:
            sel = await self._ai_find_selector(description)
            if sel:
                await page.locator(sel).first.click(timeout=5000)
                return f"Clicked (AI): '{description}'"
        except Exception:
            pass

        return f"Could not find element: '{description}'"

    async def _smart_type(self, description: str, text: str) -> str:
        page = await self._active_page()
        for method, locator in [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox")),
        ]:
            try:
                el = locator.first
                await self._fill_text(el, text)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue
        return f"Could not find input: '{description}'"

    # ── Info / utility ─────────────────────────────────────────────────────

    async def _wait_for(self, selector: str = "", text: str = "", timeout_ms: int = 10000) -> str:
        page = await self._active_page()
        try:
            if selector:
                await page.wait_for_selector(selector, timeout=timeout_ms)
                return f"Element appeared: {selector}"
            elif text:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=timeout_ms)
                return f"Text appeared: '{text}'"
            return "No selector or text specified."
        except PlaywrightTimeout:
            return "Timed out waiting for element."
        except Exception as e:
            return f"Wait error: {e}"

    async def _get_text(self, max_chars: int = 4000) -> str:
        page = await self._active_page()
        try:
            text = await page.inner_text("body")
            return text[:max_chars]
        except Exception as e:
            return f"Could not get text: {e}"

    async def _get_url(self) -> str:
        page = await self._active_page()
        return page.url

    async def _get_title(self) -> str:
        page = await self._active_page()
        return await page.title()

    async def _extract_links(self, filter_text: str = "") -> str:
        page = await self._active_page()
        try:
            anchors = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
            )
            links = [
                f"{a['text'] or '(no text)'}: {a['href']}"
                for a in anchors
                if a["href"] and (not filter_text or filter_text.lower() in a["text"].lower())
            ]
            return "\n".join(links[:50]) if links else "No links found."
        except Exception as e:
            return f"Link extraction error: {e}"

    async def _screenshot(self, path: str = "") -> str:
        page = await self._active_page()
        try:
            save_path = Path(path) if path else Path.home() / "Desktop" / "atlas_screenshot.png"
            await page.screenshot(path=str(save_path), full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    # ── AI selector fallback ───────────────────────────────────────────────

    async def _ai_find_selector(self, description: str) -> "str | None":
        try:
            import json
            page    = await self._active_page()
            snippet = (await page.inner_html("body"))[:3000]
            cfg     = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
            api_key = json.loads(cfg.read_text())["gemini_api_key"]

            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            resp  = model.generate_content(
                f"Return ONLY a CSS selector for: '{description}'\n\nHTML:\n{snippet}\n\nCSS selector:"
            )
            return resp.text.strip().strip("`").strip()
        except Exception:
            return None

    async def _close_browser(self) -> str:
        await self._close()
        return "Browser closed."


# ─── Singleton ────────────────────────────────────────────────────────────────

_bt         = _BrowserThread()
_bt_started = False
_bt_lock    = threading.Lock()


def _ensure_started():
    global _bt_started
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


# ─── Public entry point ───────────────────────────────────────────────────────

def browser_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Browser controller — auto-detects system browser, falls back to built-in Chromium.

    Actions:
        go_to        | search (query, engine: google|bing|duckduckgo|youtube)
        back         | forward | reload
        new_tab (url)| close_tab | switch_tab (index)
        click (selector or text) | type (selector, text, clear_first)
        scroll (direction, amount) | press (key) | fill_form (fields)
        smart_click (description) | smart_type (description, text)
        wait_for (selector or text, timeout_ms)
        get_text | get_url | get_title | extract_links (filter_text) | screenshot (path)
        close
    """
    _ensure_started()

    params = parameters or {}
    action = params.get("action", "").lower().strip()
    result = f"Unknown action: '{action}'"

    try:
        if action == "go_to":
            result = _bt.run(_bt._go_to(params.get("url", "")))
        elif action == "search":
            result = _bt.run(_bt._search(params.get("query", ""), params.get("engine", "google")))
        elif action == "back":
            result = _bt.run(_bt._go_back())
        elif action == "forward":
            result = _bt.run(_bt._go_forward())
        elif action == "reload":
            result = _bt.run(_bt._reload())
        elif action == "new_tab":
            result = _bt.run(_bt._new_tab(params.get("url", "")))
        elif action == "close_tab":
            result = _bt.run(_bt._close_tab())
        elif action == "switch_tab":
            result = _bt.run(_bt._switch_tab(int(params.get("index", 0))))
        elif action == "click":
            result = _bt.run(_bt._click(selector=params.get("selector"), text=params.get("text")))
        elif action == "type":
            result = _bt.run(_bt._type(
                selector=params.get("selector"),
                text=params.get("text", ""),
                clear_first=params.get("clear_first", True),
            ))
        elif action == "scroll":
            result = _bt.run(_bt._scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = _bt.run(_bt._fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = _bt.run(_bt._smart_click(params.get("description", "")), timeout=20)
        elif action == "smart_type":
            result = _bt.run(_bt._smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "wait_for":
            result = _bt.run(_bt._wait_for(
                selector=params.get("selector", ""),
                text=params.get("text", ""),
                timeout_ms=int(params.get("timeout_ms", 10000)),
            ))
        elif action == "get_text":
            result = _bt.run(_bt._get_text())
        elif action == "get_url":
            result = _bt.run(_bt._get_url())
        elif action == "get_title":
            result = _bt.run(_bt._get_title())
        elif action == "extract_links":
            result = _bt.run(_bt._extract_links(params.get("filter_text", "")))
        elif action == "screenshot":
            result = _bt.run(_bt._screenshot(params.get("path", "")))
        elif action == "press":
            result = _bt.run(_bt._press(params.get("key", "Enter")))
        elif action == "close":
            result = _bt.run(_bt._close_browser())

    except concurrent.futures.TimeoutError:
        result = "Browser action timed out (45s limit)."
    except Exception as e:
        result = f"Browser error: {e}"

    print(f"[Browser] {result[:120]}")
    if player:
        player.write_log(f"[browser] {result[:60]}")

    return result