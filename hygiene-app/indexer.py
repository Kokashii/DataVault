"""
indexer.py

Builds fast lookup indexes from the central FileStore.

All indexes: defaultdict(set)
    O(1)  average — insert a path into a bucket
    O(1)  average — retrieve all files matching one filter key
    O(|R|) — intersect two buckets where R = result set

Overall build: O(N·T)
    N = files, T = avg filename tokens per stem (typically 3–8)
    All other per-file operations are O(1).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from models import FileRecord, FileStore

log = logging.getLogger(__name__)

# ── Size tier thresholds (bytes) ──────────────────────────────────────────────
_SIZE_TIERS: list[tuple[str, int]] = [
    ("tiny",   10 * 1024),
    ("small",  1 * 1024 ** 2),
    ("medium", 100 * 1024 ** 2),
    ("large",  1 * 1024 ** 3),
    # >= 1 GB → "huge"
]

# Splits on separators AND camelCase boundaries
_SPLIT_RE = re.compile(r"[\s_\-\.]|(?<=[a-z])(?=[A-Z])")

# Noise words excluded from the term index
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
    "or", "is", "it", "by", "be", "as", "with",
    "new", "old", "tmp", "temp", "copy", "backup",
})


# ── FileIndex container ───────────────────────────────────────────────────────

class FileIndex:
    """
    Container for all lookup indexes built from a FileStore.

    Each attribute is a defaultdict(set) for direct O(1) access:

        index.by_extension["pdf"]      → set of path strings
        index.by_issue["duplicate"]    → set of path strings
        index.by_extension["pdf"] & index.by_folder["docs"]  → intersection

    Use query() for multi-filter intersection with automatic
    smallest-set-first ordering.
    """

    __slots__ = (
        "by_extension", "by_folder", "by_issue",
        "by_tag", "by_term", "by_size_tier", "_store",
    )

    def __init__(self, store: FileStore) -> None:
        self.by_extension: defaultdict[str, set[str]] = defaultdict(set)
        self.by_folder:    defaultdict[str, set[str]] = defaultdict(set)
        self.by_issue:     defaultdict[str, set[str]] = defaultdict(set)
        self.by_tag:       defaultdict[str, set[str]] = defaultdict(set)
        self.by_term:      defaultdict[str, set[str]] = defaultdict(set)
        self.by_size_tier: defaultdict[str, set[str]] = defaultdict(set)
        self._store = store

    def get_records(self, paths: set[str]) -> list[FileRecord]:
        """Resolve path strings → FileRecord objects. O(|paths|)."""
        return [r for p in paths if (r := self._store.get(p))]

    def query(self, **filters) -> set[str]:
        """
        Multi-filter intersection — smallest set first.
        Usage: index.query(by_extension="pdf", by_folder="docs")
        Time: O(F · |S_min|) where F = filter count, S_min = smallest set.
        """
        _map = {
            "by_extension": self.by_extension,
            "by_folder":    self.by_folder,
            "by_issue":     self.by_issue,
            "by_tag":       self.by_tag,
            "by_term":      self.by_term,
            "by_size_tier": self.by_size_tier,
        }
        sets: list[set[str]] = []
        for name, key in filters.items():
            if name not in _map:
                raise ValueError(f"Unknown index: {name!r}")
            sets.append(_map[name][key])

        if not sets:
            return set()

        sets.sort(key=len)
        result = sets[0].copy()
        for s in sets[1:]:
            result &= s
            if not result:
                break
        return result

    def stats(self) -> dict:
        return {
            "unique_extensions": len(self.by_extension),
            "unique_folders":    len(self.by_folder),
            "unique_issues":     len(self.by_issue),
            "unique_tags":       len(self.by_tag),
            "unique_terms":      len(self.by_term),
            "size_tiers":        {k: len(v) for k, v in self.by_size_tier.items()},
        }


# ── Public API ────────────────────────────────────────────────────────────────

def build_indexes(store: FileStore) -> FileIndex:
    """
    Build all indexes from a populated FileStore in one O(N·T) pass.
    Call AFTER the rules engine so issues/tags are populated.
    """
    idx = FileIndex(store)
    for path_str, record in store.items():
        _index_one(idx, path_str, record)

    log.info(
        "Indexes built: %d files | %d extensions | %d terms",
        len(store), len(idx.by_extension), len(idx.by_term),
    )
    return idx


def tokenize(stem: str) -> list[str]:
    """
    Split a filename stem into lowercase search tokens, dropping noise words.

    Examples:
        tokenize("ResumeV3_Final")    → ["resume", "v3"]
        tokenize("tax-return-2023")   → ["tax", "return", "2023"]
        tokenize("IMG_4821")          → ["img", "4821"]

    Time complexity: O(|stem|) — single regex split + list comprehension.
    Public so the search engine uses the same tokenisation rules.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for part in _SPLIT_RE.split(stem):
        t = part.lower()
        if len(t) >= 2 and t not in _STOPWORDS and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


# ── Internal ──────────────────────────────────────────────────────────────────

def _index_one(idx: FileIndex, path_str: str, record: FileRecord) -> None:
    """Insert one FileRecord into all index buckets. All adds O(1) amortised."""
    idx.by_extension[record.extension or "__none__"].add(path_str)
    idx.by_folder[record.parent_folder.lower()].add(path_str)
    for issue in record.issues:
        idx.by_issue[issue].add(path_str)
    for tag in record.tags:
        idx.by_tag[tag.lower()].add(path_str)
    for token in tokenize(record.path.stem):
        idx.by_term[token].add(path_str)
    idx.by_size_tier[_size_tier(record.size_bytes)].add(path_str)


def _size_tier(size_bytes: int) -> str:
    """Map byte count to named tier. O(k), k=4 → O(1)."""
    for name, threshold in _SIZE_TIERS:
        if size_bytes < threshold:
            return name
    return "huge"