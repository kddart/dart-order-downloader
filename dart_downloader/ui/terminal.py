from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

from dart_downloader.download.engine import DownloadEngine
from dart_downloader.formatting import format_bytes as _format_bytes
from dart_downloader.models.models import DownloadProgress

console = Console()

WELCOME_TEXT = """[bold cyan]Welcome to the DArT Download Assistant[/bold cyan]

This tool downloads genomic data associated with an order.

[dim]Don't have a share token?[/dim]
[link=https://ordering.diversityarrays.com/token.pl]https://ordering.diversityarrays.com/token.pl[/link]
"""


def show_welcome():
    console.print(Panel(WELCOME_TEXT, border_style="cyan"))


def prompt_inputs() -> tuple[str, str, Path]:
    console.print()
    # Mask the token: it's a secret, so hide it as it's typed/pasted (no echo).
    share_token = Prompt.ask("[bold]Share Token[/bold]", password=True)
    order_number = Prompt.ask("[bold]Order Number[/bold]")
    # Default to a folder in the current directory — predictable and portable
    # across macOS/Windows/Linux, with no assumptions about a ~/Downloads folder.
    # The leading "./" signals that this is a path the user can replace with an
    # absolute path if they prefer.
    default_folder = f"./Order_{order_number}"
    download_folder = Prompt.ask(
        "[bold]Download Folder[/bold]", default=default_folder
    )
    return share_token, order_number, Path(download_folder)


def show_order_summary(order_number: str, total_files: int, total_size: int):
    table = Table(title="Order Summary", show_header=False, border_style="green")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Order", order_number)
    table.add_row("Files to Download", str(total_files))
    table.add_row("Total Size", _format_bytes(total_size))
    console.print(table)
    console.print()


def show_downloading():
    console.print("[bold]Downloading files...[/bold]")
    console.print()


def show_download_complete():
    console.print()
    console.print("[bold green]✓ Download complete.[/bold green]")
    console.print()


def show_validating():
    console.print("[dim]Validating downloaded files (checking file sizes)...[/dim]")


def show_validation_complete(valid_count: int, total_count: int):
    console.print(
        f"[green]✓ Validation passed:[/green] {valid_count}/{total_count} files match expected size."
    )
    console.print()


def show_md5_verified(verified_count: int, total_count: int):
    console.print(
        f"[green]✓ MD5 verified:[/green] {verified_count}/{total_count} files match their MD5 checksum."
    )
    console.print()


def show_md5_unavailable_warning():
    """Prominently warn the user that no MD5SUMS file was available, so file
    contents could not be checksum-verified (only file sizes were checked)."""
    console.print(
        Panel(
            "[bold]No MD5SUMS file was available for this order.[/bold]\n\n"
            "Files were checked for completeness by [bold]size only[/bold] — their "
            "contents could [bold]not[/bold] be verified against MD5 checksums.\n"
            "If you need checksum verification, contact DArT to confirm an "
            "MD5SUMS file is included with your order.",
            title="⚠  MD5 checksum verification unavailable",
            border_style="yellow",
            title_align="left",
        )
    )
    console.print()


def confirm_no_md5sums() -> bool:
    """Warn, before downloading, that the order has no MD5SUMS file and ask the
    user whether to continue. Returns True to proceed, False to abort.

    Pressing Ctrl-C (or answering no) lets the user stop before any data is
    downloaded — e.g. to ask DArT for an order that includes checksums.
    """
    console.print(
        Panel(
            "[bold]This order does not include an MD5SUMS file.[/bold]\n\n"
            "Downloads will be checked by [bold]file size only[/bold]; their "
            "contents [bold]cannot[/bold] be verified against MD5 checksums.\n\n"
            "[dim]Press Ctrl-C now to abort if you'd like to obtain an order "
            "with checksums first.[/dim]",
            title="⚠  No MD5 checksums for this order",
            border_style="yellow",
            title_align="left",
        )
    )
    try:
        answer = Prompt.ask(
            "Continue downloading without MD5 verification?",
            choices=["y", "n"],
            default="y",
        )
    except (KeyboardInterrupt, EOFError):
        return False
    console.print()
    return answer == "y"


def run_with_progress(engine: DownloadEngine, total_files: int, total_bytes: int):
    show_downloading()
    stop_event = Event()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    overall_task = progress.add_task("Downloading", total=total_bytes)

    def update_loop():
        while not stop_event.is_set():
            downloaded = sum(p.bytes_downloaded for p in engine.progress.values())
            progress.update(overall_task, completed=downloaded)
            time.sleep(0.5)

    with progress:
        updater = Thread(target=update_loop, daemon=True)
        updater.start()
        engine.run()
        stop_event.set()
        updater.join(timeout=2)
        downloaded = sum(p.bytes_downloaded for p in engine.progress.values())
        progress.update(overall_task, completed=downloaded)

    show_download_complete()


def show_report(report: dict, download_folder: Path):
    console.print(Panel("[bold green]All Done[/bold green]", border_style="green"))
    console.print()

    table = Table(show_header=False, border_style="blue")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Order Number", report["order_number"])
    table.add_row("Download Folder", report["download_folder"])
    table.add_row("Files Downloaded", str(report["files_downloaded"]))
    table.add_row("Files Validated by Size", str(sum(1 for f in report["downloaded_files"] if f["validated"])))
    if report["md5sums_available"]:
        table.add_row("Files MD5 Verified", str(report["files_md5_verified"]))
    else:
        table.add_row("Files MD5 Verified", "[yellow]n/a - no MD5SUMS[/yellow]")
    table.add_row("Files Failed", str(report["files_failed"]))
    table.add_row("Total Downloaded", report["total_data_downloaded"])
    table.add_row("Duration", report["total_duration"])
    table.add_row("Avg Throughput", report["average_throughput"])
    console.print(table)

    if report["failed_files"]:
        console.print()
        console.print("[bold red]Failed Files:[/bold red]")
        for entry in report["failed_files"]:
            console.print(f"  [red]✗[/red] {entry['filename']} — {entry['error']}")

    console.print()
    json_path = download_folder / "download_report.json"
    txt_path = download_folder / "download_report.txt"
    console.print(f"[dim]For full details, see:[/dim]")
    console.print(f"  [dim]{json_path}[/dim]")
    console.print(f"  [dim]{txt_path}[/dim]")
    console.print()


def show_error(message: str):
    console.print(f"[bold red]Error:[/bold red] {message}")


def show_resuming():
    console.print("[yellow]Existing download detected. Resuming where we left off...[/yellow]")
    console.print()
