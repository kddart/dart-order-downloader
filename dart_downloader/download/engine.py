from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import httpx

from dart_downloader.models.models import DownloadProgress, FileState, FileStatus, Md5Status
from dart_downloader.state.manager import StateManager
from dart_downloader.validation.providers import (
    MD5SUMS_FILENAME,
    Md5ValidationProvider,
    SizeValidationProvider,
    ValidationProvider,
    parse_md5sums,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1MB
MAX_RETRIES = 3
MD5_MAX_RETRIES = 2  # extra re-downloads allowed when MD5 content check fails
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 300.0
BACKOFF_BASE = 2.0


class DownloadEngine:
    def __init__(
        self,
        share_token: str,
        download_folder: Path,
        state_manager: StateManager,
        concurrency: int = 8,
        validator: ValidationProvider | None = None,
        progress_callback=None,
    ):
        self._share_token = share_token
        self._download_folder = download_folder
        self._state_manager = state_manager
        self._concurrency = concurrency
        self._validator = validator or SizeValidationProvider()
        self._md5_validator = Md5ValidationProvider()
        self._md5_map: dict[str, str] = {}
        self._md5_available = False
        self._progress_callback = progress_callback
        self._lock = Lock()
        self._progress: dict[str, DownloadProgress] = {}

    @property
    def progress(self) -> dict[str, DownloadProgress]:
        return self._progress

    @property
    def md5_available(self) -> bool:
        """True if an MD5SUMS file was found and parsed for this order."""
        return self._md5_available

    def _get_filepath(self, file_state: FileState) -> Path:
        subdir = self._download_folder / file_state.filetype
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / file_state.filename

    def _recheck_completed_file(self, f: FileState, filepath: Path) -> bool:
        """Re-verify an already-completed file on resume.

        Returns True if the file is still valid (keep it completed), or False if
        it must be re-downloaded (its state is reset and the file removed).

        Size is always re-checked. MD5 is re-checked only for files that were
        not already verified — already-verified files are trusted and not
        re-hashed, keeping resume cheap. If MD5SUMS is now available and a
        previously unverified file matches, it is upgraded to VERIFIED.
        """
        def _requeue():
            f.status = FileStatus.PENDING
            f.actual_size = None
            f.validated = False
            f.md5_status = None
            if filepath.exists():
                filepath.unlink()
            return False

        # 1) Size must still match.
        if not self._validator.validate(filepath, f.expected_size).valid:
            return _requeue()

        # 2) Trust files already verified in a previous run (don't re-hash).
        if f.md5_status == Md5Status.VERIFIED:
            return True

        # 3) Otherwise re-check MD5 if we now have a checksum for this file.
        expected_md5 = self._md5_map.get(f.filename)
        md5_result = self._md5_validator.validate(
            filepath, f.expected_size, expected_md5=expected_md5
        )
        if not md5_result.valid:
            # Content changed / was never good — re-download.
            return _requeue()

        # Valid: record whether it was checksum-verified or simply has no entry.
        f.md5_status = (
            Md5Status.VERIFIED if expected_md5 is not None else Md5Status.NOT_AVAILABLE
        )
        return True

    def run(self):
        pending = self._state_manager.get_pending_files()
        if not pending:
            return

        transport = httpx.HTTPTransport(retries=0)
        with httpx.Client(
            headers={"Authorization": f"Bearer {self._share_token}"},
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
        ) as client:
            # Flow B: fetch and parse MD5SUMS up front (if the order contains it)
            # so files can be MD5-verified — including already-completed files
            # being re-checked on resume, below.
            pending = self._prepare_md5sums(client, pending)

            # Re-verify already-completed files and re-queue any that fail.
            # Size is always re-checked; MD5 is re-checked for files that were
            # not previously verified (already-verified files are trusted and
            # not re-hashed, to keep resume cheap).
            for f in self._state_manager.get_completed_files():
                filepath = self._get_filepath(f)
                if not self._recheck_completed_file(f, filepath):
                    pending.append(f)
            self._state_manager.save()

            # Initialize progress tracking
            for f in pending:
                self._progress[f.filename] = DownloadProgress(
                    filename=f.filename, total_bytes=f.expected_size
                )

            with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
                futures = {
                    executor.submit(self._download_file, client, f): f
                    for f in pending
                }
                for future in as_completed(futures):
                    file_state = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Unhandled error downloading {file_state.filename}: {e}")
                        self._state_manager.mark_failed(file_state.filename, str(e))

    def _prepare_md5sums(
        self, client: httpx.Client, pending: list[FileState]
    ) -> list[FileState]:
        """Download and parse the MD5SUMS file first, if present.

        Returns the pending list with the MD5SUMS entry removed (it has been
        handled here). When no MD5SUMS file is in the order, the digest map
        stays empty and validation silently falls back to size-only.
        """
        md5_state = next(
            (
                f
                for f in self._state_manager.manifest.files
                if f.filename == MD5SUMS_FILENAME
            ),
            None,
        )
        if md5_state is None:
            return pending

        remaining = [f for f in pending if f.filename != MD5SUMS_FILENAME]
        filepath = self._get_filepath(md5_state)

        # On resume MD5SUMS may already be on disk from a previous run; parse it
        # directly so the digest map is available for re-checking completed
        # files, without re-downloading.
        if md5_state not in pending and filepath.exists():
            try:
                self._md5_map = parse_md5sums(filepath.read_text())
                self._md5_available = True
                logger.info(
                    "Loaded %d checksums from existing MD5SUMS", len(self._md5_map)
                )
                return remaining
            except OSError as e:
                logger.warning(
                    "Could not read existing MD5SUMS (%s); re-downloading it.", e
                )

        # Track MD5SUMS in the progress map so its download is reflected and the
        # completed/failed updates below have an entry to set.
        with self._lock:
            self._progress.setdefault(
                md5_state.filename,
                DownloadProgress(
                    filename=md5_state.filename,
                    total_bytes=md5_state.expected_size,
                ),
            )

        try:
            self._stream_to_disk(
                client, md5_state.fileurl, filepath, md5_state.filename
            )
            self._md5_map = parse_md5sums(filepath.read_text())
            self._md5_available = True
            actual_size = filepath.stat().st_size
            # MD5SUMS itself is validated by size only.
            self._state_manager.mark_completed(
                md5_state.filename,
                actual_size,
                validated=True,
                md5_status=Md5Status.NOT_AVAILABLE,
            )
            if md5_state.filename in self._progress:
                with self._lock:
                    self._progress[md5_state.filename].completed = True
            if self._progress_callback:
                self._progress_callback(md5_state.filename, "completed")
            logger.info("Loaded %d checksums from MD5SUMS", len(self._md5_map))
        except (httpx.HTTPError, OSError) as e:
            # If MD5SUMS can't be fetched/read, fall back to size-only validation.
            logger.warning("Could not fetch/parse MD5SUMS: %s. Falling back to size validation.", e)
            self._md5_map = {}
            self._md5_available = False
            if filepath.exists():
                filepath.unlink()
            self._state_manager.mark_failed(md5_state.filename, str(e))
            if md5_state.filename in self._progress:
                with self._lock:
                    self._progress[md5_state.filename].failed = True
            if self._progress_callback:
                self._progress_callback(md5_state.filename, "failed")

        return remaining

    def _download_file(self, client: httpx.Client, file_state: FileState):
        filepath = self._get_filepath(file_state)

        md5_attempts = 0  # number of re-downloads triggered by MD5 mismatch

        for attempt in range(MAX_RETRIES):
            try:
                self._state_manager.increment_retry(file_state.filename)
                self._stream_to_disk(client, file_state.fileurl, filepath, file_state.filename)

                result = self._validator.validate(filepath, file_state.expected_size)
                if result.valid:
                    # Size is good; now verify content against MD5SUMS (if available).
                    expected_md5 = self._md5_map.get(file_state.filename)
                    md5_result = self._md5_validator.validate(
                        filepath, file_state.expected_size, expected_md5=expected_md5
                    )
                    if md5_result.valid:
                        md5_status = (
                            Md5Status.VERIFIED
                            if expected_md5 is not None
                            else Md5Status.NOT_AVAILABLE
                        )
                        actual_size = filepath.stat().st_size
                        self._state_manager.mark_completed(
                            file_state.filename,
                            actual_size,
                            validated=True,
                            md5_status=md5_status,
                        )
                        with self._lock:
                            self._progress[file_state.filename].completed = True
                        if self._progress_callback:
                            self._progress_callback(file_state.filename, "completed")
                        return

                    # MD5 mismatch: re-download up to MD5_MAX_RETRIES times.
                    if filepath.exists():
                        filepath.unlink()
                    if md5_attempts < MD5_MAX_RETRIES:
                        md5_attempts += 1
                        wait = BACKOFF_BASE ** md5_attempts
                        logger.warning(
                            f"MD5 check failed for {file_state.filename}: {md5_result.message}. "
                            f"Re-downloading ({md5_attempts}/{MD5_MAX_RETRIES}) in {wait}s..."
                        )
                        time.sleep(wait)
                        continue

                    # MD5 retries exhausted — report the mismatch.
                    self._state_manager.mark_completed(
                        file_state.filename,
                        file_state.expected_size,
                        validated=False,
                        md5_status=Md5Status.MISMATCH,
                    )
                    self._state_manager.mark_failed(
                        file_state.filename, md5_result.message
                    )
                    with self._lock:
                        self._progress[file_state.filename].failed = True
                    if self._progress_callback:
                        self._progress_callback(file_state.filename, "failed")
                    return
                else:
                    if filepath.exists():
                        filepath.unlink()
                    if attempt < MAX_RETRIES - 1:
                        wait = BACKOFF_BASE ** attempt
                        logger.warning(
                            f"Validation failed for {file_state.filename}: {result.message}. "
                            f"Retrying in {wait}s..."
                        )
                        time.sleep(wait)

            except (httpx.HTTPError, OSError) as e:
                if filepath.exists():
                    filepath.unlink()
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE ** attempt
                    logger.warning(
                        f"Error downloading {file_state.filename}: {e}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    self._state_manager.mark_failed(file_state.filename, str(e))
                    with self._lock:
                        self._progress[file_state.filename].failed = True
                    if self._progress_callback:
                        self._progress_callback(file_state.filename, "failed")
                    return

        # All retries exhausted via validation failure path
        self._state_manager.mark_failed(
            file_state.filename, f"Failed validation after {MAX_RETRIES} attempts"
        )
        with self._lock:
            self._progress[file_state.filename].failed = True
        if self._progress_callback:
            self._progress_callback(file_state.filename, "failed")

    def _stream_to_disk(
        self, client: httpx.Client, url: str, filepath: Path, filename: str
    ):
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
                    with self._lock:
                        progress = self._progress.get(filename)
                        if progress is not None:
                            progress.bytes_downloaded += len(chunk)
                    if self._progress_callback:
                        self._progress_callback(filename, "progress")
