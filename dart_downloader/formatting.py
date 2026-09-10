"""Shared human-readable formatting helpers (bytes, durations)."""

from __future__ import annotations


def format_bytes(size: float) -> str:
    """Format a byte count as a human-readable string (e.g. "1.50 MB")."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as e.g. "1h 2m 3s"."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)
