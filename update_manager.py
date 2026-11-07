"""update_manager - GitHub-Release-Abfrage und Asset-Download.

Nach dem Muster des Updaters aus Florians TeamTalk-VO-Client-Projekt
(src/update_manager.py), angepasst an die hier tatsaechlich produzierten
Release-Assets (AudibleDownloader-<plattform>.<ext> statt .pkg/.deb/.exe).

Oeffentliche API:
    get_current_version()        -> str
    fetch_latest_release()       -> Optional[Release]
    is_newer(remote, local)      -> bool
    get_platform_asset(release)  -> Optional[ReleaseAsset]
    download_asset(asset, dest_dir, progress_cb) -> str (Pfad zur Datei)
    install_update(path)         -> tuple[bool, str]
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

_GITHUB_API = "https://api.github.com/repos/fla-rion/audible-cli-gui/releases"
_HEADERS = {"User-Agent": "audible-cli-gui-UpdateManager"}


def _ssl_context() -> Optional[ssl.SSLContext]:
    """certifi statt System-Vertrauensspeicher - in einer per PyInstaller
    gebauten/eingefrorenen App ist das die zuverlaessigere Quelle fuer das
    CA-Bundle (siehe gleichnamige Begruendung in TeamTalk-VO-Client)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


_SSL_CONTEXT = _ssl_context()


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int  # Bytes


@dataclass
class Release:
    tag: str
    name: str
    date: str  # "YYYY-MM-DD"
    body: str  # Changelog-Text
    assets: List[ReleaseAsset] = field(default_factory=list)


def get_current_version() -> str:
    from version import __version__
    return __version__


def _parse_version(v: str) -> Tuple[int, ...]:
    v = v.strip().lstrip("vV")
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def fetch_releases(limit: int = 20) -> List[Release]:
    """Holt alle (nicht-Draft/Prerelease) Releases von der GitHub-API."""
    url = f"{_GITHUB_API}?per_page={limit}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    result: List[Release] = []
    for r in data:
        if r.get("draft") or r.get("prerelease"):
            continue
        assets = [
            ReleaseAsset(
                name=a["name"],
                download_url=a["browser_download_url"],
                size=int(a.get("size", 0)),
            )
            for a in r.get("assets", [])
        ]
        result.append(Release(
            tag=r["tag_name"],
            name=r.get("name") or r["tag_name"],
            date=(r.get("published_at") or "")[:10],
            body=(r.get("body") or "").strip(),
            assets=assets,
        ))
    return result


def fetch_latest_release() -> Optional[Release]:
    releases = fetch_releases(limit=5)
    return releases[0] if releases else None


def check_for_update() -> Optional[Release]:
    """Gibt das neueste Release zurueck, wenn es neuer ist als die
    aktuell installierte Version - sonst None."""
    try:
        latest = fetch_latest_release()
    except Exception:
        return None
    if latest and is_newer(latest.tag, get_current_version()):
        return latest
    return None


# Reihenfolge der Asset-Endungen je Plattform, wie sie der Release-Workflow
# dieses Repos tatsaechlich erzeugt (AudibleDownloader-<plattform>.<ext>) -
# NICHT die Installer-Endungen (.pkg/.deb/-setup.exe) von TeamTalk-VO-Client,
# die gibt es hier (noch) nicht.
_ASSET_SUFFIXES = {
    "darwin": ".dmg",
    "win32": ".zip",
}


def _linux_arch_suffix() -> str:
    import platform
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return f"linux-{arch}.tar.gz"


def get_platform_asset(release: Release) -> Optional[ReleaseAsset]:
    suffix = _ASSET_SUFFIXES.get(sys.platform) or _linux_arch_suffix()
    for asset in release.assets:
        if asset.name.lower().endswith(suffix):
            return asset
    return None


def download_asset(
    asset: ReleaseAsset,
    dest_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Laedt ein Asset herunter, gibt den Zielpfad zurueck.
    progress_cb(downloaded_bytes, total_bytes) wird regelmaessig aufgerufen."""
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, asset.name)
    req = urllib.request.Request(asset.download_url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=120, context=_SSL_CONTEXT) as resp:
        total = int(resp.headers.get("Content-Length") or asset.size or 0)
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)
    return dest_path


def install_update(path: str) -> Tuple[bool, str]:
    """Oeffnet das heruntergeladene Update-Archiv/Image. Da dieses Repo
    (anders als TeamTalk-VO-Client) keine fertigen Installer baut, sondern
    nur .dmg/.zip/.tar.gz, kann hier nicht automatisch installiert werden -
    wir oeffnen das Archiv im jeweiligen Standardprogramm/Dateimanager,
    der Nutzer zieht die neue App dann selbst an ihren Platz (macOS: aus dem
    .dmg ins Programme-Verzeichnis ziehen, wie bei jeder Mac-App)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError as exc:
        return False, str(exc)
    return True, "Update-Datei geoeffnet"


def format_size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"
