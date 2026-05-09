"""
Geteilter Zustand zwischen run_app.py (pywebview) und app.py (Flask).

run_app.py setzt `login_url_callback` bevor Flask startet.
app.py liest die Callback-Referenz beim Login-Request.
"""

import threading
from typing import Callable, Optional

# Wird von run_app.py gesetzt, wenn die App als macOS-Fenster läuft.
# None bedeutet: kein Fenster vorhanden → manuelle URL-Eingabe (CLI-Modus).
login_url_callback: Optional[Callable[[str], str]] = None
