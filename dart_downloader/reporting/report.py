from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dart_downloader.formatting import format_bytes as _format_bytes
from dart_downloader.formatting import format_duration as _format_duration
from dart_downloader.models.models import FileStatus
from dart_downloader.state.manager import StateManager


class ReportGenerator:
    def __init__(self, state_manager: StateManager, download_folder: Path):
        self._state = state_manager
        self._folder = download_folder

    def generate(self) -> dict:
        manifest = self._state.manifest
        completed = self._state.get_completed_files()
        failed = self._state.get_failed_files()

        total_downloaded = sum(f.actual_size or 0 for f in completed)

        started = datetime.fromisoformat(manifest.started_at)
        ended = (
            datetime.fromisoformat(manifest.completed_at)
            if manifest.completed_at
            else datetime.now(timezone.utc)
        )
        duration_seconds = (ended - started).total_seconds()
        throughput = total_downloaded / duration_seconds if duration_seconds > 0 else 0

        md5_verified = sum(
            1 for f in completed if f.md5_status and f.md5_status.value == "verified"
        )
        # MD5SUMS was available for the order if at least one file was verified.
        md5sums_available = md5_verified > 0

        report = {
            "order_number": manifest.order_number,
            "download_folder": manifest.download_folder,
            "files_downloaded": len(completed),
            "files_md5_verified": md5_verified,
            "md5sums_available": md5sums_available,
            "files_failed": len(failed),
            "total_data_downloaded_bytes": total_downloaded,
            "total_data_downloaded": _format_bytes(total_downloaded),
            "total_duration_seconds": duration_seconds,
            "total_duration": _format_duration(duration_seconds),
            "average_throughput": _format_bytes(int(throughput)) + "/s",
            "downloaded_files": [
                {
                    "filename": f.filename,
                    "filetype": f.filetype,
                    "expected_size": f.expected_size,
                    "actual_size": f.actual_size,
                    "size_match": f.expected_size == f.actual_size,
                    "validated": f.validated,
                    "md5_status": f.md5_status.value if f.md5_status else "na",
                }
                for f in completed
            ],
            "failed_files": [
                {"filename": f.filename, "filetype": f.filetype, "error": f.error_message or "Unknown"}
                for f in failed
            ],
        }
        return report

    def save(self):
        report = self.generate()

        json_path = self._folder / "download_report.json"
        json_path.write_text(json.dumps(report, indent=2))

        txt_path = self._folder / "download_report.txt"
        txt_path.write_text(self._format_text(report))

    def _format_text(self, report: dict) -> str:
        lines = [
            "=" * 60,
            "DArT Download Report",
            "=" * 60,
            "",
            f"Order Number:    {report['order_number']}",
            f"Download Folder: {report['download_folder']}",
            "",
            "-" * 40,
            "Files Downloaded Successfully",
            "-" * 40,
        ]
        for entry in report["downloaded_files"]:
            status = "VALID" if entry["validated"] else "NOT VALIDATED"
            md5 = entry.get("md5_status", "na")
            md5_label = {
                "verified": "MD5 OK",
                "mismatch": "MD5 MISMATCH",
                "na": "MD5 n/a",
            }.get(md5, "MD5 n/a")
            lines.append(
                f"  {entry['filename']}"
                f"  [{entry['filetype']}]"
                f"  {entry['expected_size']} bytes"
                f"  ({status}, {md5_label})"
            )
        if not report["downloaded_files"]:
            lines.append("  (none)")

        lines.extend([
            "",
            "-" * 40,
            "Files Failed",
            "-" * 40,
        ])
        for entry in report["failed_files"]:
            lines.append(f"  {entry['filename']} — {entry['error']}")
        if not report["failed_files"]:
            lines.append("  (none)")

        lines.extend([
            "",
            "-" * 40,
            "Summary",
            "-" * 40,
            f"  Total Data Downloaded: {report['total_data_downloaded']}",
            (
                f"  MD5 Verified:          {report['files_md5_verified']}/{report['files_downloaded']}"
                if report["md5sums_available"]
                else "  MD5 Verified:          NOT AVAILABLE - no MD5SUMS file; contents verified by size only"
            ),
            f"  Total Duration:        {report['total_duration']}",
            f"  Average Throughput:     {report['average_throughput']}",
            "",
            "=" * 60,
        ])
        return "\n".join(lines)
