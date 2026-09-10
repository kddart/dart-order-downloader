from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dart_downloader.models.models import (
    DownloadManifest,
    FileState,
    FileStatus,
    Md5Status,
    OrderData,
)

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "download_manifest.json"


class StateManager:
    def __init__(self, download_folder: Path, order_number: str):
        self._download_folder = download_folder
        self._order_number = order_number
        self._manifest_path = download_folder / MANIFEST_FILENAME
        self._manifest: DownloadManifest | None = None

    @property
    def manifest(self) -> DownloadManifest:
        if self._manifest is None:
            raise RuntimeError("Manifest not initialized. Call load_or_create() first.")
        return self._manifest

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path

    def load_or_create(self, order: OrderData) -> DownloadManifest:
        if self._manifest_path.exists():
            try:
                self._manifest = DownloadManifest.model_validate_json(
                    self._manifest_path.read_text()
                )
                return self._manifest
            except Exception as e:
                # Existing manifest couldn't be parsed (corrupt, or written by an
                # incompatible older version). Log it — so a genuine bug isn't
                # invisible — then rebuild from the fresh order metadata.
                logger.warning(
                    "Could not read existing manifest at %s (%s); rebuilding it.",
                    self._manifest_path,
                    e,
                )
                self._manifest_path.unlink()

        files: list[FileState] = []

        for f in order.files:
            files.append(
                FileState(
                    filename=f.filename,
                    fileurl=f.fileurl,
                    filetype=f.filetype,
                    expected_size=f.filesizeinbyte,
                )
            )

        self._manifest = DownloadManifest(
            order_number=self._order_number,
            download_folder=str(self._download_folder),
            started_at=datetime.now(timezone.utc).isoformat(),
            files=files,
        )
        self.save()
        return self._manifest

    def save(self):
        self._manifest_path.write_text(
            self._manifest.model_dump_json(indent=2)
        )

    def mark_completed(
        self,
        filename: str,
        actual_size: int,
        validated: bool = True,
        md5_status: "Md5Status | None" = None,
    ):
        for f in self.manifest.files:
            if f.filename == filename:
                f.status = FileStatus.COMPLETED
                f.actual_size = actual_size
                f.validated = validated
                if md5_status is not None:
                    f.md5_status = md5_status
                break
        self.save()

    def mark_failed(self, filename: str, error: str):
        for f in self.manifest.files:
            if f.filename == filename:
                f.status = FileStatus.FAILED
                f.error_message = error
                break
        self.save()

    def increment_retry(self, filename: str):
        for f in self.manifest.files:
            if f.filename == filename:
                f.retry_count += 1
                break
        self.save()

    def mark_complete_time(self):
        self.manifest.completed_at = datetime.now(timezone.utc).isoformat()
        self.save()

    def get_pending_files(self) -> list[FileState]:
        return [
            f for f in self.manifest.files
            if f.status in (FileStatus.PENDING, FileStatus.DOWNLOADING)
        ]

    def get_completed_files(self) -> list[FileState]:
        return [f for f in self.manifest.files if f.status == FileStatus.COMPLETED]

    def get_failed_files(self) -> list[FileState]:
        return [f for f in self.manifest.files if f.status == FileStatus.FAILED]
