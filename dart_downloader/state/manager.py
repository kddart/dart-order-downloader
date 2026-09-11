from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

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
        # The download engine mutates state from multiple worker threads
        # (mark_completed/mark_failed/increment_retry, each of which also
        # rewrites the manifest). A reentrant lock serializes those mutations
        # and their writes so the on-disk manifest is never a half-written mix
        # of two updates. RLock (not Lock) so a locked method can call save().
        self._lock = RLock()

    @property
    def manifest(self) -> DownloadManifest:
        if self._manifest is None:
            raise RuntimeError("Manifest not initialized. Call load_or_create() first.")
        return self._manifest

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path

    def load_or_create(self, order: OrderData) -> DownloadManifest:
        with self._lock:
            return self._load_or_create_locked(order)

    def _load_or_create_locked(self, order: OrderData) -> DownloadManifest:
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
                    incloud=f.incloud,
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
        """Persist the manifest atomically.

        Writes to a temporary file in the same directory, flushes and fsyncs it,
        then ``os.replace()``s it over the real manifest. ``os.replace`` is
        atomic on POSIX and Windows, so a crash (or power loss) can never leave
        a half-written manifest: an interrupted run either sees the previous
        complete manifest or the new complete one, never a corrupt mix. This
        protects resume state — a torn write here would otherwise force a full
        re-download of a large order.
        """
        with self._lock:
            data = self._manifest.model_dump_json(indent=2)
            tmp_path = self._manifest_path.with_name(
                self._manifest_path.name + f".tmp.{os.getpid()}"
            )
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._manifest_path)
            finally:
                # If os.replace succeeded the temp file is gone; this only
                # cleans up a leftover temp from a failed write.
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

    def mark_completed(
        self,
        filename: str,
        actual_size: int,
        validated: bool = True,
        md5_status: "Md5Status | None" = None,
    ):
        with self._lock:
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
        with self._lock:
            for f in self.manifest.files:
                if f.filename == filename:
                    f.status = FileStatus.FAILED
                    f.error_message = error
                    break
            self.save()

    def increment_retry(self, filename: str):
        with self._lock:
            for f in self.manifest.files:
                if f.filename == filename:
                    f.retry_count += 1
                    break
            self.save()

    def mark_complete_time(self):
        with self._lock:
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
