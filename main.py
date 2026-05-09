#!/usr/bin/env python3
"""Audible Downloader – Bibliothek anzeigen, herunterladen, konvertieren."""

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "Audible"


# ---------------------------------------------------------------------------
# CLI-Gruppe
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Audible Downloader

    Bücher aus der Audible-Bibliothek herunterladen und in M4B oder MP3 konvertieren.

    \b
    Schnellstart:
      python main.py login
      python main.py library
      python main.py download ASIN
    """


# ---------------------------------------------------------------------------
# Authentifizierung
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--locale", "-l",
    default="de",
    show_default=True,
    type=click.Choice(["de","us","uk","fr","it","es","ca","au","jp","in","br"]),
    help="Audible-Marktplatz",
)
def login(locale):
    """Bei Audible via Amazon-Browser-Login anmelden.

    Es wird eine URL angezeigt. Nach dem Login im Browser
    die Redirect-URL aus der Adressleiste einfügen.
    """
    from auth import login as do_login
    do_login(locale=locale)


@cli.command()
def logout():
    """Abmelden und Gerät deregistrieren."""
    from auth import logout as do_logout
    do_logout()


@cli.command()
def status():
    """Anmeldestatus anzeigen."""
    from auth import show_status
    show_status()


# ---------------------------------------------------------------------------
# Bibliothek
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--filter", "-f", "filter_text", default=None, help="Titel/Autor filtern")
def library(filter_text):
    """Alle Titel der Audible-Bibliothek anzeigen."""
    from auth import load_auth
    from library import fetch_library, display_library

    auth = load_auth()
    items = fetch_library(auth)
    display_library(items, filter_text=filter_text)


# ---------------------------------------------------------------------------
# Herunterladen
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("asin")
@click.option(
    "--output-dir", "-o",
    default=str(DEFAULT_DOWNLOAD_DIR),
    show_default=True,
    help="Zielverzeichnis",
)
@click.option(
    "--format", "-f", "convert_fmt",
    default=None,
    type=click.Choice(["m4b", "mp3"]),
    help="Nach dem Download sofort in dieses Format konvertieren",
)
@click.option(
    "--aaxc", is_flag=True, default=False,
    help="AAXC bevorzugen (Audible-Plus-/Abo-Inhalte)",
)
def download(asin, output_dir, convert_fmt, aaxc):
    """Ein Buch per ASIN herunterladen.

    ASIN findet man in der Bibliotheksübersicht (python main.py library).
    """
    from auth import load_auth
    from library import fetch_library, find_item
    from downloader import download_book, get_activation_bytes
    from converter import convert_file

    auth = load_auth()
    out = Path(output_dir).expanduser()

    # Titel für Dateinamen ermitteln
    with console.status("Bibliothek abfragen..."):
        items = fetch_library(auth)
    item = find_item(items, asin)
    title = item.get("title", asin) if item else asin

    downloaded = download_book(auth, asin, title, out, prefer_aaxc=aaxc)
    if not downloaded:
        raise SystemExit(1)

    if convert_fmt:
        _auto_convert(auth, downloaded, convert_fmt, out)


@cli.command("download-all")
@click.option(
    "--output-dir", "-o",
    default=str(DEFAULT_DOWNLOAD_DIR),
    show_default=True,
)
@click.option(
    "--format", "-f", "convert_fmt",
    default=None,
    type=click.Choice(["m4b", "mp3"]),
    help="Sofort nach Download konvertieren",
)
@click.option(
    "--aaxc", is_flag=True, default=False,
    help="AAXC für Abo-Inhalte bevorzugen",
)
@click.option(
    "--skip-existing", is_flag=True, default=True, show_default=True,
    help="Bereits vorhandene Dateien überspringen",
)
def download_all(output_dir, convert_fmt, aaxc, skip_existing):
    """Alle Titel der Bibliothek herunterladen."""
    import re
    from auth import load_auth
    from library import fetch_library
    from downloader import download_book, get_activation_bytes
    from converter import convert_file

    auth = load_auth()
    out = Path(output_dir).expanduser()

    items = fetch_library(auth)
    console.print(f"\n[bold]{len(items)} Titel gefunden.[/bold]\n")

    failed = []
    for idx, item in enumerate(items, 1):
        asin = item.get("asin", "")
        title = item.get("title") or asin
        safe = re.sub(r'[<>:"/\\|?*]', "_", title).strip()

        console.rule(f"[{idx}/{len(items)}] {title[:60]}")

        if skip_existing:
            existing = list(out.glob(f"{safe}.*"))
            if existing:
                console.print(f"[dim]Übersprungen (bereits vorhanden): {existing[0].name}[/dim]")
                continue

        downloaded = download_book(auth, asin, title, out, prefer_aaxc=aaxc)
        if not downloaded:
            failed.append(f"{asin} – {title}")
            continue

        if convert_fmt:
            _auto_convert(auth, downloaded, convert_fmt, out)

    if failed:
        console.print(f"\n[red]Fehlgeschlagen ({len(failed)}):[/red]")
        for f in failed:
            console.print(f"  [red]•[/red] {f}")


# ---------------------------------------------------------------------------
# Konvertieren
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--format", "-f", "fmt",
    required=True,
    type=click.Choice(["m4b", "mp3"]),
    help="Zielformat",
)
@click.option("--activation-bytes", "-a", default=None, help="Für AAX-Dateien")
@click.option("--key", default=None, help="Für AAXC-Dateien (aus Voucher)")
@click.option("--iv", default=None, help="Für AAXC-Dateien (aus Voucher)")
@click.option("--output-dir", "-o", default=None, help="Zielverzeichnis (Standard: neben Quelldatei)")
def convert(file, fmt, activation_bytes, key, iv, output_dir):
    """Eine heruntergeladene Datei in M4B oder MP3 konvertieren.

    Bei AAXC-Dateien wird die .voucher-Datei automatisch gesucht.
    """
    from converter import convert_file

    src = Path(file)
    out = Path(output_dir).expanduser() if output_dir else None
    convert_file(src, fmt, activation_bytes=activation_bytes, key=key, iv=iv, output_dir=out)


# ---------------------------------------------------------------------------
# Activation Bytes
# ---------------------------------------------------------------------------

@cli.command("activation-bytes")
@click.option("--show", is_flag=True, default=False, help="Bytes im Terminal anzeigen")
def activation_bytes_cmd(show):
    """Activation Bytes von Audible abrufen (für AAX-Konvertierung nötig)."""
    from auth import load_auth
    from downloader import get_activation_bytes

    auth = load_auth()
    ab = get_activation_bytes(auth)
    if ab:
        console.print(f"[green]✓ Activation Bytes erhalten.[/green]")
        if show:
            console.print(f"[bold cyan]{ab}[/bold cyan]")
    else:
        console.print("[red]Activation Bytes konnten nicht abgerufen werden.[/red]")
        console.print("[dim]Manche Konten benötigen einen separaten Aktivierungsschritt.[/dim]")


# ---------------------------------------------------------------------------
# Hilfsfunktion
# ---------------------------------------------------------------------------

def _auto_convert(auth, downloaded: Path, convert_fmt: str, out: Path):
    from downloader import get_activation_bytes
    from converter import convert_file

    ab = None
    if downloaded.suffix == ".aax":
        with console.status("Activation Bytes abrufen..."):
            ab = get_activation_bytes(auth)
        if not ab:
            console.print("[yellow]Konvertierung übersprungen: keine Activation Bytes.[/yellow]")
            return

    convert_file(downloaded, convert_fmt, activation_bytes=ab, output_dir=out)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
