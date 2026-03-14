"""
scanner.py

Recursively scans a directory and builds the central in-memory FileStore:

    FileStore = dict[str, FileRecord]
        key   = str(absolute path)   — O(1) average lookup
        value = FileRecord dataclass

Time complexity
───────────────
  scan_directory   O(N)  — single rglob pass; one stat() per file
  dict insertions  O(1) amortised each
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator

from models import FileRecord, FileStore

log = logging.getLogger(__name__)


def scan_directory(
    root: str | Path,
    *,
    skip_hidden: bool = True,
    min_size_bytes: int = 0,
    extensions: set[str] | None = None,
) -> tuple[FileStore, dict]:
    """
    Scan `root` recursively and return (file_store, stats).

    Args:
        root           : Directory to scan (str or Path).
        skip_hidden    : Skip files/dirs whose name starts with '.' (default True).
        min_size_bytes : Ignore files smaller than this many bytes (default 0).
        extensions     : Whitelist of lowercase extensions without dot,
                         e.g. {"pdf", "docx"}. None = accept all.

    Returns:
        file_store : dict[str, FileRecord]
        stats      : Scan summary dict (counts, timing, root).

    Raises:
        FileNotFoundError  : root does not exist.
        NotADirectoryError : root is not a directory.
    """
    root = Path(root).resolve()

    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    file_store: FileStore = {}
    stats: dict = {
        "root":               str(root),
        "scanned":            0,
        "skipped_hidden":     0,
        "skipped_unreadable": 0,
        "skipped_symlink":    0,
        "skipped_filter":     0,
        "started_at":         datetime.now().isoformat(),
        "duration_ms":        0.0,
    }

    t0 = datetime.now()

    for record in _iter_records(root, skip_hidden, min_size_bytes, extensions, stats):
        file_store[record.path_str] = record   # O(1) amortised
        stats["scanned"] += 1

    stats["duration_ms"] = round((datetime.now() - t0).total_seconds() * 1000, 2)
    log.info(
        "Scan complete: %d files in %.1f ms (root=%s)",
        stats["scanned"], stats["duration_ms"], root,
    )
    return file_store, stats


# ── Internal helpers ──────────────────────────────────────────────────────────

def _iter_records(
    root: Path,
    skip_hidden: bool,
    min_size_bytes: int,
    extensions: set[str] | None,
    stats: dict,
) -> Iterator[FileRecord]:
    """
    Lazy generator: yields one FileRecord per qualifying file.
    Memory: O(directory depth), not O(N).
    Per-file cost: O(1) — one stat() call.
    """
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        if path.is_symlink():
            stats["skipped_symlink"] += 1
            continue

        if skip_hidden and _is_hidden(path, root):
            stats["skipped_hidden"] += 1
            continue

        try:
            stat = path.stat()
        except (PermissionError, OSError) as exc:
            log.debug("Skipping unreadable %s: %s", path, exc)
            stats["skipped_unreadable"] += 1
            continue

        if stat.st_size < min_size_bytes:
            stats["skipped_filter"] += 1
            continue

        ext = path.suffix.lstrip(".").lower()

        if extensions is not None and ext not in extensions:
            stats["skipped_filter"] += 1
            continue

        yield FileRecord(
            name=path.name,
            path=path,
            extension=ext,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime),
            parent_folder=path.parent.name,
        )


def _is_hidden(path: Path, root: Path) -> bool:
    """
    True if any path component below root starts with '.'.
    Checks only the relative portion so scanning ~/.config doesn't
    skip everything underneath it.
    O(D) where D = directory depth — negligible.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part.startswith(".") for part in parts)