#!/usr/bin/env python3
"""Flask Web-UI für den Audible Downloader."""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Optional

import audible
import httpx
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from auth import AUTH_FILE, LOCALES, load_auth, login as do_login, logout as do_logout, CONFIG_DIR
from library import fetch_library
from downloader import get_activation_bytes
from converter import convert_file, split_to_chapters

# Logging in Datei und Konsole
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(CONFIG_DIR / "app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# audible-Library loggt bei jedem httpx-Request intern "load data from file" –
# das ist Rauschen, kein Fehler.
logging.getLogger("audible").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Für PyInstaller .app Bundle: Templates-Pfad korrekt auflösen
_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, template_folder=os.path.join(_HERE, "templates"))
app.secret_key = "audible-dl-local"

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Audible"
PLAY_CACHE_DIR = CONFIG_DIR / "play_cache"
PORT = 7842

# ── Task Registry ─────────────────────────────────────────────────────────────
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# ── License cache (asin → {url, voucher, fmt}) ────────────────────────────────
_license_cache: dict[str, dict] = {}
_license_cache_lock = threading.Lock()

# ── Stream-Datei-Cache (asin → (Pfad, decrypt_args)) ─────────────────────────
_stream_file_cache: dict[str, tuple[Path, list]] = {}

# ── Shared HTTP-Client für Proxy (Connection-Pooling zu CloudFront) ───────────
_proxy_http_client = httpx.Client(
    follow_redirects=True,
    timeout=None,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)
_stream_file_cache_lock = threading.Lock()


def _get_local_asins() -> set:
    """Schneller Scan des Download-Ordners: gibt Menge aller lokal verfügbaren ASINs zurück."""
    local: set = set()
    if not DEFAULT_DOWNLOAD_DIR.exists():
        return local
    try:
        for vf in DEFAULT_DOWNLOAD_DIR.glob("*.voucher"):
            try:
                data = json.loads(vf.read_text())
                asin = data.get("asin", "")
                if asin and any((vf.parent / f"{vf.stem}.{ext}").exists() for ext in ("aaxc", "aax")):
                    local.add(asin)
            except Exception:
                pass
        for sf in DEFAULT_DOWNLOAD_DIR.glob("*.asin"):
            try:
                asin = sf.read_text().strip()
                if asin:
                    local.add(asin)
            except Exception:
                pass
    except Exception:
        pass
    return local



# ── Library cache ─────────────────────────────────────────────────────────────
_library_cache: dict = {"books": None, "ts": 0}
_library_cache_lock = threading.Lock()


def _new_task(asin: str, title: str) -> str:
    tid = str(uuid.uuid4())[:8]
    with _tasks_lock:
        _tasks[tid] = {
            "id": tid, "asin": asin, "title": title,
            "status": "pending", "percent": 0,
            "message": "Warte in der Warteschlange...",
            "format": None, "file_path": None,
            "downloaded": 0, "total": 0,
        }
    return tid


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _fmt_bytes(b: int) -> str:
    if b >= 1_000_000_000:
        return f"{b / 1e9:.1f} GB"
    if b >= 1_000_000:
        return f"{b / 1e6:.0f} MB"
    return f"{b / 1e3:.0f} KB"


def _get_download_data(client, auth, asin: str):
    """Per-ASIN POST-Lizenzanfrage mit Adrm-Voucher-Entschlüsselung.

    Liefert (url, voucher_dict_or_None, fmt_str) oder (None, None, None).
    fmt ist "aaxc" wenn ein key/iv-Voucher vorhanden, sonst "aax".
    """
    from audible.aescipher import decrypt_voucher_from_licenserequest

    for quality in ("High", "Normal"):
        try:
            resp = client.post(
                f"1.0/content/{asin}/licenserequest",
                body={
                    "consumption_type": "Download",
                    "drm_type": "Adrm",
                    "quality": quality,
                    "supported_drm_types": ["Adrm"],
                    "response_groups": "content_reference",
                },
            )

            lic = resp.get("content_license", {})
            meta = lic.get("content_metadata", {})
            url = meta.get("content_url", {}).get("offline_url")
            encrypted_voucher = lic.get("license_response")

            logger.info(
                f"Lizenz ({quality}) für {asin}: url={'ja' if url else 'nein'}, "
                f"voucher={'ja' if encrypted_voucher else 'nein'}, "
                f"meta-keys={list(meta.keys())}"
            )

            if not url:
                continue

            if encrypted_voucher:
                try:
                    voucher = decrypt_voucher_from_licenserequest(auth, resp)
                    key = voucher.get("key")
                    iv = voucher.get("iv")
                    if key and iv:
                        logger.info(f"AAXC-Voucher entschlüsselt für {asin}")
                        return url, {"url": url, "key": key, "iv": iv}, "aaxc"
                except Exception as e:
                    logger.warning(f"Voucher-Entschlüsselung fehlgeschlagen: {e}")

            return url, None, "aax"

        except Exception as e:
            logger.warning(f"Lizenzanfrage ({quality}) fehlgeschlagen: {e}")

    return None, None, None


def _get_dash_stream_url(client, asin: str) -> Optional[str]:
    """Holt eine unverschlüsselte DASH-Streaming-URL (kein DRM nötig).
    Liefert die MPD-URL oder None bei Fehler/Nichtverfügbarkeit.
    """
    for quality in ("High", "Normal"):
        try:
            resp = client.post(
                f"1.0/content/{asin}/licenserequest",
                body={
                    "consumption_type": "Streaming",
                    "drm_type": "Dash",
                    "quality": quality,
                    "response_groups": ["content_reference"],
                    "supported_media_features": {
                        "codecs": ["mp4a.40.2", "mp4a.40.42"],
                        "drm_types": ["Dash"],
                    },
                },
            )
            lic = resp.get("content_license", {})
            logger.info(
                f"DASH-Response ({quality}) für {asin}: "
                f"lic-keys={list(lic.keys())}, "
                f"license_response-type={type(lic.get('license_response')).__name__}"
            )
            dash_url = lic.get("license_response", "")
            if isinstance(dash_url, str) and dash_url.startswith("http"):
                logger.info(f"DASH-URL erhalten für {asin} ({quality})")
                return dash_url
            # Alternativpfad: streaming_url oder offline_url in content_metadata
            meta = lic.get("content_metadata", {})
            content_url = meta.get("content_url", {})
            alt_url = content_url.get("streaming_url") or content_url.get("offline_url")
            if alt_url and isinstance(alt_url, str) and alt_url.startswith("http"):
                logger.info(f"DASH-URL (alt) erhalten für {asin} ({quality})")
                return alt_url
        except Exception as e:
            logger.warning(f"DASH-Streaming-Anfrage ({quality}) für {asin} fehlgeschlagen: {e}")
    return None


def _get_aaxc_bulk(client, asin: str):
    """Fallback: Bulk-AAXC-Endpoint (für Audible-Plus-Titel)."""
    try:
        resp = client.post(
            "1.0/content/licenserequest",
            body={
                "asins": [asin],
                "quality": "High",
                "consumption_type": "Download",
                "requested_codec": "AAXC",
                "supported_drm_types": ["Adrm"],
                "fulfill_drm": True,
            },
        )
        licenses = resp.get("content_licenses", [])
        logger.info(f"AAXC-Bulk für {asin}: {len(licenses)} Lizenzen")
        if not licenses:
            return None
        lic = licenses[0]
        meta = lic.get("content_metadata", {})
        url = meta.get("content_url", {}).get("offline_url")
        voucher = lic.get("voucher", {})
        logger.info(
            f"AAXC-Bulk url={'ja' if url else 'nein'}, "
            f"voucher-keys={list(voucher.keys()) if voucher else '–'}"
        )
        key = voucher.get("key") or meta.get("adrm_key")
        iv = voucher.get("iv") or meta.get("adrm_iv")
        if not url:
            return None
        return {"url": url, "key": key, "iv": iv}
    except Exception as e:
        logger.warning(f"AAXC-Bulk fehlgeschlagen: {e}")
        return None


# ── Download Thread ───────────────────────────────────────────────────────────

def _download_thread(
    task_id: str, asin: str, title: str,
    output_dir: Path, convert_fmt, prefer_aaxc: bool,
    multitrack: bool = False,
):
    task = _tasks[task_id]
    safe = _sanitize(title)
    output_dir.mkdir(parents=True, exist_ok=True)

    def upd(msg: str, pct: int = None, **kw):
        task["message"] = msg
        if pct is not None:
            task["percent"] = pct
        task.update(kw)

    try:
        logger.info(f"Starte Task {task_id} für {asin} ({title})")
        upd("Verbinde mit Audible...", 2)
        auth = load_auth()
        
        user_agent = "Audible/3.56.2 (iPhone; iOS 15.1; Scale/3.00)"

        url = None
        fmt = "aax"
        voucher = None

        upd("Rufe Lizenz ab...", 5)
        with audible.Client(auth=auth) as client:
            # 1. Per-ASIN POST mit Voucher-Entschlüsselung (primär, für AAX + AAXC)
            url, voucher, fmt = _get_download_data(client, auth, asin)

            # 2. Bulk-AAXC-Fallback (Audible-Plus-Titel)
            if not url:
                logger.info(f"Per-ASIN fehlgeschlagen, versuche AAXC-Bulk für {asin}")
                data = _get_aaxc_bulk(client, asin)
                if data and data.get("url"):
                    url, voucher, fmt = data["url"], data, "aaxc"

        if not url:
            logger.warning(f"Kein Download-Link für {asin} gefunden.")
            raise Exception(
                "Kein Download-Link erhalten. Möglicherweise ist der Titel "
                "in deiner Region nicht verfügbar oder das Gerät ist nicht autorisiert."
            )

        task["format"] = fmt.upper()
        dest = output_dir / f"{safe}.{fmt}"
        task["file_path"] = str(dest)
        # Sidecar für persistente ASIN-Erkennung (überlebt App-Neustart)
        (output_dir / f"{safe}.asin").write_text(asin)
        upd(f"Download startet...", 10)

        # Header für den eigentlichen Datei-Download
        try:
            headers = dict(auth.access_headers)
        except:
            headers = {"Authorization": f"Bearer {auth.access_token}"}
        
        headers["User-Agent"] = user_agent

        with httpx.Client(follow_redirects=True, timeout=None) as http:
            try:
                with http.stream("GET", url, headers=headers) as r:
                    logger.info(f"HTTP Status: {r.status_code} für {asin}")
                    if r.status_code >= 400:
                        raise Exception(f"Server-Fehler {r.status_code}")
                    
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0))
                    done = 0
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                pct = int(done / total * 50) + 10
                                upd(f"{_fmt_bytes(done)} / {_fmt_bytes(total)}", pct, downloaded=done, total=total)
            except Exception as e:
                logger.exception("Download fehlgeschlagen")
                raise Exception(f"Download-Fehler: {str(e)}")

        if fmt == "aaxc" and voucher:
            vpath = output_dir / f"{safe}.voucher"
            vpath.write_text(json.dumps({
                "asin": asin,
                "key": voucher.get("key", ""),
                "iv":  voucher.get("iv",  ""),
            }))

        if multitrack and convert_fmt:
            target_fmt = convert_fmt
            upd(f"Kapitel aufteilen → {target_fmt.upper()}… 0%", 60)
            ab = None
            if fmt == "aax":
                ab = get_activation_bytes(load_auth())

            if fmt == "aax":
                decrypt_args = ["-activation_bytes", ab]
            else:
                decrypt_args = [
                    "-audible_key", voucher.get("key", ""),
                    "-audible_iv",  voucher.get("iv",  ""),
                ] if voucher else []

            chapter_dir = output_dir / safe

            def _mt_progress(pct: int, msg: str):
                overall = 60 + int(pct * 39 / 100)
                upd(f"Kapitel aufteilen… {msg}", overall)

            out_files = split_to_chapters(
                dest, target_fmt, decrypt_args,
                output_dir=chapter_dir,
                progress_cb=_mt_progress,
            )
            dest.unlink(missing_ok=True)
            if fmt == "aaxc":
                (output_dir / f"{safe}.voucher").unlink(missing_ok=True)
            if out_files:
                task["file_path"] = str(chapter_dir)
                task["format"] = target_fmt.upper()
            else:
                raise Exception("Kapitel-Aufteilung fehlgeschlagen – keine Ausgabedateien erstellt.")

        elif convert_fmt:
            upd(f"Konvertiere zu {convert_fmt.upper()}… 0%", 60)
            ab = None
            if fmt == "aax":
                ab = get_activation_bytes(load_auth())

            def _conv_progress(pct: int, timecode: str):
                overall = 60 + int(pct * 39 / 100)
                upd(f"Konvertiere zu {convert_fmt.upper()}… {pct}%", overall)

            converted = convert_file(
                dest, convert_fmt,
                activation_bytes=ab,
                key=voucher.get("key") if voucher else None,
                iv=voucher.get("iv")  if voucher else None,
                output_dir=output_dir,
                progress_cb=_conv_progress,
            )
            if converted:
                dest.unlink(missing_ok=True)
                if fmt == "aaxc":
                    (output_dir / f"{safe}.voucher").unlink(missing_ok=True)
                task["file_path"] = str(converted)
                task["format"] = convert_fmt.upper()

        upd("Fertig!", 100)
        task["status"] = "done"
        logger.info(f"Task {task_id} erfolgreich beendet.")

    except Exception as e:
        logger.exception(f"Task {task_id} fehlgeschlagen: {str(e)}")
        task["status"] = "error"
        task["message"] = str(e)


# ── Download-All Worker ───────────────────────────────────────────────────────

def _download_all_thread(asin_list: list, output_dir: Path, convert_fmt, prefer_aaxc: bool):
    for asin, title in asin_list:
        safe = _sanitize(title)
        existing = list(output_dir.glob(f"{safe}.*"))
        already = [f for f in existing if f.suffix in (".aax", ".aaxc", ".m4b", ".mp3")]
        if already:
            continue
        tid = _new_task(asin, title)
        _download_thread(tid, asin, title, output_dir, convert_fmt, prefer_aaxc)
        time.sleep(1)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    logged_in = AUTH_FILE.exists()
    locale = None
    if logged_in:
        try:
            auth = load_auth()
            locale = str(auth.locale)
        except Exception:
            logged_in = False
    return jsonify({"logged_in": logged_in, "locale": locale, "locales": LOCALES})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    locale = data.get("locale", "de")
    try:
        import shared
        do_login(locale=locale, login_url_callback=shared.login_url_callback)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    do_logout()
    return jsonify({"success": True})


@app.route("/api/library")
def api_library():
    with _library_cache_lock:
        if _library_cache["books"] and time.time() - _library_cache["ts"] < 300:
            return jsonify({"books": _library_cache["books"]})
    try:
        auth = load_auth()
        items = fetch_library(auth)
        local_asins = _get_local_asins()
        books = []
        for item in items:
            authors = item.get("authors") or []
            author = authors[0].get("name", "") if authors else ""
            narrators = item.get("narrators") or []
            narrator = narrators[0].get("name", "") if narrators else ""
            images = item.get("product_images") or {}
            cover = images.get("500") or images.get("336") or images.get("252") or ""
            codecs = item.get("available_codecs") or []
            fmt = "AAXC" if any("AAXC" in (c.get("name", "")).upper() for c in codecs) else "AAX"
            series_title = ""
            series_sequence = ""
            for rel in (item.get("relationships") or []):
                rtype = rel.get("relationship_to_product", "") or rel.get("relationship_type", "")
                if "series" in rtype.lower():
                    series_title = rel.get("title", "")
                    series_sequence = str(rel.get("sort_value", ""))
                    break
            asin = item.get("asin", "")
            books.append({
                "asin": asin,
                "title": item.get("title", "Unbekannt"),
                "author": author,
                "narrator": narrator,
                "duration_min": item.get("runtime_length_min"),
                "cover": cover,
                "format": fmt,
                "description": item.get("publisher_summary", ""),
                "series_title": series_title,
                "series_sequence": series_sequence,
                "local": asin in local_asins,
            })
        with _library_cache_lock:
            _library_cache["books"] = books
            _library_cache["ts"] = time.time()
        return jsonify({"books": books})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True)
    asin = data.get("asin", "")
    title = data.get("title", asin)
    convert_fmt = data.get("format") or None
    prefer_aaxc = data.get("aaxc", False)
    multitrack  = bool(data.get("multitrack", False))

    out_dir_raw = data.get("output_dir") or str(DEFAULT_DOWNLOAD_DIR)
    output_dir = Path(out_dir_raw).expanduser()

    tid = _new_task(asin, title)
    threading.Thread(
        target=_download_thread,
        args=(tid, asin, title, output_dir, convert_fmt, prefer_aaxc),
        kwargs={"multitrack": multitrack},
        daemon=True,
    ).start()
    return jsonify({"task_id": tid, "task": _tasks[tid]})


@app.route("/api/download-all", methods=["POST"])
def api_download_all():
    data = request.get_json(force=True)
    convert_fmt = data.get("format") or None
    prefer_aaxc = data.get("aaxc", False)
    
    out_dir_raw = data.get("output_dir") or str(DEFAULT_DOWNLOAD_DIR)
    output_dir = Path(out_dir_raw).expanduser()

    try:
        auth = load_auth()
        items = fetch_library(auth)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    asin_list = [(i.get("asin", ""), i.get("title", "")) for i in items]
    threading.Thread(
        target=_download_all_thread,
        args=(asin_list, output_dir, convert_fmt, prefer_aaxc),
        daemon=True,
    ).start()
    return jsonify({"queued": len(asin_list)})


@app.route("/api/tasks")
def api_tasks():
    return jsonify(list(_tasks.values()))


@app.route("/api/events/<task_id>")
def api_events(task_id: str):
    def generate():
        while True:
            task = _tasks.get(task_id)
            if not task:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                return
            yield f"data: {json.dumps(task)}\n\n"
            if task["status"] in ("done", "error"):
                return
            time.sleep(0.4)

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _find_local_audio(asin: str) -> tuple[Optional[Path], list]:
    """Sucht eine bereits heruntergeladene Datei für diesen ASIN.
    Gibt (Pfad, decrypt_args) zurück oder (None, []).
    Funktioniert auch nach App-Neustart dank .asin-Sidecar-Dateien.
    """
    def _first_audio(d: Path) -> Optional[Path]:
        files = sorted(f for f in d.iterdir()
                       if f.suffix.lower() in ('.mp3', '.m4b', '.m4a', '.aac'))
        return files[0] if files else None

    # 1. Voucher-Dateien → noch nicht konvertierte AAXC/AAX
    for vf in DEFAULT_DOWNLOAD_DIR.glob("*.voucher"):
        try:
            data = json.loads(vf.read_text())
            if data.get("asin") != asin:
                continue
            for ext in ("aaxc", "aax"):
                audio = vf.parent / f"{vf.stem}.{ext}"
                if audio.exists():
                    if ext == "aaxc":
                        k, iv = data.get("key", ""), data.get("iv", "")
                        if k and iv:
                            return audio, ["-audible_key", k, "-audible_iv", iv]
                    else:
                        try:
                            ab = get_activation_bytes(load_auth())
                            return audio, ["-activation_bytes", ab]
                        except Exception:
                            pass
        except Exception:
            pass

    # 2. .asin-Sidecar → konvertierte Einzel-Datei oder Kapitel-Verzeichnis
    for sf in DEFAULT_DOWNLOAD_DIR.glob("*.asin"):
        try:
            if sf.read_text().strip() != asin:
                continue
            stem = sf.stem
            for ext in ("m4b", "mp3", "m4a", "aac"):
                p = sf.parent / f"{stem}.{ext}"
                if p.exists():
                    return p, []
            chapter_dir = sf.parent / stem
            if chapter_dir.is_dir():
                first = _first_audio(chapter_dir)
                if first:
                    return first, []
        except Exception:
            pass

    # 3. In-Memory-Tasks (gleiche App-Sitzung, kein Sidecar nötig)
    with _tasks_lock:
        for task in _tasks.values():
            if task.get("asin") == asin and task.get("status") == "done":
                fp = task.get("file_path")
                if not fp:
                    continue
                p = Path(fp)
                if p.is_file():
                    return p, []
                if p.is_dir():
                    first = _first_audio(p)
                    if first:
                        return first, []

    return None, []


@app.route("/api/stream/prepare/<asin>")
def api_stream_prepare(asin: str):
    """Bereitet Stream vor: Lizenz holen (≈1–2 s), dann sofort bereit.
    Kein Vorherunterladen – Streaming läuft online über /api/proxy/<asin>.
    """
    # Lokal heruntergeladen → sofort bereit
    src_path, _ = _find_local_audio(asin)
    if src_path is not None:
        return jsonify({"status": "ready"})

    # Temp-Cache → sofort bereit
    with _stream_file_cache_lock:
        cached = _stream_file_cache.get(asin)
    if cached and cached[0].exists():
        return jsonify({"status": "ready"})

    # Lizenz bereits gecacht → sofort bereit für Online-Stream
    with _license_cache_lock:
        if _license_cache.get(asin):
            return jsonify({"status": "ready"})

    # Lizenz holen (einmalig, ≈1–2 s)
    try:
        auth = load_auth()
        with audible.Client(auth=auth) as client:
            url, voucher, fmt = _get_download_data(client, auth, asin)
        if not url:
            return jsonify({"error": "Kein Download-Link erhalten"}), 404
        with _license_cache_lock:
            _license_cache[asin] = {"url": url, "voucher": voucher, "fmt": fmt}
        logger.info(f"Stream Prepare {asin}: Lizenz gecacht (online)")
        return jsonify({"status": "ready"})
    except Exception as e:
        logger.exception(f"Stream Prepare {asin}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/proxy/<asin>")
def api_proxy(asin: str):
    """Range-fähiger HTTP-Proxy für ffmpeg – leitet Anfragen ans Audible-CDN
    weiter inkl. Auth-Headern. Löst SSL-Probleme im gebündelten App-Bundle.
    """
    with _license_cache_lock:
        lic = _license_cache.get(asin)
    if not lic:
        return jsonify({"error": "Lizenz nicht gecacht – bitte /prepare aufrufen"}), 404

    url = lic["url"]
    with _license_cache_lock:
        cached_headers = _license_cache.get(f"__headers__")
    if not cached_headers:
        try:
            cached_headers = dict(load_auth().access_headers)
            cached_headers["User-Agent"] = "Audible/3.56.2 (iPhone; iOS 15.1; Scale/3.00)"
            with _license_cache_lock:
                _license_cache["__headers__"] = cached_headers
        except Exception:
            cached_headers = {"User-Agent": "Audible/3.56.2 (iPhone; iOS 15.1; Scale/3.00)"}

    fwd_headers = dict(cached_headers)
    if "Range" in request.headers:
        fwd_headers["Range"] = request.headers["Range"]

    try:
        req = _proxy_http_client.build_request("GET", url, headers=fwd_headers)
        r = _proxy_http_client.send(req, stream=True)

        resp_headers = {
            "Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        }
        for h in ("Content-Length", "Content-Range"):
            if h in r.headers:
                resp_headers[h] = r.headers[h]

        def generate():
            try:
                for chunk in r.iter_bytes(65536):
                    yield chunk
            finally:
                r.close()

        return Response(
            stream_with_context(generate()),
            status=r.status_code,
            headers=resp_headers,
        )
    except Exception as e:
        logger.exception(f"Proxy {asin}")
        return jsonify({"error": str(e)}), 502


@app.route("/api/play/<asin>")
def api_play(asin: str):
    """Entschlüsselt lokale AAXC/AAX-Datei → Cache → sendet mit HTTP-Range-Support.
    Der Browser kann damit nativ suchen, ohne Chunk-Logik.
    """
    import flask
    from converter import _find_tool

    PLAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PLAY_CACHE_DIR / f"{asin}.m4a"

    if cache_file.exists() and cache_file.stat().st_size > 0:
        logger.info(f"api_play {asin}: Cache-Hit ({cache_file.stat().st_size // 1024} KB)")
        return flask.send_file(cache_file, mimetype="audio/mp4", conditional=True)

    src_path, decrypt_args = _find_local_audio(asin)
    if src_path is None:
        with _stream_file_cache_lock:
            cached = _stream_file_cache.get(asin)
        if cached:
            cp, ca = cached
            if cp.exists():
                src_path, decrypt_args = cp, ca

    if src_path is None:
        return jsonify({"error": "Nicht lokal vorhanden"}), 404

    # Bereits konvertierte Datei (kein DRM) → direkt servieren
    if not decrypt_args and src_path.suffix.lower() in (".m4a", ".m4b", ".mp3", ".aac"):
        return flask.send_file(src_path, conditional=True)

    try:
        ffmpeg = _find_tool("ffmpeg")
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    tmp = PLAY_CACHE_DIR / f"{asin}.tmp.m4a"
    cmd = [ffmpeg, "-y", "-loglevel", "warning",
           "-probesize", "100k", "-analyzeduration", "100000"]
    cmd += decrypt_args + ["-i", str(src_path)]
    cmd += ["-vn", "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)]

    logger.info(f"api_play {asin}: Entschlüssele {src_path.name} → {cache_file.name}")
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, timeout=600)

    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        err = proc.stderr.decode(errors="replace")[:500]
        logger.error(f"api_play {asin}: ffmpeg Fehler rc={proc.returncode}: {err}")
        return jsonify({"error": "Entschlüsselung fehlgeschlagen"}), 500

    tmp.rename(cache_file)
    logger.info(f"api_play {asin}: Cache bereit ({cache_file.stat().st_size // 1024} KB)")
    return flask.send_file(cache_file, mimetype="audio/mp4", conditional=True)


@app.route("/api/stream/<asin>")
def api_stream(asin: str):
    """Streamt ein Hörbuch via ffmpeg.
    Lokal heruntergeladen: direkt von Disk.
    Nicht lokal: Lizenz live holen, dann online via lokalem Proxy.
    """
    from converter import _find_tool

    start    = float(request.args.get("start", 0))
    duration = request.args.get("duration")  # Sekunden; None = unbegrenzt

    try:
        ffmpeg = _find_tool("ffmpeg")
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    # 1. Lokale Datei (sofort, kein Netz nötig)
    src_path, decrypt_args = _find_local_audio(asin)
    if src_path is None:
        with _stream_file_cache_lock:
            cached = _stream_file_cache.get(asin)
        if cached:
            cp, ca = cached
            if cp.exists():
                src_path, decrypt_args = cp, ca

    _DUR_ARGS = ["-t", str(int(float(duration)))] if duration else []
    # Seekable MP4: c:a copy (kein Re-Encoding), faststart (moov vorne)
    _ENC_ARGS = _DUR_ARGS + ["-vn", "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4"]

    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".m4a")
    os.close(tmp_fd)

    if src_path is not None:
        cmd = [ffmpeg, "-loglevel", "warning",
               "-probesize", "100k", "-analyzeduration", "100000"]
        if start > 0:
            cmd += ["-ss", str(int(start))]
        cmd += decrypt_args + ["-i", str(src_path)] + _ENC_ARGS + [tmp_path_str]
        logger.info(f"Stream {asin} ab {start:.0f}s ← lokal: {src_path.name}")

    else:
        # 2. Primär: unverschlüsselter DASH-Stream (kein DRM, kein ffmpeg-Decrypt)
        dash_url = None
        auth = None
        try:
            auth = load_auth()
            with audible.Client(auth=auth) as client:
                dash_url = _get_dash_stream_url(client, asin)
        except Exception as e:
            logger.warning(f"DASH-Anfrage für {asin} fehlgeschlagen: {e}")

        if dash_url:
            cmd = [ffmpeg, "-loglevel", "warning",
                   "-probesize", "100k", "-analyzeduration", "100000"]
            if start > 0:
                cmd += ["-ss", str(int(start))]
            cmd += ["-i", dash_url] + _ENC_ARGS + [tmp_path_str]
            logger.info(f"Stream {asin} ab {start:.0f}s ← DASH (unverschlüsselt)")

        else:
            # 3. Fallback: Download-Lizenz + lokaler Proxy (verschlüsselt)
            logger.info(f"Stream {asin}: DASH nicht verfügbar, Fallback auf Proxy")
            with _license_cache_lock:
                lic = _license_cache.get(asin)

            if not lic:
                try:
                    if auth is None:
                        auth = load_auth()
                    with audible.Client(auth=auth) as client:
                        url, voucher, fmt = _get_download_data(client, auth, asin)
                    if not url:
                        with audible.Client(auth=auth) as client:
                            data = _get_aaxc_bulk(client, asin)
                        if data and data.get("url"):
                            url, voucher, fmt = data["url"], data, "aaxc"
                    if not url:
                        return jsonify({"error": "Kein Download-Link erhalten"}), 404
                    lic = {"url": url, "voucher": voucher, "fmt": fmt}
                    with _license_cache_lock:
                        _license_cache[asin] = lic
                    logger.info(f"Stream {asin}: Fallback-Lizenz live geholt")
                except Exception as e:
                    logger.exception(f"Stream {asin}: Lizenzfehler")
                    return jsonify({"error": str(e)}), 500

            fmt     = lic.get("fmt", "aaxc")
            voucher = lic.get("voucher")

            if fmt == "aaxc" and voucher:
                decrypt_args = ["-audible_key", voucher["key"], "-audible_iv", voucher["iv"]]
            else:
                try:
                    if auth is None:
                        auth = load_auth()
                    ab = get_activation_bytes(auth)
                    decrypt_args = ["-activation_bytes", ab]
                except Exception as e:
                    return jsonify({"error": f"Activation bytes: {e}"}), 500

            # Proxy: httpx holt vom CDN (funktioniert mit WAF), ffmpeg greift lokal zu
            proxy_url = f"http://127.0.0.1:{PORT}/api/proxy/{asin}"
            cmd = [ffmpeg, "-loglevel", "warning",
                   "-probesize", "100k", "-analyzeduration", "100000"]
            if start > 0:
                cmd += ["-ss", str(int(start))]
            cmd += decrypt_args + ["-i", proxy_url] + _ENC_ARGS + [tmp_path_str]
            logger.info(f"Stream {asin} ab {start:.0f}s ← Proxy+decrypt")

    try:
        proc = subprocess.run(cmd, stderr=subprocess.PIPE, timeout=180)
        stderr_out = proc.stderr or b""
        rc = proc.returncode
        if stderr_out:
            logger.warning(f"ffmpeg stderr ({asin}, rc={rc}): {stderr_out.decode(errors='replace')[:800]}")
        if b"403" in stderr_out or b"Forbidden" in stderr_out:
            with _license_cache_lock:
                _license_cache.pop(asin, None)
            logger.info(f"Stream {asin}: URL abgelaufen (403) – Lizenz-Cache geleert")
        tmp = Path(tmp_path_str)
        if rc not in (0, None) or not tmp.exists() or tmp.stat().st_size == 0:
            return jsonify({"error": f"ffmpeg Fehler (rc={rc})"}), 500
        data = tmp.read_bytes()
        return Response(
            data,
            content_type="audio/mp4",
            headers={"Content-Length": str(len(data)), "Cache-Control": "no-cache"},
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Stream {asin}: ffmpeg Timeout")
        return jsonify({"error": "Timeout bei ffmpeg"}), 504
    except Exception as e:
        logger.exception(f"Stream {asin}: unerwarteter Fehler")
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            Path(tmp_path_str).unlink(missing_ok=True)
        except Exception:
            pass


@app.route("/api/js-log", methods=["POST"])
def api_js_log():
    """Empfängt JavaScript-Debug-Meldungen und schreibt sie ins App-Log."""
    msg = request.get_data(as_text=True)[:500]
    logger.info(f"[JS] {msg}")
    return "", 204


@app.route("/api/stream/test")
def api_stream_test():
    """Liefert ein synthetisches fMP4-Testsignal (440Hz Sinus, 5 Sek.)."""
    from converter import _find_tool
    try:
        ffmpeg = _find_tool("ffmpeg")
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-vn", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]

    def generate():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for chunk in iter(lambda: proc.stdout.read(8192), b""):
                yield chunk
        finally:
            proc.terminate()
            proc.wait()

    logger.info("Stream-Test (440Hz Sinus, 5s)")
    return Response(
        stream_with_context(generate()),
        content_type="audio/mp4",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    print(f"\n🎧  Audible Downloader  →  {url}")
    print("    CTRL+C zum Beenden\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)
