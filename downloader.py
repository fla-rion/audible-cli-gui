import json
import re
from pathlib import Path
from typing import Optional

import audible
import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()

QUALITY_MAP = {"high": "High", "best": "Extreme", "normal": "Normal"}


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _get_aax_url(client: audible.Client, asin: str) -> Optional[str]:
    """Holt die Download-URL für standard AAX-Inhalte (gekaufte Bücher).

    Der Endpunkt ist POST 1.0/content/{asin}/licenserequest (pro-ASIN), NICHT
    GET mit requested_codec/quality=Extreme (403) und NICHT der Batch-POST auf
    1.0/content/licenserequest (UnknownOperationException).

    WICHTIG: supported_drm_types darf NUR "Adrm" enthalten. Mit "Mpeg" mit in
    der Liste liefert Audible ein AAXC-Container-File aus (major_brand=aaxc),
    das trotz drm_type="Adrm" in der Antwort NICHT mit -activation_bytes
    entschlüsselbar ist (ffmpeg dekodiert dann nur Rauschen). Mit NUR "Adrm"
    kommt echtes Legacy-AAX zurück, das -activation_bytes korrekt entschlüsselt
    (verifiziert gegen die Referenzimplementierung von audible-cli/mkb79).
    """
    try:
        resp = client.post(
            f"1.0/content/{asin}/licenserequest",
            body={
                "supported_drm_types": ["Adrm"],
                "quality": "High",
                "consumption_type": "Download",
            },
        )
        meta = resp.get("content_license", {}).get("content_metadata", {})
        return meta.get("content_url", {}).get("offline_url")
    except Exception:
        return None


def _get_aaxc_data(client: audible.Client, asin: str) -> Optional[dict]:
    """Holt URL + Voucher für AAXC-Inhalte (heute der Normalfall, auch für
    gekaufte Bücher, nicht nur Audible-Plus/Abo-Inhalte).

    Der Request fragt beide DRM-Typen an (wie die offizielle audible-cli es
    tut). Die Antwort enthält KEIN direktes "voucher"-Feld mit Klartext-
    key/iv — stattdessen muss das verschlüsselte "license_response"-Feld via
    RSA mit dem Geräte-Private-Key entschlüsselt werden
    (audible.aescipher.decrypt_voucher_from_licenserequest). Das Ergebnis
    enthält dann den echten AES-Key+IV für ffmpegs -audible_key/-audible_iv.
    """
    try:
        resp = client.post(
            f"1.0/content/{asin}/licenserequest",
            body={
                "supported_drm_types": ["Mpeg", "Adrm"],
                "quality": "High",
                "consumption_type": "Download",
            },
        )
        meta = resp.get("content_license", {}).get("content_metadata", {})
        url = meta.get("content_url", {}).get("offline_url")
        if not url:
            return None

        from audible.aescipher import decrypt_voucher_from_licenserequest

        voucher = decrypt_voucher_from_licenserequest(client.auth, resp)
        key = voucher.get("key")
        iv = voucher.get("iv")
        if not (key and iv):
            return None
        return {"url": url, "key": key, "iv": iv}
    except Exception:
        return None


def _stream_download(url: str, dest: Path, title: str, auth_headers: dict):
    # User-Agent hinzufügen
    headers = dict(auth_headers)
    if "User-Agent" not in headers:
        headers["User-Agent"] = "Audible/1.46.1 (iPhone; iOS 15.1; Scale/3.00)"

    with httpx.Client(follow_redirects=True, timeout=None) as http:
        with http.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with Progress(
                TextColumn(f"[bold cyan]{title[:40]}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as prog:
                task = prog.add_task("download", total=total or None)
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
                        prog.advance(task, len(chunk))


def download_book(
    auth: audible.Authenticator,
    asin: str,
    title: str,
    output_dir: Path,
    prefer_aaxc: bool = False,
) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _sanitize_filename(title)

    with audible.Client(auth=auth) as client:
        # AAXC (mit RSA-entschlüsseltem Voucher) ist heute der zuverlässige
        # Standardpfad - Audible liefert fuer die meisten Konten/Titel
        # inzwischen auch bei "Adrm"-Anfragen ein AAXC-Container-File aus,
        # das sich NICHT mit -activation_bytes entschlüsseln laesst. Echtes
        # Legacy-AAX (aax-Demuxer, activation_bytes) ist nur noch der
        # Fallback für ältere Konten/Inhalte, bei denen AAXC fehlschlägt.
        data = _get_aaxc_data(client, asin)
        if data:
            return _save_aaxc(auth, asin, safe_title, output_dir, data)
        url = _get_aax_url(client, asin)
        if url:
            return _save_aax(auth, asin, safe_title, output_dir, url)

    console.print(f"[red]✗ Kein Download-Link gefunden für {asin}[/red]")
    return None


def _build_headers(auth: audible.Authenticator) -> dict:
    # Audible-API-Auth-Header aus dem registrierten Gerät
    try:
        return dict(auth.access_headers)
    except Exception:
        return {}


def _save_aax(
    auth: audible.Authenticator,
    asin: str,
    safe_title: str,
    output_dir: Path,
    url: str,
) -> Path:
    dest = output_dir / f"{safe_title}.aax"
    console.print(f"[cyan]↓ AAX:[/cyan] {dest.name}")
    _stream_download(url, dest, safe_title, _build_headers(auth))
    console.print(f"[green]✓ Gespeichert:[/green] {dest}")
    return dest


def _save_aaxc(
    auth: audible.Authenticator,
    asin: str,
    safe_title: str,
    output_dir: Path,
    data: dict,
) -> Path:
    dest = output_dir / f"{safe_title}.aaxc"
    voucher_path = output_dir / f"{safe_title}.voucher"

    console.print(f"[cyan]↓ AAXC:[/cyan] {dest.name}")
    _stream_download(data["url"], dest, safe_title, _build_headers(auth))

    # Voucher neben die Audiodatei speichern (wird für Konvertierung benötigt)
    voucher_data = {
        "asin": asin,
        "key": data.get("key", ""),
        "iv": data.get("iv", ""),
    }
    voucher_path.write_text(json.dumps(voucher_data, indent=2))
    console.print(f"[green]✓ Gespeichert:[/green] {dest}")
    console.print(f"[dim]  Voucher:    {voucher_path}[/dim]")
    return dest


def get_activation_bytes(auth: audible.Authenticator) -> Optional[str]:
    """Holt die Activation Bytes für AAX-Entschlüsselung.

    Nutzt Authenticator.get_activation_bytes() (holt/entschlüsselt den
    Activation-Blob vom Standard-Endpunkt und cached ihn in der
    Authenticator-Instanz) statt eines nicht existierenden REST-Endpunkts
    "1.0/activation/activationbytes" (lieferte früher immer None).
    """
    try:
        ab = auth.get_activation_bytes()
        if ab:
            return ab
    except Exception:
        pass
    return None
