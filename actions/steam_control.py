import os
import json
import time
import requests
import webbrowser
from pathlib import Path

# Cache settings
CACHE_FILE = Path(__file__).parent.parent / "config" / "steam_apps.json"
CACHE_EXPIRY = 86400  # 24 hours

def _find_best_match(game_name):
    """Finds the best AppID for a given game name using Steam Store search."""
    try:
        query = game_name.lower().strip()
        search_url = f"https://store.steampowered.com/search/?term={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            
            # The search results are in elements with class 'search_result_row'
            results = soup.select(".search_result_row")
            if results:
                first_result = results[0]
                appid = first_result.get("data-ds-appid")
                name_elem = first_result.select_one(".title")
                name = name_elem.get_text() if name_elem else game_name
                
                if appid:
                    return appid, name
    except Exception as e:
        print(f"[Steam] Search error: {e}")
    return None, None

def steam_action(parameters: dict, player=None):
    """
    Main tool for Steam game management.
    actions: install, run, open_store
    """
    action = parameters.get("action", "install").lower()
    game_query = parameters.get("game_name", "")

    if not game_query:
        return "Please specify a game name."

    # Use the new search-based lookup
    appid, exact_name = _find_best_match(game_query)
    
    if not appid:
        return f"Could not find any Steam game matching '{game_query}'."

    url = ""
    if action == "install":
        url = f"steam://install/{appid}"
        msg = f"Opening Steam to install {exact_name}..."
    elif action == "run":
        url = f"steam://run/{appid}"
        msg = f"Launching {exact_name} via Steam..."
    elif action == "open_store":
        url = f"https://store.steampowered.com/app/{appid}"
        msg = f"Opening Steam Store page for {exact_name}..."
    else:
        return f"Unknown Steam action: {action}"

    try:
        webbrowser.open(url)
        return msg
    except Exception as e:
        return f"Failed to trigger Steam action: {e}"

if __name__ == "__main__":
    # Test
    print(steam_action({"action": "install", "game_name": "pubg"}))
