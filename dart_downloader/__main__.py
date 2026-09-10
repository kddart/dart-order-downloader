from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dart_downloader import __version__
from dart_downloader.api.client import DEFAULT_HOST, DartApiClient, base_url_for_host
from dart_downloader.download.engine import DownloadEngine
from dart_downloader.models.models import FileStatus
from dart_downloader.reporting.report import ReportGenerator
from dart_downloader.state.manager import StateManager
from dart_downloader.ui import terminal
from dart_downloader.validation.providers import MD5SUMS_FILENAME, SizeValidationProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dart_downloader",
        description="DArT Genomic Data Download Assistant",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of simultaneous downloads (default: 8)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("DART_ORDERING_HOST", DEFAULT_HOST),
        help=(
            "Ordering server host to fetch the order from "
            "(default: %(default)s; also settable via the DART_ORDERING_HOST "
            "environment variable). Accepts a bare host or a full URL."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def setup_logging(debug: bool):
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )


def main():
    """Console entry point. Owns all top-level error handling and the exit
    code, so that every way of invoking the tool (``python -m dart_downloader``
    or a direct ``main()`` call) behaves identically.
    """
    try:
        exit_code = _run()
    except SystemExit as e:
        # _run() (or code it calls) requested a specific exit code.
        exit_code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except KeyboardInterrupt:
        terminal.console.print("\n[dim]Cancelled.[/dim]")
        exit_code = 130
    except Exception as e:  # noqa: BLE001 - top-level safety net
        terminal.show_error(str(e))
        exit_code = 1

    sys.exit(exit_code)


def _run() -> int:
    """Run the download workflow. Returns the process exit code (0 = success,
    1 = one or more files failed). May raise; main() is the single place that
    turns exceptions into exit codes and the exit pause."""
    args = parse_args()
    setup_logging(args.debug)

    base_url = base_url_for_host(args.host)
    if args.host and args.host != DEFAULT_HOST:
        terminal.console.print(f"[dim]Using ordering host: {base_url}[/dim]")

    terminal.show_welcome()

    try:
        share_token, order_number, download_folder = terminal.prompt_inputs()
    except (KeyboardInterrupt, EOFError):
        terminal.console.print("\n[dim]Cancelled.[/dim]")
        return 0

    # Create download folder
    download_folder.mkdir(parents=True, exist_ok=True)

    # Initialize state manager
    state_mgr = StateManager(download_folder, order_number)

    # Check for existing state (resume)
    if state_mgr.manifest_path.exists():
        terminal.show_resuming()
        with DartApiClient(share_token, base_url=base_url) as api:
            order = api.get_order(order_number)
        state_mgr.load_or_create(order)
    else:
        terminal.console.print("[dim]Fetching order metadata...[/dim]")
        try:
            with DartApiClient(share_token, base_url=base_url) as api:
                order = api.get_order(order_number)
        except Exception as e:
            terminal.show_error(f"Failed to fetch order: {e}")
            return 1

        state_mgr.load_or_create(order)

    manifest = state_mgr.manifest
    pending = state_mgr.get_pending_files()
    total_files = len(pending)
    total_bytes = sum(f.expected_size for f in pending)

    terminal.show_order_summary(order_number, total_files, total_bytes)

    # Warn up front if the order has no MD5SUMS file, so the user can abort
    # (Ctrl-C / answer no) before any data is downloaded.
    order_has_md5sums = any(f.filename == MD5SUMS_FILENAME for f in order.files)
    if total_files > 0 and not order_has_md5sums:
        if not terminal.confirm_no_md5sums():
            terminal.console.print("[dim]Aborted before downloading.[/dim]")
            return 0

    md5_available = False
    if total_files == 0:
        terminal.console.print("[green]Nothing to download. All files complete.[/green]")
    else:
        engine = DownloadEngine(
            share_token=share_token,
            download_folder=download_folder,
            state_manager=state_mgr,
            concurrency=args.concurrency,
        )
        terminal.run_with_progress(engine, total_files, total_bytes)
        md5_available = engine.md5_available

    # Show validation status
    terminal.show_validating()
    completed = state_mgr.get_completed_files()
    valid_count = sum(1 for f in completed if f.validated)
    terminal.show_validation_complete(valid_count, len(completed))

    # Content-integrity (MD5) status
    completed_md5_verified = sum(
        1 for f in completed if f.md5_status and f.md5_status.value == "verified"
    )
    # On a resumed/already-complete run no engine ran this time, so infer whether
    # MD5SUMS was available from whether any file was previously verified.
    if not md5_available and completed_md5_verified > 0:
        md5_available = True

    if md5_available:
        terminal.show_md5_verified(completed_md5_verified, len(completed))
    else:
        terminal.show_md5_unavailable_warning()

    # Finalize
    state_mgr.mark_complete_time()

    # Generate reports
    reporter = ReportGenerator(state_mgr, download_folder)
    reporter.save()
    report = reporter.generate()

    terminal.show_report(report, download_folder)

    # Non-zero exit if any files failed
    return 1 if report["files_failed"] > 0 else 0


if __name__ == "__main__":
    main()
