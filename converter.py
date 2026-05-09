import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

console = Console()
log = logging.getLogger(__name__)

_BREW_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
]


def _find_tool(name: str) -> str:
    """Findet ffmpeg/ffprobe: zuerst im App-Bundle, dann im System."""
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / name
        if bundled.exists():
            return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    for d in _BREW_DIRS:
        p = Path(d) / name
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        f"'{name}' nicht gefunden. Bitte installieren: brew install ffmpeg"
    )


SUPPORTED_FORMATS = {
    "m4b":  ["-c", "copy"],
    "m4a":  ["-c", "copy"],
    "mp3":  ["-c:a", "libmp3lame", "-q:a", "2", "-vn"],
    "flac": ["-c:a", "flac", "-vn"],
    "ogg":  ["-c:a", "libvorbis", "-q:a", "6", "-vn"],
    "opus": ["-c:a", "libopus", "-b:a", "128k", "-vn"],
}


def _get_duration_ms(src: Path, decrypt_args: list) -> Optional[float]:
    """Gesamtdauer der Datei in Millisekunden via ffprobe."""
    try:
        cmd = [
            _find_tool("ffprobe"), "-v", "quiet",
            *decrypt_args,
            "-i", str(src),
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        val = r.stdout.strip()
        log.debug(f"ffprobe {src.name}: stdout='{val}' stderr='{r.stderr.strip()[:120]}'")
        if val and val != "N/A":
            return float(val) * 1000.0
    except Exception as e:
        log.warning(f"ffprobe fehlgeschlagen für {src.name}: {e}")
    return None


def _run_ffmpeg(
    args: list,
    decrypt_args: list,
    src: Path,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> int:
    ffmpeg = _find_tool("ffmpeg")

    if progress_cb is None:
        result = subprocess.run([ffmpeg, "-y", "-loglevel", "error"] + args)
        return result.returncode

    total_ms = _get_duration_ms(src, decrypt_args)

    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-progress", "pipe:1",
        "-stats_period", "0.5",
    ] + args

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    for line in proc.stdout:
        key, _, val = line.strip().partition("=")
        val = val.strip()
        if key == "out_time" and total_ms and total_ms > 0:
            # out_time Format: HH:MM:SS.mmm
            try:
                h, m, s = val.split(":")
                out_ms = (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
                pct = min(99, int(out_ms / total_ms * 100))
                progress_cb(pct, val)
            except (ValueError, ZeroDivisionError):
                pass
        elif key == "progress" and val == "end":
            progress_cb(100, "fertig")

    proc.wait()
    return proc.returncode


def _convert(
    src: Path,
    fmt: str,
    decrypt: list,
    output_dir: Optional[Path],
    progress_cb: Optional[Callable[[int, str], None]],
) -> Optional[Path]:
    out_args = SUPPORTED_FORMATS.get(fmt)
    if out_args is None:
        log.error(f"Unbekanntes Format: {fmt}")
        console.print(f"[red]Unbekanntes Format: {fmt}[/red]")
        return None
    dest = _build_dest(src, fmt, output_dir)
    console.print(f"[cyan]Konvertiere[/cyan] {src.name} → {dest.name}")
    rc = _run_ffmpeg(decrypt + ["-i", str(src)] + out_args + [str(dest)], decrypt, src, progress_cb)
    if rc != 0:
        log.error(f"ffmpeg fehlgeschlagen (Exit {rc}) für {src.name}")
        dest.unlink(missing_ok=True)
        return None
    console.print(f"[green]✓ Fertig:[/green] {dest}")
    return dest


def convert_aax(
    src: Path,
    fmt: str,
    activation_bytes: str,
    output_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Optional[Path]:
    return _convert(src, fmt, ["-activation_bytes", activation_bytes], output_dir, progress_cb)


def convert_aaxc(
    src: Path,
    fmt: str,
    key: str,
    iv: str,
    output_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Optional[Path]:
    return _convert(src, fmt, ["-audible_key", key, "-audible_iv", iv], output_dir, progress_cb)


def convert_file(
    src: Path,
    fmt: str,
    activation_bytes: Optional[str] = None,
    key: Optional[str] = None,
    iv: Optional[str] = None,
    output_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Optional[Path]:
    suffix = src.suffix.lower()
    if suffix == ".aax":
        if not activation_bytes:
            console.print("[red]Für AAX-Dateien werden Activation Bytes benötigt.[/red]")
            return None
        return convert_aax(src, fmt, activation_bytes, output_dir, progress_cb)

    if suffix == ".aaxc":
        if not (key and iv):
            voucher_path = src.with_suffix(".voucher")
            if voucher_path.exists():
                voucher = json.loads(voucher_path.read_text())
                key = voucher.get("key", "")
                iv = voucher.get("iv", "")
            else:
                console.print(f"[red]Keine Voucher-Datei gefunden: {voucher_path}[/red]")
                return None
        return convert_aaxc(src, fmt, key, iv, output_dir, progress_cb)

    console.print(f"[red]Nicht unterstütztes Format: {suffix}[/red]")
    return None


def _build_dest(src: Path, fmt: str, output_dir: Optional[Path]) -> Path:
    base = output_dir or src.parent
    return base / f"{src.stem}.{fmt}"


def _get_chapters(src: Path) -> list:
    """Reads chapter metadata from a decrypted M4B file via ffprobe."""
    try:
        cmd = [
            _find_tool("ffprobe"), "-v", "quiet",
            "-i", str(src),
            "-print_format", "json",
            "-show_chapters",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("chapters", [])
    except Exception as e:
        log.warning(f"Chapter-Erkennung fehlgeschlagen für {src.name}: {e}")
        return []


def split_to_chapters(
    src: Path,
    fmt: str,
    decrypt_args: list,
    output_dir: Optional[Path],
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> list:
    """Splits an encrypted AAX/AAXC file into per-chapter files.

    Returns list of output Paths on success, empty list on failure.
    """
    out_args = SUPPORTED_FORMATS.get(fmt)
    if out_args is None:
        log.error(f"Unbekanntes Format: {fmt}")
        return []

    base_dir = output_dir or src.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 – decrypt to a temporary M4B (lossless copy)
    tmp = base_dir / f"_tmp_{src.stem}.m4b"
    ffmpeg = _find_tool("ffmpeg")
    log.info(f"split_to_chapters: ffmpeg={ffmpeg}, src={src}, tmp={tmp}")
    log.info(f"split_to_chapters: decrypt_args={decrypt_args[:4]}…")  # log first 4 args only (no key values)
    if progress_cb:
        progress_cb(0, "Entschlüssele…")
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error"]
            + decrypt_args
            + ["-i", str(src), "-c", "copy", str(tmp)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            log.error(f"Entschlüsselung fehlgeschlagen (rc={proc.returncode}): {proc.stderr.strip()}")
        rc = proc.returncode
    except Exception as exc:
        log.exception(f"Subprocess-Fehler beim Entschlüsseln: {exc}")
        tmp.unlink(missing_ok=True)
        return []
    if rc != 0 or not tmp.exists():
        log.error(f"Entschlüsselung fehlgeschlagen für {src.name}, tmp exists={tmp.exists()}")
        tmp.unlink(missing_ok=True)
        return []

    # Step 2 – read chapters
    chapters = _get_chapters(tmp)
    if not chapters:
        log.warning(f"Keine Kapitel gefunden in {src.name}, überspringe Split")
        tmp.unlink(missing_ok=True)
        return []

    total = len(chapters)
    log.info(f"split_to_chapters: {total} Kapitel gefunden in {tmp.name}")
    out_files = []

    # Step 3 – extract each chapter
    for idx, ch in enumerate(chapters):
        tags = ch.get("tags", {})
        title = tags.get("title") or f"Kapitel {idx + 1:02d}"
        safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
        num = f"{idx + 1:02d}"
        dest = base_dir / f"{num} – {safe_title}.{fmt}"

        start = float(ch.get("start_time", 0))
        end   = float(ch.get("end_time", 0))
        duration = end - start

        if duration <= 0:
            continue

        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", str(start),
            "-i", str(tmp),
            "-t", str(duration),
        ] + out_args + [str(dest)]

        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                log.error(f"Kapitel {idx+1} fehlgeschlagen (rc={r.returncode}): {r.stderr.strip()[:200]}")
            else:
                out_files.append(dest)
        except Exception as exc:
            log.exception(f"Subprocess-Fehler bei Kapitel {idx+1}: {exc}")
            break

        if progress_cb:
            pct = int((idx + 1) / total * 100)
            progress_cb(pct, f"Kapitel {idx + 1}/{total}: {title[:40]}")

    tmp.unlink(missing_ok=True)
    return out_files
