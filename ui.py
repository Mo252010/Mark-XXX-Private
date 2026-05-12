import os, json, time, math, random, sounddevice as sd
import tkinter as tk
from collections import deque
from PIL import Image, ImageTk, ImageDraw
import sys
from pathlib import Path

from utils import rtl_visual_embed, contains_arabic


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR   = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

SYSTEM_NAME = "A.T.L.A.S"
MODEL_BADGE = "A.T.L.A.S."

SETTINGS_FILE = CONFIG_DIR / "settings.json"

TOP_BAR_H = 46
UI_FONT_CHAT = ("Segoe UI", 10)

THEMES = {
    "Dark": {
        # Deep space + neon cyan / خلفية فضائية داكنة ولمسات سيان نيون
        "C_BG": "#02060c", "C_PRI": "#00fff2", "C_MID": "#008799",
        "C_DIM": "#062030", "C_DIMMER": "#030810", "C_ACC": "#00fff2",
        "C_ACC2": "#010408", "C_TEXT": "#d5f8ff", "C_PANEL": "#050a12",
        "RGB_PRI": (0, 255, 242),
    },
    "Light": {
        "C_BG": "#f0f4f8", "C_PRI": "#0077cc", "C_MID": "#5599bb",
        "C_DIM": "#ccdde8", "C_DIMMER": "#e4ecf2", "C_ACC": "#0077cc",
        "C_ACC2": "#f0f4f8", "C_TEXT": "#1a2a3a", "C_PANEL": "#e8eef4",
        "RGB_PRI": (0, 119, 204),
    },
    "Cyber": {
        "C_BG": "#0a0008", "C_PRI": "#ff003c", "C_MID": "#9d0031",
        "C_DIM": "#3a001a", "C_DIMMER": "#1a000d", "C_ACC": "#00ffd0",
        "C_ACC2": "#0a0004", "C_TEXT": "#ffc8d8", "C_PANEL": "#12000a",
        "RGB_PRI": (255, 0, 60),
    },
}

# Default colors — overwritten by _apply_theme()
C_BG     = "#060610"
C_PRI    = "#00e5ff"    # Electric Cyan
C_MID    = "#00708a"
C_DIM    = "#0a2a3a"
C_DIMMER = "#060d14"
C_ACC    = "#00e5ff"
C_ACC2   = "#000408"
C_TEXT   = "#d0eaf4"
C_PANEL  = "#08080f"
C_GREEN  = "#00ff88"
C_AMBER  = "#ffbf00"
C_RED    = "#ff3040"


def _load_api_config():
    try:
        with open(API_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _has_valid_api_key() -> bool:
    data = _load_api_config()
    provider = str(data.get("provider", "gemini")).strip().lower()
    gemini_key = str(data.get("gemini_api_key", "")).strip()
    openrouter_key = str(data.get("openrouter_api_key", "")).strip()

    if provider == "openrouter":
        return bool(openrouter_key and "YOUR_API_KEY_HERE" not in openrouter_key)

    return bool(
        (gemini_key and "YOUR_API_KEY_HERE" not in gemini_key) or
        (openrouter_key and "YOUR_API_KEY_HERE" not in openrouter_key)
    )

class AtlasUI:
    """Main Tk cockpit — العربية تعرض عبر Rtl embed على النص حيث يلزم."""

    def __init__(self, face_path, size=None):
        self.root = tk.Tk()
        self.root.title("A.T.L.A.S. — Intelligence System")
        self.root.state('zoomed')
        self.root.resizable(True, True)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        W  = sw
        H  = sh
        self.root.geometry(f"{W}x{H}+0+0")
        self.root.configure(bg=C_BG)

        self.W = W
        self.H = H

        # Define sizes and coordinates early
        self.side_width = 220
        _avail0 = H - TOP_BAR_H
        self.FACE_SZ = min(int(_avail0 * 0.54), 400)
        self.FCX = self.side_width + (W - self.side_width) // 2
        self.FCY = TOP_BAR_H + int(_avail0 * 0.13) + self.FACE_SZ // 2
        self.pulse_r = [0.0, self.FACE_SZ * 0.26, self.FACE_SZ * 0.52]

        self._connect_wait = True
        self._link_var = tk.StringVar(value="STANDBY · جاهز للربط")
        self._mic_pulse_phase = 0.0

        self.speaking     = False
        self.scale        = 1.0
        self.target_scale = 1.0
        self.halo_a       = 60.0
        self.target_halo  = 60.0
        self.last_t       = time.time()
        self.tick         = 0
        self.scan_angle   = 0.0
        self.scan2_angle  = 180.0
        self.rings_spin   = [0.0, 120.0, 240.0]
        self.status_text  = "INITIALISING"
        self.status_blink = True
        self.audio_input_index = None
        self.audio_output_index = None

        self.typing_queue = deque()
        self.is_typing    = False

        # Waveform data for interactive voice line
        self.wave_points  = [0.0] * 64
        self.wave_target  = [0.0] * 64

        self._face_pil         = None
        self._has_face         = False
        self._face_scale_cache = None
        self._load_face(face_path)

        self.language = "English"
        self.theme = "Dark"
        self.rgb_pri = THEMES["Dark"]["RGB_PRI"]
        self._load_settings()

        # Current UI mode: "voice" or "chat"
        self.current_mode = "voice"

        self.root.bind('<Configure>', self._on_resize)

        # ─── Futuristic top bar (full-width system strip) ───
        self.top_bar = tk.Frame(
            self.root, bg=C_DIMMER, height=TOP_BAR_H,
            highlightbackground=C_DIM, highlightthickness=1
        )
        self.top_bar.place(x=0, y=0, relwidth=1.0, height=TOP_BAR_H)
        tk.Label(
            self.top_bar, text="◈ ATLAS", fg=C_PRI, bg=C_DIMMER,
            font=("Segoe UI", 13, "bold")
        ).place(x=12, rely=0.45, anchor="w")
        tk.Label(
            self.top_bar,
            text="Neural cockpit · جهاز التشغيل الصوتي",
            fg=C_MID, bg=C_DIMMER, font=("Segoe UI", 8),
        ).place(x=120, rely=0.55, anchor="w")
        self._lbl_top_status = tk.Label(
            self.top_bar, textvariable=self._link_var, fg=C_AMBER,
            bg=C_DIMMER, font=("Consolas", 9),
        )
        self._lbl_top_status.place(relx=1.0, x=-14, rely=0.5, anchor="e")
        
        # --- Side menu ---
        self.side_frame = tk.Frame(self.root, bg=C_PANEL, highlightbackground=C_DIM, highlightthickness=1)
        self.side_frame.place(x=0, y=TOP_BAR_H, width=self.side_width, height=H - TOP_BAR_H)

        self._build_side_menu()

        # --- Controls (Stop / Mute) ---
        self.controls_frame = tk.Frame(self.root, bg=C_BG, bd=0)
        self._btn_stop = tk.Button(self.controls_frame, text="⏹ STOP", command=self._on_stop_click,
                                  bg=C_PANEL, fg=C_RED, bd=1, highlightthickness=0,
                                  activebackground=C_DIM, activeforeground="#ffffff",
                                  font=("Consolas", 10, "bold"), width=8, cursor="hand2")
        self._btn_stop.pack(side="left", padx=5)

        self._btn_mute = tk.Button(self.controls_frame, text="🎤 MUTE", command=self._on_mute_click,
                                  bg=C_PANEL, fg=C_PRI, bd=1, highlightthickness=0,
                                  activebackground=C_DIM, activeforeground=C_ACC,
                                  font=("Consolas", 10, "bold"), width=8, cursor="hand2")
        self._btn_mute.pack(side="left", padx=5)
        self.settings_frame = tk.Frame(self.root, bg=C_PANEL, highlightbackground=C_MID, highlightthickness=1)
        self._build_settings_menu()
        
        # --- Main canvas for voice mode ---
        self.bg = tk.Canvas(self.root, width=W - self.side_width, height=H - TOP_BAR_H,
                            bg=C_BG, highlightthickness=0)
        self.bg.place(x=self.side_width, y=TOP_BAR_H, width=W - self.side_width, height=H - TOP_BAR_H)

        # --- Chat mode widgets ---
        content_w = W - self.side_width
        LW = int(content_w * 0.72)
        LH = H - 140  # Most of the screen for chat log

        self.log_frame = tk.Frame(self.root, bg=C_PANEL, highlightbackground=C_DIM, highlightthickness=1)

        scroll_wrap = tk.Frame(self.log_frame, bg=C_PANEL)
        scroll_wrap.pack(fill="both", expand=True)
        log_sb = tk.Scrollbar(scroll_wrap, orient="vertical", bg=C_PANEL, troughcolor=C_DIMMER)
        self.log_text = tk.Text(scroll_wrap, fg=C_TEXT, bg=C_PANEL,
                                insertbackground=C_TEXT, borderwidth=0,
                                wrap="word", font=UI_FONT_CHAT, padx=12, pady=8,
                                spacing1=2, spacing3=4, selectbackground=C_DIM,
                                yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb.config(command=self.log_text.yview)

        self.log_text.configure(state="disabled")

        bubble_you_bg = "#0e2438"
        bubble_ai_bg = "#071c28"
        self.log_text.tag_config(
            "you", foreground=C_TEXT, background=bubble_you_bg,
            font=(UI_FONT_CHAT[0], UI_FONT_CHAT[1], "bold"),
            lmargin1=16, rmargin=18, spacing1=8, spacing3=10,
            selectbackground=C_DIM)
        self.log_text.tag_config(
            "ai", foreground=C_PRI, background=bubble_ai_bg,
            font=(UI_FONT_CHAT[0], UI_FONT_CHAT[1]),
            lmargin1=48, rmargin=12, spacing1=8, spacing3=10,
            selectbackground=C_DIM)
        self.log_text.tag_config("sys",  foreground=C_MID, font=UI_FONT_CHAT,
                                 lmargin1=8, spacing1=6, spacing3=4)
        self.log_text.tag_config("warn", foreground=C_AMBER, font=UI_FONT_CHAT,
                                 lmargin1=8, spacing1=6, spacing3=4)
        self.log_text.tag_config("err", foreground=C_RED, font=UI_FONT_CHAT, lmargin1=8)
        self.log_text.tag_config("boot", foreground=C_GREEN, font=UI_FONT_CHAT,
                                 background="#051018", spacing1=4)

        self.chat_entry = tk.Entry(self.root, bg=C_PANEL, fg=C_TEXT, insertbackground=C_ACC,
                                   borderwidth=0, highlightbackground=C_DIM, highlightthickness=1,
                                   font=("Segoe UI", 11), relief="flat")
        self.chat_entry.bind("<Return>", self._on_chat_enter)
        self.chat_callback = None

        # Lift side menu above everything
        self.side_frame.lift()

        # Start in voice mode
        self._apply_mode()

        self._api_key_ready = self._api_keys_exist()
        if not self._api_key_ready:
            self._show_setup_ui()

        self._animate()
        self.root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))

    def _load_face(self, path):
        FW = self.FACE_SZ
        try:
            img  = Image.open(path).convert("RGBA").resize((FW, FW), Image.LANCZOS)
            mask = Image.new("L", (FW, FW), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, FW - 2, FW - 2), fill=255)
            img.putalpha(mask)
            self._face_pil = img
            self._has_face = True
        except Exception:
            self._has_face = False

    def _ac(self, r, g, b, a):
        f = a / 255.0
        bg_hex = C_BG.lstrip('#')
        bg_r = int(bg_hex[0:2], 16)
        bg_g = int(bg_hex[2:4], 16)
        bg_b = int(bg_hex[4:6], 16)
        nr = int(r * f + bg_r * (1 - f))
        ng = int(g * f + bg_g * (1 - f))
        nb = int(b * f + bg_b * (1 - f))
        return f"#{nr:02x}{ng:02x}{nb:02x}"

    # ─── Mode switching ──────────────────────────────────────
    def _apply_mode(self):
        """Switch voice vs chat layout; voice canvas sits under the global top bar."""
        W, H = self.W, self.H
        content_w = W - self.side_width
        LW = int(content_w * 0.72)
        cx = self.side_width + (content_w - LW) // 2
        avail_h = H - TOP_BAR_H

        if self.current_mode == "voice":
            # Show canvas, hide chat widgets
            self.bg.place(x=self.side_width, y=TOP_BAR_H, width=content_w, height=avail_h)
            self.log_frame.place_forget()
            self.chat_entry.place_forget()
        else:
            # Chat mode: show log + entry, hide canvas
            self.bg.place_forget()
            LH = avail_h - 120
            gap = 72
            self.log_frame.place(x=cx, y=TOP_BAR_H + gap, width=LW, height=LH)
            self.chat_entry.place(x=cx, y=TOP_BAR_H + gap + LH + 10, width=LW, height=32)
            self.log_frame.lift()
            self.chat_entry.lift()
            self.chat_entry.focus()

        self.side_frame.lift()
        self.top_bar.lift()
        self.controls_frame.place(x=self.side_width + 20, y=H - 54)
        self.controls_frame.lift()

    def _on_stop_click(self):
        self.stop_speaking()
        self.status_text = "READY"
        self.write_log("SYS: Manual stop triggered.", "warn")
        if hasattr(self, "on_stop_callback") and self.on_stop_callback:
            self.on_stop_callback()

    def _on_mute_click(self):
        if hasattr(self, "on_mute_callback") and self.on_mute_callback:
            is_muted = self.on_mute_callback() # Should return new state
            self.set_mute_state(is_muted)

    def set_mute_state(self, is_muted: bool):
        """Update the Mute button UI to reflect the current state."""
        if is_muted:
            self._btn_mute.configure(text="🔇 UNMUTE", fg=C_AMBER)
        else:
            self._btn_mute.configure(text="🎤 MUTE", fg=C_PRI)

    def _mode_voice(self):
        self.settings_frame.place_forget()
        self.current_mode = "voice"
        self._apply_mode()
        self._update_side_highlight()
        
    def _mode_chat(self):
        self.settings_frame.place_forget()
        self.current_mode = "chat"
        self._apply_mode()
        self._update_side_highlight()
        
    def _mode_settings(self):
        self.settings_frame.place(x=self.side_width + 20, y=TOP_BAR_H + 80, width=260, height=340)
        self.settings_frame.lift()

    # ─── Animation ────────────────────────────────────────────
    def _animate(self):
        self.tick += 1
        t   = self.tick
        now = time.time()

        if now - self.last_t > (0.14 if self.speaking else 0.55):
            if self.speaking:
                self.target_scale = random.uniform(1.05, 1.11)
                self.target_halo  = random.uniform(138, 182)
            else:
                self.target_scale = random.uniform(1.001, 1.007)
                self.target_halo  = random.uniform(50, 68)
            self.last_t = now

        sp = 0.35 if self.speaking else 0.16
        self.scale  += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo  - self.halo_a) * sp

        for i, spd in enumerate([1.2, -0.8, 1.9] if self.speaking else [0.5, -0.3, 0.82]):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360

        self.scan_angle  = (self.scan_angle  + (2.8 if self.speaking else 1.2)) % 360
        self.scan2_angle = (self.scan2_angle + (-1.7 if self.speaking else -0.68)) % 360

        pspd  = 3.8 if self.speaking else 1.8
        limit = self.FACE_SZ * 0.72
        new_p = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(new_p) < 3 and random.random() < (0.06 if self.speaking else 0.022):
            new_p.append(0.0)
        self.pulse_r = new_p

        if t % 40 == 0:
            self.status_blink = not self.status_blink

        self._mic_pulse_phase = (self._mic_pulse_phase + 0.12) % (2 * math.pi)

        # Update waveform
        for i in range(len(self.wave_points)):
            if self.speaking:
                self.wave_target[i] = random.uniform(-1.0, 1.0)
            else:
                self.wave_target[i] = 0.3 * math.sin(t * 0.04 + i * 0.3)
            self.wave_points[i] += (self.wave_target[i] - self.wave_points[i]) * 0.25

        if self.current_mode == "voice":
            self._draw()
        self.root.after(16, self._animate)

    # ─── Draw (voice mode) ────────────────────────────────────
    def _draw(self):
        c    = self.bg
        W    = int(c.winfo_width()) or (self.W - self.side_width)
        H    = int(c.winfo_height()) or self.H
        t    = self.tick
        FCX  = W // 2
        FCY  = int(H * 0.13) + self.FACE_SZ // 2
        FW   = self.FACE_SZ
        c.delete("all")

        # Dot grid
        for x in range(0, W, 44):
            for y in range(0, H, 44):
                c.create_rectangle(x, y, x+1, y+1, fill=C_DIMMER, outline="")

        # Halo rings
        for r in range(int(FW * 0.54), int(FW * 0.28), -22):
            frac = 1.0 - (r - FW * 0.28) / (FW * 0.26)
            ga   = max(0, min(255, int(self.halo_a * 0.09 * frac)))
            c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                          outline=self._ac(*self.rgb_pri, ga), width=2)

        # Pulse rings
        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            r  = int(pr)
            c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                          outline=self._ac(*self.rgb_pri, pa), width=2)

        # Spinning arcs
        for idx, (r_frac, w_ring, arc_l, gap) in enumerate([
                (0.47, 3, 110, 75), (0.39, 2, 75, 55), (0.31, 1, 55, 38)]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx]
            a_val  = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.18))))
            col    = self._ac(*self.rgb_pri, a_val)
            for s in range(360 // (arc_l + gap)):
                start = (base_a + s * (arc_l + gap)) % 360
                c.create_arc(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r,
                             start=start, extent=arc_l,
                             outline=col, width=w_ring, style="arc")

        # Scanner arcs
        sr      = int(FW * 0.49)
        scan_a  = min(255, int(self.halo_a * 1.4))
        arc_ext = 70 if self.speaking else 42
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan_angle, extent=arc_ext,
                     outline=self._ac(*self.rgb_pri, scan_a), width=3, style="arc")
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan2_angle, extent=arc_ext,
                     outline=self._ac(*self.rgb_pri, scan_a // 2), width=2, style="arc")

        # Tick marks
        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        a_mk  = self._ac(*self.rgb_pri, 155)
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 5
            c.create_line(FCX + t_out * math.cos(rad), FCY - t_out * math.sin(rad),
                          FCX + inn  * math.cos(rad), FCY - inn  * math.sin(rad),
                          fill=a_mk, width=1)

        # Crosshair
        ch_r = int(FW * 0.50)
        gap  = int(FW * 0.15)
        ch_a = self._ac(*self.rgb_pri, int(self.halo_a * 0.55))
        for x1, y1, x2, y2 in [
                (FCX - ch_r, FCY, FCX - gap, FCY), (FCX + gap, FCY, FCX + ch_r, FCY),
                (FCX, FCY - ch_r, FCX, FCY - gap), (FCX, FCY + gap, FCX, FCY + ch_r)]:
            c.create_line(x1, y1, x2, y2, fill=ch_a, width=1)

        # Corner brackets
        blen = 22
        bc   = self._ac(*self.rgb_pri, 200)
        hl = FCX - FW // 2; hr = FCX + FW // 2
        ht = FCY - FW // 2; hb = FCY + FW // 2
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            c.create_line(bx, by, bx + sdx * blen, by,            fill=bc, width=2)
            c.create_line(bx, by, bx,               by + sdy * blen, fill=bc, width=2)

        # Face image or fallback orb
        if self._has_face:
            fw = int(FW * self.scale)
            if (self._face_scale_cache is None or
                    abs(self._face_scale_cache[0] - self.scale) > 0.004):
                scaled = self._face_pil.resize((fw, fw), Image.BILINEAR)
                tk_img = ImageTk.PhotoImage(scaled)
                self._face_scale_cache = (self.scale, tk_img)
            c.create_image(FCX, FCY, image=self._face_scale_cache[1])
        else:
            orb_r = int(FW * 0.27 * self.scale)
            for i in range(7, 0, -1):
                r2   = int(orb_r * i / 7)
                frac = i / 7
                ga   = max(0, min(255, int(self.halo_a * 1.1 * frac)))
                c.create_oval(FCX-r2, FCY-r2, FCX+r2, FCY+r2,
                              fill=self._ac(*self.rgb_pri, int(ga * 0.5)),
                              outline="")
            c.create_text(FCX, FCY, text=SYSTEM_NAME,
                          fill=self._ac(*self.rgb_pri, min(255, int(self.halo_a * 2))),
                          font=("Consolas", 14, "bold"))

        # Clock + mini badge (top bar holds brand; canvas shows live telemetry)
        c.create_text(W - 12, 22, text=time.strftime("%H:%M:%S"),
                      fill=C_PRI, font=("Consolas", 12, "bold"), anchor="e")
        c.create_text(12, 20, text=MODEL_BADGE, fill=C_DIM, font=("Consolas", 7), anchor="w")

        if getattr(self, "_connect_wait", False):
            sx, syw = W // 2, 48
            c.create_rectangle(sx - 190, syw - 16, sx + 190, syw + 16,
                               fill=C_ACC2, outline=self._ac(*self.rgb_pri, 90), width=1)
            c.create_text(sx, syw, fill=C_AMBER,
                          font=("Segoe UI", 9),
                          text="SYNC • جاري الربط بالشبكة العصبية…")

        # Mic capture indicator — expands while LISTENING / مؤشر التقاط صوتي نابض
        if self.status_text == "LISTENING":
            mx, my = 54, H - 96
            pr = 10 + 6 * (0.5 + 0.5 * math.sin(self._mic_pulse_phase))
            c.create_oval(mx - pr, my - pr, mx + pr, my + pr,
                          outline=self._ac(*self.rgb_pri, 140), width=2)
            c.create_oval(mx - 5, my - 5, mx + 5, my + 5, fill=C_GREEN, outline=C_PRI)
            c.create_text(mx + 52, my, text="MIC · LIVE", fill=C_GREEN,
                          font=("Consolas", 8, "bold"), anchor="w")

        # ─── Status indicator ─────────────────────────────────
        sy = FCY + FW // 2 + 38
        if self.speaking:
            stat, sc = "◉ SPEAKING", C_ACC
        elif self.status_text == "LISTENING":
            stat, sc = "● LISTENING", C_GREEN
        elif self.status_text == "[MUTED]":
            stat, sc = "○ MUTED", C_AMBER
        elif "PROCESS" in self.status_text.upper() or "RESPOND" in self.status_text.upper():
            stat, sc = f"◎ {self.status_text}", C_AMBER
        else:
            sym = "●" if self.status_blink else "○"
            stat, sc = f"{sym} {self.status_text}", C_PRI

        # Subtle glitch: randomly corrupt a character for sci-fi feel
        current_stat = stat
        if random.random() < 0.04:
            chars = list(stat)
            if len(chars) > 2:
                idx = random.randint(1, len(chars) - 1)
                chars[idx] = random.choice(["_", "!", "#", "@", "█", "▓", "░"])
                current_stat = "".join(chars)

        c.create_text(W // 2, sy, text=current_stat,
                      fill=sc, font=("Consolas", 11, "bold"))

        # ─── Interactive waveform line ─────────────────────────
        wy = sy + 32
        wave_w = int(W * 0.48)
        wx0 = (W - wave_w) // 2
        n = len(self.wave_points)
        step = wave_w / (n - 1) if n > 1 else wave_w
        amp = 32 if self.speaking else 12

        pts = []
        for i in range(n):
            px = wx0 + i * step
            py = wy + self.wave_points[i] * amp
            pts.append(px)
            pts.append(py)

        if len(pts) >= 4:
            wave_alpha = min(255, int(self.halo_a * 1.5)) if self.speaking else min(255, int(self.halo_a * 0.7))
            # Triple-layer glow: outer soft, mid, sharp line
            c.create_line(*pts, fill=self._ac(*self.rgb_pri, wave_alpha // 5), width=8, smooth=True)
            c.create_line(*pts, fill=self._ac(*self.rgb_pri, wave_alpha // 3), width=4, smooth=True)
            c.create_line(*pts, fill=self._ac(*self.rgb_pri, wave_alpha), width=2, smooth=True)

        # End caps and center dot on waveform
        for cap_i in [0, n - 1]:
            cap_x = wx0 + cap_i * step
            cap_y = wy + self.wave_points[cap_i] * amp
            c.create_oval(cap_x - 2, cap_y - 2, cap_x + 2, cap_y + 2,
                          fill=self._ac(*self.rgb_pri, 100), outline="")
        mid_i = n // 2
        mid_x = wx0 + mid_i * step
        mid_y = wy + self.wave_points[mid_i] * amp
        dot_r = 5 if self.speaking else 3
        c.create_oval(mid_x - dot_r, mid_y - dot_r, mid_x + dot_r, mid_y + dot_r,
                      fill=C_PRI, outline="")

        # ─── Footer ───────────────────────────────────────────
        FTR = 26
        c.create_rectangle(0, H - FTR, W, H, fill=C_PANEL, outline="")
        c.create_line(0, H - FTR, W, H - FTR, fill=C_DIM, width=1)
        c.create_line(W // 2 - 160, H - FTR, W // 2 + 160, H - FTR,
                      fill=self._ac(*self.rgb_pri, 40), width=1)
        c.create_text(W // 2, H - FTR // 2, fill=C_MID, font=("Consolas", 7),
                      text="A.T.L.A.S.  ·  NEURAL INTERFACE  ·  CLASSIFIED")

    # ─── Settings ─────────────────────────────────────────────
    def _load_settings(self):
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    self.language = data.get("language", "English")
                    self.theme = data.get("theme", "Dark")
                    self.audio_input_index = data.get("audio_input_index", None)
                    self.audio_output_index = data.get("audio_output_index", None)
            except: pass
        self._apply_theme(self.theme)

    def _save_settings(self):
        data = {
            "language": self.language, 
            "theme": self.theme,
            "audio_input_index": self.audio_input_index,
            "audio_output_index": self.audio_output_index
        }
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f)

    def _apply_theme(self, name):
        if name not in THEMES: return
        t = THEMES[name]
        global C_BG, C_PRI, C_MID, C_DIM, C_DIMMER, C_ACC, C_ACC2, C_TEXT, C_PANEL
        C_BG, C_PRI, C_MID, C_DIM, C_DIMMER = t["C_BG"], t["C_PRI"], t["C_MID"], t["C_DIM"], t["C_DIMMER"]
        C_ACC, C_ACC2, C_TEXT, C_PANEL = t["C_ACC"], t["C_ACC2"], t["C_TEXT"], t["C_PANEL"]
        self.rgb_pri = t["RGB_PRI"]
        
        try:
            self.root.configure(bg=C_BG)
            self.bg.configure(bg=C_BG)
            bb_y, bb_a = "#0e2438", "#071c28"
            self.log_frame.configure(bg=C_PANEL, highlightbackground=C_MID)
            self.log_text.configure(bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT)
            self.log_text.tag_config(
                "you", foreground=C_TEXT, background=bb_y,
                font=(UI_FONT_CHAT[0], UI_FONT_CHAT[1], "bold"),
                lmargin1=16, rmargin=18, spacing1=8, spacing3=10)
            self.log_text.tag_config(
                "ai", foreground=C_PRI, background=bb_a,
                font=UI_FONT_CHAT, lmargin1=48, rmargin=12,
                spacing1=8, spacing3=10)
            self.log_text.tag_config("sys", foreground=C_MID, font=UI_FONT_CHAT,
                                     lmargin1=8, spacing1=6, spacing3=4)
            self.log_text.tag_config("warn", foreground=C_AMBER, font=UI_FONT_CHAT,
                                     lmargin1=8, spacing1=6, spacing3=4)
            self.log_text.tag_config("boot", foreground=C_GREEN,
                                     background="#051018", spacing1=4)
            self.log_text.tag_config("err", foreground=C_RED, font=UI_FONT_CHAT)
            self.chat_entry.configure(bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT, highlightbackground=C_DIM)
            self.side_frame.configure(bg=C_PANEL, highlightbackground=C_DIM)
            self.settings_frame.configure(bg=C_PANEL, highlightbackground=C_MID)
            self.top_bar.configure(bg=C_DIMMER, highlightbackground=C_DIM)
            self._lbl_top_status.configure(bg=C_DIMMER, fg=C_AMBER)
            for wb in self.top_bar.winfo_children():
                if isinstance(wb, tk.Label) and wb is not self._lbl_top_status:
                    wb.configure(bg=C_DIMMER)
            # Update all child widgets
            for widget in self.side_frame.winfo_children() + self.settings_frame.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.configure(bg=C_PANEL, fg=C_PRI, activebackground=C_DIM, activeforeground=C_ACC)
                elif isinstance(widget, tk.Label):
                    widget.configure(bg=C_PANEL, fg=C_PRI)
            self._draw()
        except: pass

    def _build_side_menu(self):
        tk.Label(self.side_frame, text="◈ A.T.L.A.S.", fg=C_PRI, bg=C_PANEL, font=("Consolas", 13, "bold")).pack(pady=(20, 6))
        tk.Frame(self.side_frame, bg=C_DIM, height=1).pack(fill="x", padx=16, pady=(0, 12))

        self._voice_btn = tk.Button(self.side_frame, text="▸ Voice Mode", command=self._mode_voice,
                                     bg=C_PANEL, fg=C_PRI, bd=0, font=("Consolas", 10),
                                     activebackground=C_DIM, activeforeground=C_ACC, cursor="hand2")
        self._voice_btn.pack(fill="x", pady=4, padx=10)

        self._chat_btn = tk.Button(self.side_frame, text="  Chat Mode", command=self._mode_chat,
                                    bg=C_PANEL, fg=C_MID, bd=0, font=("Consolas", 10),
                                    activebackground=C_DIM, activeforeground=C_ACC, cursor="hand2")
        self._chat_btn.pack(fill="x", pady=4, padx=10)

        self._settings_btn = tk.Button(self.side_frame, text="  Settings", command=self._mode_settings,
                                        bg=C_PANEL, fg=C_MID, bd=0, font=("Consolas", 10),
                                        activebackground=C_DIM, activeforeground=C_ACC, cursor="hand2")
        self._settings_btn.pack(fill="x", pady=4, padx=10)

    def _update_side_highlight(self):
        """Highlight the active mode button."""
        labels = {self._voice_btn: "Voice Mode", self._chat_btn: "Chat Mode"}
        for btn, label in labels.items():
            mode = "voice" if btn is self._voice_btn else "chat"
            if self.current_mode == mode:
                btn.configure(fg=C_PRI, text=f"▸ {label}", font=("Consolas", 10, "bold"))
            else:
                btn.configure(fg=C_MID, text=f"  {label}", font=("Consolas", 10))

    def _build_settings_menu(self):
        tk.Label(self.settings_frame, text="─── SETTINGS ───", fg=C_PRI, bg=C_PANEL, font=("Courier", 12, "bold")).pack(pady=(12, 8))
        
        tk.Label(self.settings_frame, text="Language", fg=C_MID, bg=C_PANEL, font=("Courier", 10)).pack(pady=(8, 4))
        for lang in ["English", "Arabic", "Turkish"]:
            tk.Button(self.settings_frame, text=lang, command=lambda l=lang: self._set_lang(l),
                      bg=C_PANEL, fg=C_PRI, bd=0, font=("Courier", 10),
                      activebackground=C_DIM, activeforeground=C_ACC, cursor="hand2").pack(pady=1)
            
        tk.Label(self.settings_frame, text="Theme", fg=C_MID, bg=C_PANEL, font=("Courier", 10)).pack(pady=(15, 4))
        for t in THEMES.keys():
            tk.Button(self.settings_frame, text=t, command=lambda th=t: self._set_theme(th),
                      bg=C_PANEL, fg=C_PRI, bd=0, font=("Courier", 10),
                      activebackground=C_DIM, activeforeground=C_ACC, cursor="hand2").pack(pady=1)

        tk.Label(self.settings_frame, text="Microphone", fg=C_MID, bg=C_PANEL, font=("Courier", 10)).pack(pady=(15, 4))
        try:
            devices = sd.query_devices()
            input_devices = []
            for i, d in enumerate(devices):
                if d['max_input_channels'] > 0:
                    name = (d['name'][:27] + '..') if len(d['name']) > 27 else d['name']
                    input_devices.append((i, name))

            for idx, name in input_devices[:6]:
                color = C_PRI if self.audio_input_index == idx else C_MID
                tk.Button(self.settings_frame, text=f"• {name}",
                          command=lambda i=idx: self._set_audio_device(i),
                          bg=C_PANEL, fg=color, bd=0, font=("Courier", 9),
                          activebackground=C_DIM, cursor="hand2").pack(pady=0, anchor="w", padx=20)
        except Exception:
            tk.Label(self.settings_frame, text="No Mics Found", fg=C_RED, bg=C_PANEL, font=("Courier", 8)).pack()

        tk.Label(self.settings_frame, text="Speaker", fg=C_MID, bg=C_PANEL, font=("Courier", 10)).pack(pady=(15, 4))
        try:
            devices = sd.query_devices()
            output_devices = []
            for i, d in enumerate(devices):
                if d['max_output_channels'] > 0:
                    name = (d['name'][:27] + '..') if len(d['name']) > 27 else d['name']
                    output_devices.append((i, name))

            for idx, name in output_devices[:6]:
                color = C_PRI if self.audio_output_index == idx else C_MID
                tk.Button(self.settings_frame, text=f"• {name}",
                          command=lambda i=idx: self._set_audio_output_device(i),
                          bg=C_PANEL, fg=color, bd=0, font=("Courier", 9),
                          activebackground=C_DIM, cursor="hand2").pack(pady=0, anchor="w", padx=20)
        except Exception:
            tk.Label(self.settings_frame, text="No Speakers Found", fg=C_RED, bg=C_PANEL, font=("Courier", 8)).pack()

        # Close button
        tk.Button(self.settings_frame, text="✕ Close", command=lambda: self.settings_frame.place_forget(),
                  bg=C_PANEL, fg=C_MID, bd=0, font=("Courier", 9),
                  activebackground=C_DIM, cursor="hand2").pack(pady=(15, 5))

    def _set_lang(self, l):
        self.language = l
        self._save_settings()
        
    def _set_theme(self, th):
        self.theme = th
        self._apply_theme(th)
        self._save_settings()

    def _set_audio_device(self, idx):
        self.audio_input_index = idx
        self._save_settings()
        self.write_log(f"SYS: Audio input set to index {idx}.")
        # Refresh settings menu for highlight
        for widget in self.settings_frame.winfo_children():
            widget.destroy()
        self._build_settings_menu()

    def _set_audio_output_device(self, idx):
        self.audio_output_index = idx
        self._save_settings()
        self.write_log(f"SYS: Audio output set to index {idx}.")
        for widget in self.settings_frame.winfo_children():
            widget.destroy()
        self._build_settings_menu()

    # ─── Resize ───────────────────────────────────────────────
    def _on_resize(self, event):
        if event.widget == self.root:
            W, H = event.width, event.height
            if W == self.W and H == self.H: return
            self.W, self.H = W, H
            avail_h = H - TOP_BAR_H
            self.FACE_SZ = min(int(avail_h * 0.54), 400)
            self.FCX = self.side_width + (W - self.side_width) // 2
            self.FCY = TOP_BAR_H + int(avail_h * 0.13) + self.FACE_SZ // 2

            self.top_bar.place(x=0, y=0, relwidth=1.0, height=TOP_BAR_H)
            self.side_frame.place(height=avail_h)
            self._apply_mode()

    # ─── Logging ──────────────────────────────────────────────
    def enter_connect_wait_state(self):
        """Show banner while Gemini / provider link is not ready — أثناء انتظار الاتصال."""
        self._connect_wait = True
        try:
            self._link_var.set("SYNC • جاري الربط…")
            self._lbl_top_status.configure(fg=C_AMBER)
        except Exception:
            pass

    def exit_connect_wait_state(self):
        """Dismiss sync HUD after live session attaches — تم تأسيس القناة الصوتية."""
        self._connect_wait = False
        try:
            self._link_var.set("[ LINK ACTIVE · مفعّل ]")
            self._lbl_top_status.configure(fg=C_PRI)
        except Exception:
            pass

    def notify_link_recovery(self):
        """Back to standby between reconnect ticks — بين محاولات إعادة الاتصال."""
        self.enter_connect_wait_state()

    def prepare_log_display(self, text: str) -> str:
        """Rtl-wrap Arabic-heavy lines inside left-to-right Text widget."""
        lang_ar = getattr(self, "language", "") == "Arabic"
        if lang_ar or contains_arabic(text):
            return rtl_visual_embed(text)
        return text

    def write_log(self, text: str, tag: str | None = None):
        disp = self.prepare_log_display(text)
        self.typing_queue.append((disp, tag) if tag is not None else disp)
        tl = text.lower()
        self.status_text = ("PROCESSING" if tl.startswith("you:")
                            else "RESPONDING" if (tl.startswith("ai:") or tl.startswith("atlas:"))
                            else self.status_text)
        if not self.is_typing:
            self._start_typing()

    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            # Status text is managed by AtlasLive mic loop
            return
        self.is_typing = True
        raw = self.typing_queue.popleft()
        if isinstance(raw, tuple):
            text, tag = raw
        else:
            text = raw
            tag = None
        tl = text.lower()
        if tag is None:
            if tl.startswith("you:"):
                tag = "you"
            elif tl.startswith("ai:") or tl.startswith("atlas:"):
                tag = "ai"
            elif tl.startswith("boot:"):
                tag = "boot"
            else:
                tag = "sys"
        self.log_text.configure(state="normal")
        self._type_char(text, 0, tag)

    def _type_char(self, text, i, tag):
        if i < len(text):
            self.log_text.insert(tk.END, text[i], tag)
            self.log_text.see(tk.END)
            self.root.after(8, self._type_char, text, i + 1, tag)
        else:
            self.log_text.insert(tk.END, "\n")
            self.log_text.configure(state="disabled")
            self.root.after(25, self._start_typing)

    def start_speaking(self):
        self.speaking    = True
        self.status_text = "SPEAKING"

    def stop_speaking(self):
        self.speaking    = False
        # Status text is managed by AtlasLive mic loop

    def _on_chat_enter(self, event):
        txt = self.chat_entry.get().strip()
        if txt and self.chat_callback:
            self.write_log(f"You: {txt}")
            self.chat_callback(txt)
            self.chat_entry.delete(0, tk.END)

    def boot_sequence(self):
        """Displays a professional boot-up log."""
        steps = [
            ("BOOT", "Initializing ATLAS Core v2.5..."),
            ("BOOT", "Loading neural pathways..."),
            ("BOOT", "Verifying decryption keys..."),
            ("BOOT", "Connecting to Multimodal Live Hub..."),
            ("BOOT", "Peripheral sync: Microphone -> Active"),
            ("BOOT", "Peripheral sync: Visual Sensor -> Online"),
            ("AI",   "ATLAS ONLINE. SYSTEMS STABLE.")
        ]
        
        def run_step(idx):
            if idx < len(steps):
                tag_low, msg = steps[idx]
                tag = tag_low.lower()
                self.write_log(f"{tag_low}: {msg}")
                # Speed up for later steps
                delay = random.randint(400, 800) if idx < 3 else 300
                self.root.after(delay, lambda: run_step(idx + 1))
        
        self.root.after(500, lambda: run_step(0))

    # ─── API key setup ────────────────────────────────────────
    def _api_keys_exist(self):
        return _has_valid_api_key()

    def wait_for_api_key(self):
        """Block until API key is saved (called from main thread before starting ATLAS)."""
        while not self._api_key_ready:
            time.sleep(0.1)

    def _show_setup_ui(self):
        self.setup_frame = tk.Frame(
            self.root, bg=C_PANEL,
            highlightbackground=C_PRI, highlightthickness=1
        )
        self.setup_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.setup_frame, text="◈  INITIALISATION REQUIRED",
                 fg=C_PRI, bg=C_PANEL, font=("Courier", 13, "bold")).pack(pady=(18, 4))
        tk.Label(self.setup_frame,
                 text="enter api key to boot A.T.L.A.S.",
                 fg=C_MID, bg=C_PANEL, font=("Courier", 9)).pack(pady=(0, 10))

        tk.Label(self.setup_frame, text="GEMINI API KEY",
                 fg=C_DIM, bg=C_PANEL, font=("Courier", 9)).pack(pady=(8, 2))
        self.gemini_entry = tk.Entry(
            self.setup_frame, width=52, fg=C_TEXT, bg=C_BG,
            insertbackground=C_TEXT, borderwidth=1, highlightbackground=C_DIM, highlightthickness=1, font=("Courier", 10), show="*"
        )
        self.gemini_entry.pack(pady=(0, 4))

        tk.Button(
            self.setup_frame, text="▸  INITIALISE SYSTEMS",
            command=self._save_api_keys, bg=C_BG, fg=C_PRI,
            activebackground=C_DIM, font=("Courier", 10),
            borderwidth=0, pady=8
        ).pack(pady=14)

        self.setup_frame.lift()

    def _save_api_keys(self):
        gemini = self.gemini_entry.get().strip()
        if not gemini:
            return
        os.makedirs(CONFIG_DIR, exist_ok=True)
        config = _load_api_config()
        config["gemini_api_key"] = gemini
        with open(API_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        self.setup_frame.destroy()
        self._api_key_ready = True
        self.status_text = "PRESS [F7]"
        self.write_log("SYS: Systems initialised. ATLAS online.")
