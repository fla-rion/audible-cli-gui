#!/usr/bin/env python3
"""
Startet den Audible Downloader als natives macOS-Fenster.
Flask läuft unsichtbar im Hintergrund; die UI erscheint in einem
WKWebView-Fenster (Apples nativer WebKit-Engine).

Login-Flow:
  1. User klickt "Mit Amazon anmelden"
  2. Ein kleines Popup-Fenster öffnet die Amazon-OAuth-Seite
     (Hauptfenster + laufender Fetch bleiben unberührt)
  3. User loggt sich normal bei Amazon ein (2FA, CAPTCHA etc.)
  4. Amazon leitet auf maplanding-URL um → Popup schließt sich automatisch
  5. Fetch in der App-UI resolvet, Bibliothek lädt
"""

import logging
import os
import sys
import threading
import time

# ── Pfad-Setup für PyInstaller .app Bundle ────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── Imports ───────────────────────────────────────────────────────────────────
import webview
import httpx

PORT = 7842
APP_URL = f"http://127.0.0.1:{PORT}"

logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ── pywebview JS-API (wird dem Hauptfenster als window.pywebview.api verfügbar)
_main_window: webview.Window = None


class AudibleAPI:
    """Python-Methoden, die direkt aus dem Webfrontend per JS aufrufbar sind."""

    def pick_folder(self):
        """Öffnet einen nativen macOS-Ordnerwähler, gibt den gewählten Pfad zurück."""
        if _main_window is None:
            return None
        result = _main_window.create_file_dialog(
            webview.FOLDER_DIALOG,
            allow_multiple=False,
        )
        return result[0] if result else None


# ── Login-Koordination (Thread-sicher) ───────────────────────────────────────

def _pywebview_login_callback(amazon_url: str) -> str:
    """
    Wird von der audible-Bibliothek mit der Amazon-OAuth-URL aufgerufen.
    Öffnet ein Popup-Fenster für den Login. Das Hauptfenster (und der
    laufende Fetch) bleiben dabei unangetastet.
    """
    result: dict = {"url": None}
    done = threading.Event()

    def on_loaded():
        """Prüft nach jeder Seitenladung ob die maplanding-URL erreicht wurde."""
        try:
            url = popup.get_current_url() or ""
        except Exception:
            return
        if "maplanding" in url and "openid.oa2.authorization_code" in url:
            result["url"] = url
            done.set()
            try:
                popup.destroy()
            except Exception:
                pass

    def on_closed():
        """Wird ausgelöst wenn der User das Popup manuell schließt."""
        done.set()

    popup = webview.create_window(
        title="Bei Amazon anmelden – Audible Downloader",
        url=amazon_url,
        width=460,
        height=920,
        on_top=True,
        background_color="#ffffff",
    )
    popup.events.loaded += on_loaded
    popup.events.closed += on_closed

    # Wartet max. 5 Minuten auf den Redirect
    done.wait(timeout=300)

    return result.get("url") or ""


# ── Lade-Bildschirm ───────────────────────────────────────────────────────────
SPLASH = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #09090b;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    -webkit-user-select: none;
  }
  .icon {
    width: 80px; height: 80px;
    background: #f59e0b;
    border-radius: 20px;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 22px;
    font-size: 42px;
    box-shadow: 0 0 48px #f59e0b44;
  }
  h1 { color: #fafafa; font-size: 22px; font-weight: 600; letter-spacing: -.3px; }
  .sub { color: #52525b; font-size: 13px; margin-top: 6px; }
  .dots { margin-top: 30px; }
  .dot {
    display: inline-block; width: 8px; height: 8px;
    background: #f59e0b; border-radius: 50%; margin: 0 4px;
    animation: p 1.3s ease infinite;
  }
  .dot:nth-child(2) { animation-delay: .22s; }
  .dot:nth-child(3) { animation-delay: .44s; }
  @keyframes p {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:.2; transform:scale(.6); }
  }
</style>
</head>
<body>
<div style="text-align:center">
  <div class="icon">🎧</div>
  <h1>Audible Downloader</h1>
  <p class="sub">Wird gestartet…</p>
  <div class="dots">
    <span class="dot"></span>
    <span class="dot"></span>
    <span class="dot"></span>
  </div>
</div>
</body>
</html>"""

ERROR_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body { background:#09090b; color:#ef4444; font-family:-apple-system,sans-serif;
         display:flex; align-items:center; justify-content:center;
         height:100vh; margin:0; text-align:center; }
  p  { color:#71717a; font-size:13px; margin-top:8px; }
</style></head>
<body>
<div>
  <div style="font-size:18px;font-weight:600">Server konnte nicht starten</div>
  <p>Port 7842 ist möglicherweise bereits belegt.<br>Bitte App neu starten.</p>
</div>
</body></html>"""


# ── Flask ─────────────────────────────────────────────────────────────────────

def _run_flask():
    from app import app as flask_app
    flask_app.run(
        host="127.0.0.1",
        port=PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


def _wait_for_flask(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(APP_URL, timeout=1.0)
            return True
        except Exception:
            time.sleep(0.08)
    return False


def _load_when_ready(window):
    if _wait_for_flask():
        window.load_url(APP_URL)
    else:
        window.load_html(ERROR_HTML)


# ── Einstiegspunkt ────────────────────────────────────────────────────────────

def main():
    global _main_window

    # Pywebview-Login-Callback in shared-Modul registrieren
    import shared
    shared.login_url_callback = _pywebview_login_callback

    # Flask im Hintergrund starten
    threading.Thread(target=_run_flask, daemon=True).start()

    # Natives macOS-Fenster anlegen
    _main_window = webview.create_window(
        title="Audible Downloader",
        html=SPLASH,
        width=1300,
        height=840,
        min_size=(900, 620),
        background_color="#09090b",
        text_select=True,
        js_api=AudibleAPI(),
    )

    # Linux-Bundles bringen PySide6/QtWebEngine mit (self-contained), statt sich
    # auf System-GTK/WebKit2GTK zu verlassen, das PyInstaller nicht bündeln kann.
    gui = "qt" if sys.platform.startswith("linux") else None
    webview.start(_load_when_ready, _main_window, private_mode=False, gui=gui)


if __name__ == "__main__":
    main()
