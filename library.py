from typing import Optional

import audible
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

RESPONSE_GROUPS = (
    "product_desc,product_attrs,relationships,"
    "product_extended_attrs,series,media"
)


def fetch_library(auth: audible.Authenticator, max_items: int = 999) -> list[dict]:
    items = []
    page = 1
    page_size = 100

    with audible.Client(auth=auth) as client:
        with console.status("[bold cyan]Bibliothek wird geladen...[/bold cyan]"):
            while True:
                resp = client.get(
                    "1.0/library",
                    num_results=page_size,
                    page=page,
                    response_groups=RESPONSE_GROUPS,
                    sort_by="-PurchaseDate",
                )
                batch = resp.get("items", [])
                items.extend(batch)
                if len(batch) < page_size or len(items) >= max_items:
                    break
                page += 1

    return items


def display_library(items: list[dict], filter_text: Optional[str] = None):
    if filter_text:
        fl = filter_text.lower()
        items = [
            i for i in items
            if fl in (i.get("title") or "").lower()
            or fl in (i.get("authors") or [{}])[0].get("name", "").lower()
        ]

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("ASIN", style="cyan", width=12)
    table.add_column("Titel", min_width=30)
    table.add_column("Autor", min_width=20)
    table.add_column("Laufzeit", width=10, justify="right")
    table.add_column("Format", width=6, justify="center")

    for idx, item in enumerate(items, 1):
        asin = item.get("asin", "—")
        title = item.get("title") or "Unbekannt"
        authors = item.get("authors") or []
        author = authors[0].get("name", "—") if authors else "—"

        minutes = item.get("runtime_length_min") or 0
        if minutes:
            h, m = divmod(int(minutes), 60)
            duration = f"{h}h {m:02d}m"
        else:
            duration = "—"

        fmt = _detect_format(item)
        fmt_style = "green" if fmt == "AAX" else "yellow"

        table.add_row(
            str(idx),
            asin,
            title,
            author,
            duration,
            f"[{fmt_style}]{fmt}[/{fmt_style}]",
        )

    console.print(table)
    console.print(f"\n[dim]Gesamt: {len(items)} Titel[/dim]")


def _detect_format(item: dict) -> str:
    """Audible Plus / Abo-Inhalte liefern AAXC, gekaufte Titel AAX."""
    available = item.get("available_codecs") or []
    codec_names = [c.get("name", "").upper() for c in available]
    if any("AAXC" in n for n in codec_names):
        return "AAXC"
    return "AAX"


def find_item(items: list[dict], query: str) -> Optional[dict]:
    query_lower = query.lower()
    for item in items:
        if item.get("asin", "").upper() == query.upper():
            return item
        if query_lower in (item.get("title") or "").lower():
            return item
    return None
