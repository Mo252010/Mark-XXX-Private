import ctypes
import ctypes.wintypes
import time

def enum_windows():
    results = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value

                cls_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, cls_buf, 256)
                cls_name = cls_buf.value

                results.append(f"TITLE: '{title}' | CLASS: '{cls_name}'")
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return results

print("Scanning for Windows... (Look for WhatsApp or incoming calls)")
for w in enum_windows():
    if "whatsapp" in w.lower() or "مكالمة" in w.lower() or "call" in w.lower():
        print(">> MATCH:", w)
