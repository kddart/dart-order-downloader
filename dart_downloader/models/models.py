from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class OrderFile(BaseModel):
    filename: str
    filesizeinbyte: int
    filetype: str
    fileurl: str
    modifieddatetime: str
    # 1 = file is in cloud storage (Wasabi) and can be downloaded aggressively;
    # 0 = file is on a local DArT server with a limited connection, so downloads
    # of these files are capped (see engine's INCLOUD_LOCAL cap). Defaults to 1
    # (cloud) when the API omits the field, so older responses aren't throttled.
    incloud: int = 1


class OrderData(BaseModel):
    ordernumber: str
    orderstatus: str
    productname: str
    numberofsamples: int
    files: list[OrderFile] = Field(default_factory=list)


class OrderResponse(BaseModel):
    data: list[OrderData]


class FileStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class Md5Status(str, Enum):
    """Outcome of MD5 content validation for a file."""

    VERIFIED = "verified"        # digest matched MD5SUMS
    NOT_AVAILABLE = "na"         # no entry in MD5SUMS (or no MD5SUMS in order)
    MISMATCH = "mismatch"        # digest did not match (only if retries exhausted)


class FileState(BaseModel):
    filename: str
    fileurl: str
    filetype: str
    expected_size: int
    # Carried through from OrderFile.incloud (see there). Persisted so resume
    # applies the same throttling. Defaults to 1 for manifests written before
    # this field existed.
    incloud: int = 1
    actual_size: int | None = None
    status: FileStatus = FileStatus.PENDING
    validated: bool = False
    md5_status: Md5Status | None = None
    retry_count: int = 0
    error_message: str | None = None


class DownloadManifest(BaseModel):
    order_number: str
    download_folder: str
    started_at: str
    completed_at: str | None = None
    files: list[FileState] = Field(default_factory=list)


class DownloadProgress(BaseModel):
    filename: str
    bytes_downloaded: int = 0
    total_bytes: int = 0
    completed: bool = False
    failed: bool = False
