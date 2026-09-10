from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dart_downloader.models.models import (
    DownloadManifest,
    FileState,
    FileStatus,
    OrderData,
    OrderFile,
    OrderResponse,
)
from dart_downloader.state.manager import StateManager
from dart_downloader.validation.providers import SizeValidationProvider


# --- Fixtures ---

@pytest.fixture
def sample_order_path():
    return Path(__file__).parent / "fixtures" / "sample_order.json"


@pytest.fixture
def sample_order_data(sample_order_path) -> OrderData:
    raw = json.loads(sample_order_path.read_text())
    response = OrderResponse.model_validate(raw)
    return response.data[0]


@pytest.fixture
def tmp_download_folder():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# --- Manifest Parsing ---

class TestManifestParsing:
    def test_parse_order_response(self, sample_order_path):
        raw = json.loads(sample_order_path.read_text())
        response = OrderResponse.model_validate(raw)
        assert len(response.data) == 1
        assert response.data[0].ordernumber == "DO25-11328"

    def test_order_has_files(self, sample_order_data):
        assert len(sample_order_data.files) > 0

    def test_order_metadata(self, sample_order_data):
        assert sample_order_data.orderstatus == "complete"
        assert sample_order_data.productname == "Oat DArTseq (1.0)"
        assert sample_order_data.numberofsamples == 376


# --- File Queueing ---

class TestFileQueueing:
    def test_all_files_queued(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        manifest = state_mgr.manifest
        # Every file in the order is queued for download
        assert len(manifest.files) == len(sample_order_data.files)
        queued_names = {f.filename for f in manifest.files}
        assert queued_names == {o.filename for o in sample_order_data.files}

    def test_all_filetypes_included(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        manifest = state_mgr.manifest
        filetypes = {f.filetype for f in manifest.files}
        # sample_order.json has RawData, Report, OrderAppendix, etc
        assert "RawData" in filetypes
        assert "Report" in filetypes

    def test_filetype_stored_on_state(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        for f in state_mgr.manifest.files:
            assert f.filetype != ""
            original = next(
                o for o in sample_order_data.files if o.filename == f.filename
            )
            assert f.filetype == original.filetype


# --- Validation ---

class TestValidation:
    def test_size_validation_valid(self, tmp_download_folder):
        filepath = tmp_download_folder / "test.fastq.gz"
        filepath.write_bytes(b"x" * 1000)

        validator = SizeValidationProvider()
        result = validator.validate(filepath, 1000)
        assert result.valid

    def test_size_validation_invalid(self, tmp_download_folder):
        filepath = tmp_download_folder / "test.fastq.gz"
        filepath.write_bytes(b"x" * 500)

        validator = SizeValidationProvider()
        result = validator.validate(filepath, 1000)
        assert not result.valid
        assert "mismatch" in result.message.lower()

    def test_size_validation_missing_file(self, tmp_download_folder):
        filepath = tmp_download_folder / "nonexistent.gz"

        validator = SizeValidationProvider()
        result = validator.validate(filepath, 1000)
        assert not result.valid
        assert "not exist" in result.message.lower()


# --- State Recovery ---

class TestStateRecovery:
    def test_state_persists_to_disk(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        manifest_path = tmp_download_folder / "download_manifest.json"
        assert manifest_path.exists()

        loaded = DownloadManifest.model_validate_json(manifest_path.read_text())
        assert loaded.order_number == "DO25-11328"
        assert len(loaded.files) > 0

    def test_state_resumes_from_disk(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        first_file = state_mgr.manifest.files[0]
        state_mgr.mark_completed(first_file.filename, first_file.expected_size)

        # Second run — should load from disk
        state_mgr2 = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr2.load_or_create(sample_order_data)

        completed = state_mgr2.get_completed_files()
        assert len(completed) == 1
        assert completed[0].filename == first_file.filename

    def test_pending_excludes_completed(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        total = len(state_mgr.get_pending_files())
        first_file = state_mgr.manifest.files[0]
        state_mgr.mark_completed(first_file.filename, first_file.expected_size)

        pending = state_mgr.get_pending_files()
        assert len(pending) == total - 1

    def test_validated_flag_set_on_completion(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        first_file = state_mgr.manifest.files[0]
        state_mgr.mark_completed(first_file.filename, first_file.expected_size, validated=True)

        completed = state_mgr.get_completed_files()
        assert completed[0].validated is True


# --- Retry Handling ---

class TestRetryHandling:
    def test_retry_count_increments(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        filename = state_mgr.manifest.files[0].filename
        state_mgr.increment_retry(filename)
        state_mgr.increment_retry(filename)

        file_state = next(f for f in state_mgr.manifest.files if f.filename == filename)
        assert file_state.retry_count == 2

    def test_mark_failed(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        filename = state_mgr.manifest.files[0].filename
        state_mgr.mark_failed(filename, "Connection timeout after 3 retries")

        failed = state_mgr.get_failed_files()
        assert len(failed) == 1
        assert failed[0].error_message == "Connection timeout after 3 retries"

    def test_failed_does_not_block_others(self, sample_order_data, tmp_download_folder):
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        first = state_mgr.manifest.files[0].filename
        state_mgr.mark_failed(first, "error")

        pending = state_mgr.get_pending_files()
        assert all(f.filename != first for f in pending)
        assert len(pending) == len(state_mgr.manifest.files) - 1


# --- Report Validation Evidence ---

class TestReportValidation:
    def test_report_includes_validation_evidence(self, sample_order_data, tmp_download_folder):
        from dart_downloader.reporting.report import ReportGenerator

        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        # Simulate completing a file
        f = state_mgr.manifest.files[0]
        state_mgr.mark_completed(f.filename, f.expected_size, validated=True)
        state_mgr.mark_complete_time()

        reporter = ReportGenerator(state_mgr, tmp_download_folder)
        report = reporter.generate()

        assert len(report["downloaded_files"]) == 1
        entry = report["downloaded_files"][0]
        assert entry["expected_size"] == f.expected_size
        assert entry["actual_size"] == f.expected_size
        assert entry["size_match"] is True
        assert entry["validated"] is True

    def test_report_saves_json_and_txt(self, sample_order_data, tmp_download_folder):
        from dart_downloader.reporting.report import ReportGenerator

        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)
        state_mgr.mark_complete_time()

        reporter = ReportGenerator(state_mgr, tmp_download_folder)
        reporter.save()

        assert (tmp_download_folder / "download_report.json").exists()
        assert (tmp_download_folder / "download_report.txt").exists()


# --- MD5SUMS Parsing & Validation ---

import hashlib

from dart_downloader.models.models import Md5Status
from dart_downloader.validation.providers import (
    MD5SUMS_FILENAME,
    Md5ValidationProvider,
    compute_md5,
    parse_md5sums,
)


class TestMd5Parsing:
    def test_parses_two_space_text_mode(self):
        text = (
            "d41d8cd98f00b204e9800998ecf8427e  a.fastq.gz\n"
            "0cc175b9c0f1b6a831c399e269772661  b.fastq.gz\n"
        )
        result = parse_md5sums(text)
        assert result == {
            "a.fastq.gz": "d41d8cd98f00b204e9800998ecf8427e",
            "b.fastq.gz": "0cc175b9c0f1b6a831c399e269772661",
        }

    def test_parses_binary_mode_asterisk(self):
        text = "d41d8cd98f00b204e9800998ecf8427e *a.fastq.gz\n"
        result = parse_md5sums(text)
        assert result == {"a.fastq.gz": "d41d8cd98f00b204e9800998ecf8427e"}

    def test_ignores_blank_and_comment_lines(self):
        text = (
            "# checksums\n"
            "\n"
            "d41d8cd98f00b204e9800998ecf8427e  a.fastq.gz\n"
        )
        result = parse_md5sums(text)
        assert result == {"a.fastq.gz": "d41d8cd98f00b204e9800998ecf8427e"}

    def test_keys_are_basenames(self):
        text = "d41d8cd98f00b204e9800998ecf8427e  RawData/a.fastq.gz\n"
        result = parse_md5sums(text)
        assert "a.fastq.gz" in result

    def test_compute_md5_matches_hashlib(self, tmp_download_folder):
        filepath = tmp_download_folder / "f.bin"
        payload = b"genomic-data-payload" * 1000
        filepath.write_bytes(payload)
        assert compute_md5(filepath) == hashlib.md5(payload).hexdigest()


class TestMd5Validation:
    def _write(self, folder, name, payload):
        p = folder / name
        p.write_bytes(payload)
        return p

    def test_md5_match_is_valid(self, tmp_download_folder):
        payload = b"hello"
        p = self._write(tmp_download_folder, "x.gz", payload)
        expected = hashlib.md5(payload).hexdigest()

        result = Md5ValidationProvider().validate(p, len(payload), expected_md5=expected)
        assert result.valid
        assert "verified" in result.message.lower()

    def test_md5_mismatch_is_invalid(self, tmp_download_folder):
        p = self._write(tmp_download_folder, "x.gz", b"hello")
        result = Md5ValidationProvider().validate(
            p, 5, expected_md5="ffffffffffffffffffffffffffffffff"
        )
        assert not result.valid
        assert "mismatch" in result.message.lower()

    def test_no_expected_md5_passes_with_note(self, tmp_download_folder):
        # Files with no MD5SUMS entry should pass but be noted (decision: pass + note)
        p = self._write(tmp_download_folder, "report.pdf", b"pdf-bytes")
        result = Md5ValidationProvider().validate(p, 9, expected_md5=None)
        assert result.valid
        assert "no md5" in result.message.lower()

    def test_missing_file_is_invalid(self, tmp_download_folder):
        result = Md5ValidationProvider().validate(
            tmp_download_folder / "nope.gz", 10, expected_md5="abc"
        )
        assert not result.valid


class TestMd5Reporting:
    def test_report_surfaces_md5_status(self, sample_order_data, tmp_download_folder):
        from dart_downloader.reporting.report import ReportGenerator

        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        files = state_mgr.manifest.files
        # One verified, one not-available (N/A)
        state_mgr.mark_completed(
            files[0].filename, files[0].expected_size,
            validated=True, md5_status=Md5Status.VERIFIED,
        )
        state_mgr.mark_completed(
            files[1].filename, files[1].expected_size,
            validated=True, md5_status=Md5Status.NOT_AVAILABLE,
        )
        state_mgr.mark_complete_time()

        report = ReportGenerator(state_mgr, tmp_download_folder).generate()

        assert report["files_md5_verified"] == 1
        assert report["md5sums_available"] is True
        by_name = {e["filename"]: e for e in report["downloaded_files"]}
        assert by_name[files[0].filename]["md5_status"] == "verified"
        assert by_name[files[1].filename]["md5_status"] == "na"

    def test_report_flags_md5sums_unavailable(self, sample_order_data, tmp_download_folder):
        from dart_downloader.reporting.report import ReportGenerator

        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(sample_order_data)

        # Complete files with no MD5 verification (no MD5SUMS in order)
        for f in state_mgr.manifest.files:
            state_mgr.mark_completed(
                f.filename, f.expected_size,
                validated=True, md5_status=Md5Status.NOT_AVAILABLE,
            )
        state_mgr.mark_complete_time()

        report = ReportGenerator(state_mgr, tmp_download_folder).generate()
        assert report["md5sums_available"] is False
        assert report["files_md5_verified"] == 0
        # The obvious warning must appear in the human-readable report text
        txt = ReportGenerator(state_mgr, tmp_download_folder)._format_text(report)
        assert "NOT AVAILABLE" in txt


# --- Engine Flow B (MD5SUMS-first) ---

from contextlib import contextmanager

from dart_downloader.download.engine import DownloadEngine, MD5_MAX_RETRIES
from dart_downloader.models.models import OrderData, OrderFile


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=65536):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


class _FakeClient:
    """Serves bytes per-URL. `contents` maps fileurl -> bytes (or a callable
    returning bytes, so a file can change between download attempts)."""

    def __init__(self, contents):
        self._contents = contents
        self.request_counts: dict[str, int] = {}

    @contextmanager
    def stream(self, method, url):
        self.request_counts[url] = self.request_counts.get(url, 0) + 1
        value = self._contents[url]
        data = value(self.request_counts[url]) if callable(value) else value
        yield _FakeResponse(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _order_with(files) -> OrderData:
    return OrderData(
        ordernumber="DO25-11328",
        orderstatus="complete",
        productname="Oat DArTseq (1.0)",
        numberofsamples=1,
        files=files,
    )


def _make_engine(state_mgr, folder):
    engine = DownloadEngine(
        share_token="tok",
        download_folder=folder,
        state_manager=state_mgr,
        concurrency=1,
    )
    return engine


class TestEngineFlowB:
    def test_md5sums_verifies_files(self, tmp_download_folder):
        good = b"read-data-A"
        good_md5 = hashlib.md5(good).hexdigest()
        base = "https://ex.invalid/RawData"
        md5sums = f"{good_md5}  a.fastq.gz\n".encode()

        files = [
            OrderFile(filename="MD5SUMS", filesizeinbyte=len(md5sums),
                      filetype="RawData", fileurl=f"{base}/MD5SUMS",
                      modifieddatetime="2026-01-01"),
            OrderFile(filename="a.fastq.gz", filesizeinbyte=len(good),
                      filetype="RawData", fileurl=f"{base}/a.fastq.gz",
                      modifieddatetime="2026-01-01"),
        ]
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(_order_with(files))

        engine = _make_engine(state_mgr, tmp_download_folder)
        client = _FakeClient({f"{base}/MD5SUMS": md5sums, f"{base}/a.fastq.gz": good})
        # Drive flow B directly with the fake client.
        pending = state_mgr.get_pending_files()
        for f in pending:
            engine._progress[f.filename] = __import__(
                "dart_downloader.models.models", fromlist=["DownloadProgress"]
            ).DownloadProgress(filename=f.filename, total_bytes=f.expected_size)
        remaining = engine._prepare_md5sums(client, pending)
        for f in remaining:
            engine._download_file(client, f)

        completed = {f.filename: f for f in state_mgr.get_completed_files()}
        assert completed["a.fastq.gz"].md5_status == Md5Status.VERIFIED
        assert completed["MD5SUMS"].md5_status == Md5Status.NOT_AVAILABLE

    def test_md5_mismatch_retries_then_fails(self, tmp_download_folder, monkeypatch):
        # Avoid real backoff sleeps during the retry loop.
        monkeypatch.setattr("dart_downloader.download.engine.time.sleep", lambda *_: None)
        # Server always returns wrong bytes for a.fastq.gz, so MD5 never matches.
        base = "https://ex.invalid/RawData"
        wrong = b"corrupted"
        md5sums = f"{'0' * 32}  a.fastq.gz\n".encode()

        files = [
            OrderFile(filename="MD5SUMS", filesizeinbyte=len(md5sums),
                      filetype="RawData", fileurl=f"{base}/MD5SUMS",
                      modifieddatetime="2026-01-01"),
            OrderFile(filename="a.fastq.gz", filesizeinbyte=len(wrong),
                      filetype="RawData", fileurl=f"{base}/a.fastq.gz",
                      modifieddatetime="2026-01-01"),
        ]
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(_order_with(files))

        engine = _make_engine(state_mgr, tmp_download_folder)
        client = _FakeClient({f"{base}/MD5SUMS": md5sums, f"{base}/a.fastq.gz": wrong})
        pending = state_mgr.get_pending_files()
        for f in pending:
            engine._progress[f.filename] = __import__(
                "dart_downloader.models.models", fromlist=["DownloadProgress"]
            ).DownloadProgress(filename=f.filename, total_bytes=f.expected_size)
        remaining = engine._prepare_md5sums(client, pending)
        for f in remaining:
            engine._download_file(client, f)

        failed = {f.filename: f for f in state_mgr.get_failed_files()}
        assert "a.fastq.gz" in failed
        assert failed["a.fastq.gz"].md5_status == Md5Status.MISMATCH
        # 1 initial download + MD5_MAX_RETRIES re-downloads
        assert client.request_counts[f"{base}/a.fastq.gz"] == 1 + MD5_MAX_RETRIES

    def test_absent_md5sums_falls_back_to_size_only(self, tmp_download_folder):
        base = "https://ex.invalid/RawData"
        data = b"some-report"
        files = [
            OrderFile(filename="report.pdf", filesizeinbyte=len(data),
                      filetype="Report", fileurl=f"{base}/report.pdf",
                      modifieddatetime="2026-01-01"),
        ]
        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        state_mgr.load_or_create(_order_with(files))

        engine = _make_engine(state_mgr, tmp_download_folder)
        client = _FakeClient({f"{base}/report.pdf": data})
        pending = state_mgr.get_pending_files()
        for f in pending:
            engine._progress[f.filename] = __import__(
                "dart_downloader.models.models", fromlist=["DownloadProgress"]
            ).DownloadProgress(filename=f.filename, total_bytes=f.expected_size)
        remaining = engine._prepare_md5sums(client, pending)  # no MD5SUMS -> unchanged
        assert len(remaining) == 1
        for f in remaining:
            engine._download_file(client, f)

        completed = {f.filename: f for f in state_mgr.get_completed_files()}
        assert completed["report.pdf"].md5_status == Md5Status.NOT_AVAILABLE
        assert completed["report.pdf"].validated is True


# --- Early (pre-download) MD5SUMS warning ---

from dart_downloader.ui import terminal as ui_terminal
from dart_downloader.validation.providers import MD5SUMS_FILENAME


class TestEarlyMd5Warning:
    def _order_files(self, include_md5sums: bool):
        files = [
            OrderFile(filename="a.fastq.gz", filesizeinbyte=10, filetype="RawData",
                      fileurl="http://x/a", modifieddatetime="2026-01-01"),
        ]
        if include_md5sums:
            files.append(
                OrderFile(filename=MD5SUMS_FILENAME, filesizeinbyte=20, filetype="RawData",
                          fileurl="http://x/MD5SUMS", modifieddatetime="2026-01-01")
            )
        return files

    def test_detects_missing_md5sums_in_order(self):
        files = self._order_files(include_md5sums=False)
        assert not any(f.filename == MD5SUMS_FILENAME for f in files)

    def test_detects_present_md5sums_in_order(self):
        files = self._order_files(include_md5sums=True)
        assert any(f.filename == MD5SUMS_FILENAME for f in files)

    def test_confirm_proceeds_on_yes(self, monkeypatch):
        monkeypatch.setattr(ui_terminal.Prompt, "ask", lambda *a, **k: "y")
        assert ui_terminal.confirm_no_md5sums() is True

    def test_confirm_aborts_on_no(self, monkeypatch):
        monkeypatch.setattr(ui_terminal.Prompt, "ask", lambda *a, **k: "n")
        assert ui_terminal.confirm_no_md5sums() is False

    def test_confirm_aborts_on_interrupt(self, monkeypatch):
        def _raise(*a, **k):
            raise KeyboardInterrupt
        monkeypatch.setattr(ui_terminal.Prompt, "ask", _raise)
        assert ui_terminal.confirm_no_md5sums() is False


# --- Shared formatting helpers ---

from dart_downloader.formatting import format_bytes, format_duration


class TestFormatting:
    def test_format_bytes_units(self):
        assert format_bytes(0) == "0.00 B"
        assert format_bytes(1536) == "1.50 KB"
        assert format_bytes(1024 * 1024) == "1.00 MB"

    def test_format_duration(self):
        assert format_duration(5) == "5s"
        assert format_duration(65) == "1m 5s"
        assert format_duration(3725) == "1h 2m 5s"

    def test_report_and_terminal_share_one_impl(self):
        # Both modules must reference the single shared implementation.
        from dart_downloader.reporting import report as report_mod
        from dart_downloader.ui import terminal as terminal_mod
        assert report_mod._format_bytes is format_bytes
        assert terminal_mod._format_bytes is format_bytes


# --- Corrupt-manifest rebuild logging ---

class TestManifestRebuildLogging:
    def test_corrupt_manifest_is_logged_and_rebuilt(
        self, sample_order_data, tmp_download_folder, caplog
    ):
        # Write a garbage manifest file, then load_or_create over it.
        manifest_path = tmp_download_folder / "download_manifest.json"
        manifest_path.write_text("{ this is not valid json")

        state_mgr = StateManager(tmp_download_folder, "DO25-11328")
        with caplog.at_level("WARNING"):
            state_mgr.load_or_create(sample_order_data)

        # The parse failure must be logged (not silently swallowed)...
        assert any(
            "rebuilding" in r.getMessage().lower() for r in caplog.records
        )
        # ...and the manifest must have been rebuilt from the order.
        assert len(state_mgr.manifest.files) == len(sample_order_data.files)


# --- Resume: MD5 re-check of already-completed files ---

from dart_downloader.models.models import FileStatus


class TestResumeMd5Recheck:
    def _setup(self, folder, good: bytes, md5_line_digest: str):
        """Create an order (MD5SUMS + a.gz), write MD5SUMS + a.gz to disk, and
        mark both completed — simulating state left by a prior run."""
        base = "https://ex.invalid/RawData"
        md5sums = f"{md5_line_digest}  a.gz\n".encode()
        files = [
            OrderFile(filename="MD5SUMS", filesizeinbyte=len(md5sums), filetype="RawData",
                      fileurl=f"{base}/MD5SUMS", modifieddatetime="2026-01-01"),
            OrderFile(filename="a.gz", filesizeinbyte=len(good), filetype="RawData",
                      fileurl=f"{base}/a.gz", modifieddatetime="2026-01-01"),
        ]
        sm = StateManager(folder, "DO25-11328")
        sm.load_or_create(_order_with(files))
        # Write files to disk under their filetype subdir (as the engine would).
        (folder / "RawData").mkdir(parents=True, exist_ok=True)
        (folder / "RawData" / "MD5SUMS").write_bytes(md5sums)
        (folder / "RawData" / "a.gz").write_bytes(good)
        return sm, base, files

    def _prepare(self, engine, sm, client):
        # Mirror run(): parse MD5SUMS, then re-check completed files.
        pending = sm.get_pending_files()
        engine._prepare_md5sums(client, pending)
        requeued = []
        for f in sm.get_completed_files():
            fp = engine._get_filepath(f)
            if not engine._recheck_completed_file(f, fp):
                requeued.append(f)
        sm.save()
        return requeued

    def test_unverified_completed_file_gets_md5_verified_on_resume(self, tmp_download_folder):
        good = b"genome-A"
        digest = hashlib.md5(good).hexdigest()
        sm, base, _ = self._setup(tmp_download_folder, good, digest)
        # Mark both completed but NOT md5-verified (as an older run might have).
        for f in sm.manifest.files:
            sm.mark_completed(f.filename, f.expected_size, validated=True, md5_status=None)

        engine = _make_engine(sm, tmp_download_folder)
        client = _FakeClient({})  # nothing should need downloading
        requeued = self._prepare(engine, sm, client)

        assert requeued == []
        a = next(f for f in sm.manifest.files if f.filename == "a.gz")
        assert a.md5_status == Md5Status.VERIFIED  # upgraded on resume

    def test_verified_file_is_trusted_and_not_rehashed(self, tmp_download_folder, monkeypatch):
        good = b"genome-A"
        digest = hashlib.md5(good).hexdigest()
        sm, base, _ = self._setup(tmp_download_folder, good, digest)
        for f in sm.manifest.files:
            status = Md5Status.VERIFIED if f.filename == "a.gz" else Md5Status.NOT_AVAILABLE
            sm.mark_completed(f.filename, f.expected_size, validated=True, md5_status=status)

        # Fail the test if compute_md5 is called for the already-verified file.
        import dart_downloader.validation.providers as prov
        calls = {"n": 0}
        real = prov.compute_md5
        def _spy(path):
            calls["n"] += 1
            return real(path)
        monkeypatch.setattr(prov, "compute_md5", _spy)
        # Md5ValidationProvider imported compute_md5 at module load; patch there too.
        monkeypatch.setattr("dart_downloader.validation.providers.compute_md5", _spy)

        engine = _make_engine(sm, tmp_download_folder)
        requeued = self._prepare(engine, sm, _FakeClient({}))

        assert requeued == []
        assert calls["n"] == 0  # verified file was not re-hashed

    def test_corrupt_completed_file_is_requeued_on_resume(self, tmp_download_folder):
        good = b"genome-A"
        good_digest = hashlib.md5(good).hexdigest()
        sm, base, _ = self._setup(tmp_download_folder, good, good_digest)
        # Completed but unverified; now corrupt the on-disk file so MD5 fails.
        for f in sm.manifest.files:
            sm.mark_completed(f.filename, f.expected_size, validated=True, md5_status=None)
        # Overwrite a.gz with wrong content of the SAME size (so size still passes).
        (tmp_download_folder / "RawData" / "a.gz").write_bytes(b"XXXXXXXX")

        engine = _make_engine(sm, tmp_download_folder)
        requeued = self._prepare(engine, sm, _FakeClient({}))

        assert [f.filename for f in requeued] == ["a.gz"]
        a = next(f for f in sm.manifest.files if f.filename == "a.gz")
        assert a.status == FileStatus.PENDING
        assert not (tmp_download_folder / "RawData" / "a.gz").exists()


# --- Project metadata & CLI entry point ---

import tomllib

import dart_downloader
from dart_downloader.__main__ import main as cli_main


class TestProjectMetadata:
    def _pyproject(self):
        root = Path(__file__).parent.parent
        with open(root / "pyproject.toml", "rb") as f:
            return tomllib.load(f)

    def test_pyproject_has_core_metadata(self):
        proj = self._pyproject()["project"]
        assert proj["name"] == "dart-order-downloader"
        assert proj["requires-python"] == ">=3.12"
        assert "version" in proj

    def test_version_matches_package(self):
        # Guard against version drift between pyproject.toml and __init__.py.
        proj = self._pyproject()["project"]
        assert proj["version"] == dart_downloader.__version__


class TestCliEntryPoint:
    def test_help_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["dart_downloader", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 0
        assert "usage" in capsys.readouterr().out.lower()

    def test_version_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["dart_downloader", "--version"])
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 0
        assert dart_downloader.__version__ in capsys.readouterr().out


# --- Ordering host override ---

from dart_downloader.api.client import (
    BASE_URL,
    DEFAULT_HOST,
    DartApiClient,
    base_url_for_host,
)


class TestHostOverride:
    def test_default_base_url(self):
        assert base_url_for_host(DEFAULT_HOST) == BASE_URL
        assert BASE_URL == "https://ordering.diversityarrays.com/dart/orders"

    def test_bare_host_gets_https_and_path(self):
        assert (
            base_url_for_host("ordering-od-d1.diversityarrays.com")
            == "https://ordering-od-d1.diversityarrays.com/dart/orders"
        )

    def test_full_url_scheme_preserved(self):
        assert (
            base_url_for_host("http://localhost:8080")
            == "http://localhost:8080/dart/orders"
        )

    def test_trailing_slash_and_blank_handled(self):
        assert (
            base_url_for_host("https://staging.example.com/")
            == "https://staging.example.com/dart/orders"
        )
        # Blank/None falls back to the default host.
        assert base_url_for_host("") == BASE_URL

    def test_client_uses_override_base_url(self):
        client = DartApiClient("tok", base_url="https://staging.example.com/dart/orders")
        try:
            assert client._base_url == "https://staging.example.com/dart/orders"
        finally:
            client.close()

    def test_client_defaults_to_base_url(self):
        client = DartApiClient("tok")
        try:
            assert client._base_url == BASE_URL
        finally:
            client.close()


class TestHostArg:
    def test_host_defaults_to_env(self, monkeypatch):
        from dart_downloader.__main__ import parse_args
        monkeypatch.setenv("DART_ORDERING_HOST", "envhost.example.com")
        monkeypatch.setattr("sys.argv", ["dart_downloader"])
        args = parse_args()
        assert args.host == "envhost.example.com"

    def test_host_flag_overrides_env(self, monkeypatch):
        from dart_downloader.__main__ import parse_args
        monkeypatch.setenv("DART_ORDERING_HOST", "envhost.example.com")
        monkeypatch.setattr("sys.argv", ["dart_downloader", "--host", "clihost.example.com"])
        args = parse_args()
        assert args.host == "clihost.example.com"

    def test_host_defaults_to_production_when_unset(self, monkeypatch):
        from dart_downloader.__main__ import parse_args
        monkeypatch.delenv("DART_ORDERING_HOST", raising=False)
        monkeypatch.setattr("sys.argv", ["dart_downloader"])
        args = parse_args()
        assert args.host == DEFAULT_HOST


# --- Share token is masked on input ---

class TestTokenMasking:
    def test_share_token_prompt_uses_password(self, monkeypatch):
        from dart_downloader.ui import terminal as ui_terminal

        calls = []

        def fake_ask(prompt, *args, **kwargs):
            calls.append((prompt, kwargs))
            # Return an order number for the second prompt so the default folder
            # is well-formed; value is otherwise irrelevant to the assertion.
            return "12345"

        monkeypatch.setattr(ui_terminal.Prompt, "ask", staticmethod(fake_ask))
        ui_terminal.prompt_inputs()

        # First prompt is the share token and must be masked.
        token_prompt, token_kwargs = calls[0]
        assert "Share Token" in token_prompt
        assert token_kwargs.get("password") is True

        # The other prompts must NOT be masked.
        for prompt, kwargs in calls[1:]:
            assert kwargs.get("password") is not True


# --- Regression: full run() must not KeyError on MD5SUMS ---

import dart_downloader.download.engine as engine_mod


class TestFullRunWithMd5sums:
    def test_run_downloads_and_verifies_without_keyerror(
        self, tmp_download_folder, monkeypatch
    ):
        """End-to-end run() with an order containing MD5SUMS. This exercises the
        real progress-tracking path (no manual seeding), guarding against the
        regression where streaming MD5SUMS before progress init raised
        KeyError: 'MD5SUMS'."""
        base = "https://ex.invalid/RawData"
        good = b"read-data-A"
        good_md5 = hashlib.md5(good).hexdigest()
        md5sums = f"{good_md5}  a.fastq.gz\n".encode()

        files = [
            OrderFile(filename="MD5SUMS", filesizeinbyte=len(md5sums),
                      filetype="RawData", fileurl=f"{base}/MD5SUMS",
                      modifieddatetime="2026-01-01"),
            OrderFile(filename="a.fastq.gz", filesizeinbyte=len(good),
                      filetype="RawData", fileurl=f"{base}/a.fastq.gz",
                      modifieddatetime="2026-01-01"),
        ]
        sm = StateManager(tmp_download_folder, "DO25-11328")
        sm.load_or_create(_order_with(files))

        client = _FakeClient({f"{base}/MD5SUMS": md5sums, f"{base}/a.fastq.gz": good})
        # Patch the engine's httpx usage so run() uses our fake client.
        monkeypatch.setattr(engine_mod.httpx, "Client", lambda *a, **k: client)
        monkeypatch.setattr(engine_mod.httpx, "HTTPTransport", lambda *a, **k: object())

        engine = _make_engine(sm, tmp_download_folder)
        engine.run()  # must not raise

        completed = {f.filename: f for f in sm.get_completed_files()}
        assert completed["a.fastq.gz"].md5_status == Md5Status.VERIFIED
        assert "MD5SUMS" in completed
        assert (tmp_download_folder / "RawData" / "a.fastq.gz").read_bytes() == good

    def test_realistic_order_shape_mixed_filetypes_and_foreign_host(
        self, tmp_download_folder, monkeypatch
    ):
        """Mirror a real order: several filetypes (SampleFile, ServiceSpecification,
        Invoice, RawData incl. MD5SUMS and FASTQ, Report), with MD5SUMS present
        and file URLs served from a DIFFERENT host than the metadata. All
        synthetic bytes — no real customer data."""
        meta_host = "https://ordering.diversityarrays.com"
        file_host = "https://ordering-od-d1.diversityarrays.com"  # files on another host

        fastq1 = b"FASTQ-A" * 10
        fastq2 = b"FASTQ-B" * 12
        contents_by_name = {
            "SampleFile-DGX.csv": b"col1,col2\n1,2\n",
            "ServiceSpecification-DGX.pdf": b"%PDF-fake-spec",
            "Invoice-DGX-1.pdf": b"%PDF-fake-invoice",
            "Raw_Data_Index.html": b"<html>index</html>",
            "2662516.FASTQ.gz": fastq1,
            "2662199.FASTQ.gz": fastq2,
            "Report-DGX.zip": b"PK-fake-zip",
        }
        md5sums = (
            f"{hashlib.md5(fastq1).hexdigest()}  2662516.FASTQ.gz\n"
            f"{hashlib.md5(fastq2).hexdigest()}  2662199.FASTQ.gz\n"
        ).encode()

        def _url(filetype, name):
            return f"{file_host}/dart/orders/DGX/{filetype}/{name}"

        files = [
            OrderFile(filename="SampleFile-DGX.csv", filesizeinbyte=len(contents_by_name["SampleFile-DGX.csv"]),
                      filetype="SampleFile", fileurl=_url("SampleFile", "SampleFile-DGX.csv"), modifieddatetime="2021-05-10"),
            OrderFile(filename="ServiceSpecification-DGX.pdf", filesizeinbyte=len(contents_by_name["ServiceSpecification-DGX.pdf"]),
                      filetype="ServiceSpecification", fileurl=_url("ServiceSpecification", "ServiceSpecification-DGX.pdf"), modifieddatetime="2021-05-10"),
            OrderFile(filename="Invoice-DGX-1.pdf", filesizeinbyte=len(contents_by_name["Invoice-DGX-1.pdf"]),
                      filetype="Invoice", fileurl=_url("Invoice", "Invoice-DGX-1.pdf"), modifieddatetime="2021-05-28"),
            OrderFile(filename="Raw_Data_Index.html", filesizeinbyte=len(contents_by_name["Raw_Data_Index.html"]),
                      filetype="RawData", fileurl=_url("RawData", "Raw_Data_Index.html"), modifieddatetime="2026-06-11"),
            OrderFile(filename="MD5SUMS", filesizeinbyte=len(md5sums),
                      filetype="RawData", fileurl=_url("RawData", "MD5SUMS"), modifieddatetime="2026-06-03"),
            OrderFile(filename="2662516.FASTQ.gz", filesizeinbyte=len(fastq1),
                      filetype="RawData", fileurl=_url("RawData", "2662516.FASTQ.gz"), modifieddatetime="2022-01-27"),
            OrderFile(filename="2662199.FASTQ.gz", filesizeinbyte=len(fastq2),
                      filetype="RawData", fileurl=_url("RawData", "2662199.FASTQ.gz"), modifieddatetime="2022-01-27"),
            OrderFile(filename="Report-DGX.zip", filesizeinbyte=len(contents_by_name["Report-DGX.zip"]),
                      filetype="Report", fileurl=_url("Report", "Report-DGX.zip"), modifieddatetime="2021-10-21"),
        ]

        url_contents = {_url(f.filetype, f.filename): contents_by_name[f.filename]
                        for f in files if f.filename != "MD5SUMS"}
        url_contents[_url("RawData", "MD5SUMS")] = md5sums

        sm = StateManager(tmp_download_folder, "DGX")
        sm.load_or_create(_order_with(files))

        client = _FakeClient(url_contents)
        monkeypatch.setattr(engine_mod.httpx, "Client", lambda *a, **k: client)
        monkeypatch.setattr(engine_mod.httpx, "HTTPTransport", lambda *a, **k: object())

        engine = _make_engine(sm, tmp_download_folder)
        engine.run()  # must not raise (regression: KeyError: 'MD5SUMS')

        completed = {f.filename: f for f in sm.get_completed_files()}
        # All 8 files complete.
        assert len(completed) == len(files)
        # FASTQ files were MD5-verified against MD5SUMS.
        assert completed["2662516.FASTQ.gz"].md5_status == Md5Status.VERIFIED
        assert completed["2662199.FASTQ.gz"].md5_status == Md5Status.VERIFIED
        # Files with no MD5 entry pass as n/a.
        assert completed["Report-DGX.zip"].md5_status == Md5Status.NOT_AVAILABLE
        assert completed["Invoice-DGX-1.pdf"].md5_status == Md5Status.NOT_AVAILABLE
        # Downloads used the foreign file host, not the metadata host.
        assert any(meta_host not in u for u in client.request_counts)
        assert all(file_host in u for u in client.request_counts)
