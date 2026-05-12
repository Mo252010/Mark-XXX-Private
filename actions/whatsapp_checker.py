# actions/whatsapp_checker.py
import time
from actions.open_app import open_app
from actions.screen_processor import screen_process

def check_whatsapp_messages(player=None):
    """
    1. Opens WhatsApp.
    2. Waits for it to sync.
    3. Triggers the screen processor to check for unread messages from VIPs.
    """
    if player:
        player.write_log("SYS: Opening WhatsApp to check messages...")

    # Open WhatsApp
    open_app({"app_name": "WhatsApp"}, player=player)
    
    # Wait for the app to open and sync messages
    time.sleep(3.5)

    if player:
        player.write_log("SYS: Scanning screen for unread messages...")

    prompt = (
        "Look at the screen (I'm having you check my WhatsApp). "
        "Find any unread messages in the chat list (usually indicated by a green dot or bold text). "
        "I want to know if there are unread messages from ANY of these specific people: "
        "Daddy, maro, MIZO, Mamy, Koki Sis, moza. "
        "ALSO check if there is an unread message from any unknown number (a number instead of a name). "
        "Please speak directly to me in Arabic and give me a quick summary of who sent what."
    )

    screen_process({
        "angle": "screen",
        "user_text": prompt
    }, player=player)

    return "I am now checking the screen for WhatsApp messages. The vision assistant will speak the results."
