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
    """Holt die Download-URL für standard AAX-Inhalte (gekaufte Bücher)."""
    try:
        resp = client.get(
            f"1.0/content/{asin}/licenserequest",
            quality="Extreme",
            consumption_type="Download",
            requested_codec="AAX_22_128",
            supported_drm_types="Adrm",
        )
        meta = resp.get("content_license", {}).get("content_metadata", {})
        return meta.get("content_url", {}).get("offline_url")
    except Exception:
        return None


def _get_aaxc_data(client: audible.Client, asin: str) -> Optional[dict]:
    """Holt URL + Voucher für AAXC-Inhalte (Audible Plus / Abo-Katalog)."""
    try:
        resp = client.post(
            "1.0/content/licenserequest",
            body={
                "asins": [asin],
                "quality": "Extreme",
                "consumption_type": "Download",
                "requested_codec": "AAXC",
                "supported_drm_types": ["Adrm"],
                "fulfill_drm": True,
            },
        )
        licenses = resp.get("content_licenses", [])
        if not licenses:
            return None
        lic = licenses[0]
        meta = lic.get("content_metadata", {})
        url = meta.get("content_url", {}).get("offline_url")
        voucher = lic.get("voucher", {})
        key = voucher.get("key") or meta.get("adrm_key")
        iv = voucher.get("iv") or meta.get("adrm_iv")
        if not url:
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
        # Versuche zunächst das bevorzugte Format, dann das andere
        if prefer_aaxc:
            data = _get_aaxc_data(client, asin)
            if data:
                return _save_aaxc(auth, asin, safe_title, output_dir, data)
            url = _get_aax_url(client, asin)
            if url:
                return _save_aax(auth, asin, safe_title, output_dir, url)
        else:
            url = _get_aax_url(client, asin)
            if url:
                return _save_aax(auth, asin, safe_title, output_dir, url)
            data = _get_aaxc_data(client, asin)
            if data:
                return _save_aaxc(auth, asin, safe_title, output_dir, data)

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
    """Holt die Activation Bytes für AAX-Entschlüsselung via Audible-API."""
    try:
        ab = auth.activation_bytes
        if ab:
            return ab
    except Exception:
        pass
    try:
        with audible.Client(auth=auth) as client:
            resp = client.get("1.0/activation/activationbytes")
            ab = resp.get("activation_bytes")
            if ab:
                # In Auth-Datei cachen
                auth.activation_bytes = ab
                auth.to_file(str(auth._filename if hasattr(auth, "_filename") else ""))
                return ab
    except Exception:
        pass
    return None
