from pathlib import Path
from typing import Callable, Optional

import audible
from rich.console import Console
from rich.table import Table

console = Console()

CONFIG_DIR = Path.home() / ".audible_tool"
AUTH_FILE = CONFIG_DIR / "auth.json"

LOCALES = {
    "de": "Deutschland (Amazon.de)",
    "us": "USA (Amazon.com)",
    "uk": "Großbritannien (Amazon.co.uk)",
    "fr": "Frankreich (Amazon.fr)",
    "it": "Italien (Amazon.it)",
    "es": "Spanien (Amazon.es)",
    "ca": "Kanada (Amazon.ca)",
    "au": "Australien (Amazon.com.au)",
    "jp": "Japan (Amazon.co.jp)",
    "in": "Indien (Amazon.in)",
    "br": "Brasilien (Amazon.com.br)",
}


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def login(locale: str = "de", login_url_callback: Optional[Callable] = None) -> audible.Authenticator:
    """
    Meldet sich via Amazon-OAuth-Browser-Flow an.

    login_url_callback(url) -> redirect_url:
      - Im App-Modus: navigiert das pywebview-Fenster zur Amazon-Login-Seite
        und gibt die maplanding-URL nach erfolgreichem Login zurück.
      - None: audible-Bibliothek fragt interaktiv nach der Redirect-URL (CLI-Modus).
    """
    _ensure_config_dir()
    locale_name = LOCALES.get(locale, locale)
    console.print(f"\n[bold cyan]Anmelden bei Audible[/bold cyan] ({locale_name})")

    if login_url_callback is None:
        # CLI-Modus: URL wird auf der Konsole ausgegeben, User kopiert Redirect-URL
        console.print("[dim]Browser-Login: Die URL wird angezeigt.[/dim]")
        def _manual_callback(url: str) -> str:
            console.print(f"\n[bold yellow]Bitte diese URL im Browser öffnen:[/bold yellow]")
            console.print(f"[cyan]{url}[/cyan]\n")
            console.print("[dim]Nach dem Login zeigt der Browser eine Fehlerseite.[/dim]")
            console.print("[dim]Bitte die URL aus der Adressleiste kopieren:[/dim]")
            return input("Redirect-URL einfügen: ").strip()
        login_url_callback = _manual_callback

    try:
        import uuid
        serial = uuid.uuid4().hex.upper()
        auth = audible.Authenticator.from_login_external(
            locale=locale,
            login_url_callback=login_url_callback,
            serial=serial
        )
        auth.to_file(str(AUTH_FILE))
        console.print("[bold green]✓ Erfolgreich angemeldet![/bold green]")
        return auth
    except Exception as e:
        console.print(f"[red]Anmeldefehler: {e}[/red]")
        raise


def load_auth() -> audible.Authenticator:
    if not AUTH_FILE.exists():
        console.print("[red]Nicht angemeldet. Bitte zuerst anmelden.[/red]")
        raise SystemExit(1)
    return audible.Authenticator.from_file(str(AUTH_FILE))


def logout():
    if not AUTH_FILE.exists():
        console.print("[yellow]Bereits abgemeldet.[/yellow]")
        return
    try:
        auth = load_auth()
        auth.deregister_device()
        console.print("[yellow]Gerät bei Audible abgemeldet.[/yellow]")
    except Exception:
        pass
    AUTH_FILE.unlink(missing_ok=True)
    console.print("[green]✓ Erfolgreich abgemeldet.[/green]")


def show_status():
    if not AUTH_FILE.exists():
        console.print("[yellow]Status: Nicht angemeldet[/yellow]")
        return
    try:
        auth = load_auth()
        locale_name = LOCALES.get(str(auth.locale), str(auth.locale))
        table = Table(show_header=False, box=None)
        table.add_column(style="bold")
        table.add_column()
        table.add_row("Status:", "[green]Angemeldet[/green]")
        table.add_row("Marktplatz:", locale_name)
        console.print(table)
    except Exception as e:
        console.print(f"[red]Auth-Datei beschädigt: {e}[/red]")
