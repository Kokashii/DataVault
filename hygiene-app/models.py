"""
models.py

Core data model for the DataVault tool.
Every scanned file becomes one FileRecord stored in the central FileStore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class FileRecord:
    """
    Represents a single file discovered during a directory scan.

    Static fields are populated once by the scanner via a single stat() call.
    Mutable fields (issues, tags) are enriched later by the rules engine.
    """

    # ── Core metadata (set by scanner) ───────────────────────────────────────
    name:          str       # filename with extension, e.g. "resume_final.pdf"
    path:          Path      # absolute pathlib.Path
    extension:     str       # lowercase, no dot — e.g. "pdf"; "" if none
    size_bytes:    int       # raw byte count from stat().st_size
    modified_at:   datetime  # derived from stat().st_mtime
    parent_folder: str       # immediate parent directory name

    # ── Enrichment fields (set by rules engine) ───────────────────────────────
    issues: list[str] = field(default_factory=list)
    # e.g. ["duplicate:a1b2c3", "outdated:412d", "poor_name:spaces"]

    tags: list[str] = field(default_factory=list)
    # e.g. ["resume", "tax", "hash:deadbeef..."]

    # ── Computed helpers ──────────────────────────────────────────────────────

    @property
    def path_str(self) -> str:
        """String key used in the central FileStore dict. O(1)."""
        return str(self.path)

    @property
    def size_kb(self) -> float:
        return round(self.size_bytes / 1024, 2)

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 ** 2), 2)

    @property
    def age_days(self) -> int:
        """Days since last modification. O(1)."""
        return (datetime.now() - self.modified_at).days

    def add_issue(self, issue: str) -> None:
        """Append a hygiene issue if not already present. O(n), n tiny."""
        if issue not in self.issues:
            self.issues.append(issue)

    def add_tag(self, tag: str) -> None:
        """Append a tag if not already present. O(n), n tiny."""
        if tag not in self.tags:
            self.tags.append(tag)

    def __hash__(self) -> int:
        """Hash by path so FileRecords can live in sets. O(1)."""
        return hash(self.path)


# Central store type alias used across all modules
FileStore = dict[str, FileRecord]