from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

# Name of the checksum manifest file that DArT includes in an order (when present).
MD5SUMS_FILENAME = "MD5SUMS"

# Read files in 1 MB chunks when hashing so large genomic files don't load into memory.
_HASH_CHUNK_SIZE = 1024 * 1024


def parse_md5sums(text: str) -> dict[str, str]:
    """Parse the contents of an ``MD5SUMS`` file.

    Accepts the standard ``md5sum`` output format, one entry per line::

        d41d8cd98f00b204e9800998ecf8427e  filename.fastq.gz
        d41d8cd98f00b204e9800998ecf8427e *filename.fastq.gz

    Both the two-space (text mode) and the space-plus-asterisk (binary mode)
    separators are handled. Blank lines and ``#`` comments are ignored. Keys
    are the file basenames; digests are lower-cased hex strings.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Split into digest and the remainder on the first run of whitespace.
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        digest = digest.strip().lower()
        # Binary-mode entries prefix the name with '*'.
        name = name.lstrip("*").strip()
        if not digest or not name:
            continue
        # Store under the basename so lookups by filename work regardless of
        # any leading path in the MD5SUMS entry.
        result[Path(name).name] = digest
    return result


def compute_md5(filepath: Path) -> str:
    """Return the lower-case hex MD5 digest of a file, hashed in chunks."""
    digest = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ValidationResult:
    def __init__(self, valid: bool, message: str = ""):
        self.valid = valid
        self.message = message


class ValidationProvider(ABC):
    @abstractmethod
    def validate(self, filepath: Path, expected_size: int, **kwargs) -> ValidationResult:
        ...


class SizeValidationProvider(ValidationProvider):
    def validate(self, filepath: Path, expected_size: int, **kwargs) -> ValidationResult:
        if not filepath.exists():
            return ValidationResult(False, "File does not exist")
        actual_size = filepath.stat().st_size
        if actual_size == expected_size:
            return ValidationResult(True)
        return ValidationResult(
            False,
            f"Size mismatch: expected {expected_size}, got {actual_size}",
        )


class Md5ValidationProvider(ValidationProvider):
    """Validate a file's content against an expected MD5 digest.

    The expected digest is passed via the ``expected_md5`` keyword argument
    (typically taken from a parsed ``MD5SUMS`` file). When ``expected_md5`` is
    ``None`` the file has no entry in MD5SUMS: this is treated as a pass with a
    note, since size validation has already covered completeness.
    """

    def validate(
        self,
        filepath: Path,
        expected_size: int,
        *,
        expected_md5: str | None = None,
        actual_md5: str | None = None,
        **kwargs,
    ) -> ValidationResult:
        """Validate the file's MD5 against ``expected_md5``.

        ``actual_md5`` may be supplied by the caller when the digest was already
        computed while streaming the file to disk (see the engine's inline
        hashing), avoiding a second full read of large genomic files. When it is
        ``None`` the digest is computed here by reading the file.
        """
        if not filepath.exists():
            return ValidationResult(False, "File does not exist")
        if expected_md5 is None:
            return ValidationResult(True, "No MD5 checksum available (not in MD5SUMS)")
        if actual_md5 is None:
            actual_md5 = compute_md5(filepath)
        if actual_md5 == expected_md5.lower():
            return ValidationResult(True, "MD5 verified")
        return ValidationResult(
            False,
            f"MD5 mismatch: expected {expected_md5.lower()}, got {actual_md5}",
        )
