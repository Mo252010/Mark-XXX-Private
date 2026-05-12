# actions/routines.py
# ATLAS XXV — Custom Workspace Routines

import time
from actions.open_app import open_app

def graphic_workspace(player=None):
    """
    Graphic Routine:
    1. Minimizes all windows (Win+D).
    2. Sequentially opens Comet, Photoshop, Spotify, and WhatsApp.
    """
    import pyautogui
    if player:
        player.write_log("[Routine] Initializing Graphic Workspace...")
    
    # Minimize all windows
    try:
        pyautogui.hotkey("win", "d")
        time.sleep(1.0)
    except Exception as e:
        print(f"[Routine] Error minimizing windows: {e}")

    # Apps to launch
    apps = ["Comet", "Photoshop", "Spotify", "WhatsApp"]
    
    results = []
    for app in apps:
        if player:
            player.write_log(f"[Routine] Launching {app}...")
        
        # We reuse open_app which handles the Win+S macro
        res = open_app(parameters={"app_name": app}, player=player)
        results.append(f"{app}: {res}")
        
        # Brief pause between launches to avoid overwhelming the search menu
        time.sleep(1.5)

    return "Graphic workspace routine completed, sir. " + " | ".join(results)
