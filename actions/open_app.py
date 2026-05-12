# actions/open_app.py
# ATLAS — Reliable app launcher via Windows Search
# ─── يعتمد اللصق بالحافظة لكل الأسماء (عربي/إنجليزي) ───

import time


def open_app(parameters=None, response=None, player=None, session_memory=None) -> str:
    import pyautogui
    from utils import paste_unicode_text
    # Reliability overhead
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    
    app_name = (parameters or {}).get("app_name", "").strip()
    if not app_name:
        return "Please specify which application to open, sir."

    if player:
        player.write_log(f"SYS: Initializing clean launch for {app_name}...")

    try:
        # Step 1: Force clear any existing menus
        pyautogui.press("esc")
        time.sleep(0.2)
        pyautogui.press("esc")
        time.sleep(0.3)
        
        # Step 2: Open Windows Search (user requested method)
        pyautogui.keyDown('win')
        pyautogui.press('s')
        pyautogui.keyUp('win')
        
        # Step 3: Wait for Search UI (User limit: 4s. We use 2.5s for safety/speed balance)
        time.sleep(2.5)
        
        # Step 4: Wake up search bar (prevents focus loss)
        pyautogui.press("backspace")
        time.sleep(0.2)
        
        # Step 5: Arabic-safe entry — pyautogui.write() cannot compose Arabic / لا يكتب العربي صحيحاً
        if not paste_unicode_text(app_name, pre_delay_s=0.2, post_delay_s=0.35):
            return "Paste stack unavailable — install pyperclip + keyboard drivers for search entry."
        time.sleep(0.8)
        
        # Step 6: Launch
        pyautogui.press("enter")
        
        return f"Opening {app_name}, sir. (Heavy launch mode)"
        
    except Exception as e:
        error_msg = f"Failed to open {app_name}: {e}"
        print(f"[open_app] {error_msg}")
        return error_msg


if __name__ == "__main__":
    raise SystemExit("open_app runs through ATLAS; import from actions.open_app.")