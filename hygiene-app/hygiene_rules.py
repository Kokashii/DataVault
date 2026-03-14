"""
hygiene_rules.py

Hygiene rules engine. Each rule is a standalone function:
    rule_*(record, **config) -> list[str]

Rules return issue strings; an empty list means no problems found.
apply_all_rules() runs every registered rule and writes issues back
onto each FileRecord via record.add_issue() (in-place mutation).

Adding a new rule: write the function, add one entry to RULE_REGISTRY.

Time complexity per rule
────────────────────────
  rule_poor_naming   O(|stem|) ≈ O(1) — three short regex scans
  rule_outdated      O(1)              — one integer comparison
  rule_duplicate     O(1) per file after O(N·S) build_hash_map
  rule_misplaced     O(W)              — W = words in filename stem
  apply_all_rules    O(N·S) dominated by hashing; all others O(N)
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from models import FileRecord, FileStore

log = logging.getLogger(__name__)

RuleFunc = Callable[..., list[str]]

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_OUTDATED_DAYS: int = 365
_HASH_CHUNK: int = 65_536   # 64 KB read blocks — keeps RAM flat for large files

_RE_SPACES  = re.compile(r"\s+")
_RE_REPEAT  = re.compile(r"\b(\w{3,})\W*\1\b", re.IGNORECASE)
_RE_BAD_CAPS = re.compile(r"[a-z][A-Z]{2,}|[A-Z]{2,}[a-z][A-Z]")

# keyword → set of acceptable parent folder names
FOLDER_KEYWORDS: dict[str, set[str]] = {
    "resume":     {"resume", "cv", "job", "career", "applications"},
    "cv":         {"resume", "cv", "job", "career", "applications"},
    "invoice":    {"invoices", "billing", "finance", "accounting"},
    "receipt":    {"receipts", "expenses", "finance", "accounting"},
    "tax":        {"tax", "taxes", "finance", "accounting"},
    "budget":     {"budget", "finance", "accounting"},
    "photo":      {"photos", "pictures", "images", "media"},
    "img":        {"photos", "pictures", "images", "media"},
    "screenshot": {"screenshots", "screen", "captures", "media"},
    "contract":   {"contracts", "legal", "agreements"},
    "report":     {"reports", "work", "projects"},
    "notes":      {"notes", "docs", "documents", "personal"},
    "backup":     {"backup", "backups", "archive"},
}


# ── Rule: poor naming ─────────────────────────────────────────────────────────

def rule_poor_naming(record: FileRecord, **_) -> list[str]:
    """
    Detect poor filename conventions.
      1. Spaces in the stem.
      2. Repeated adjacent words ("final_final", "copy copy").
      3. Inconsistent mid-word capitalisation ("myFILEName").

    Time complexity: O(|stem|) — three independent regex scans.
    """
    issues: list[str] = []
    stem = record.path.stem

    if _RE_SPACES.search(stem):
        issues.append("poor_name:spaces")

    if _RE_REPEAT.search(stem):
        issues.append("poor_name:repeated_word")

    if _RE_BAD_CAPS.search(stem):
        issues.append("poor_name:inconsistent_caps")

    return issues


# ── Rule: outdated files ──────────────────────────────────────────────────────

def rule_outdated(
    record: FileRecord,
    *,
    max_age_days: int = DEFAULT_OUTDATED_DAYS,
    **_,
) -> list[str]:
    """
    Flag files not modified in more than max_age_days days.
    Time complexity: O(1) — one integer comparison.
    """
    if record.age_days > max_age_days:
        return [f"outdated:{record.age_days}d"]
    return []


# ── Rule: duplicate detection ─────────────────────────────────────────────────

def rule_duplicate(
    record: FileRecord,
    *,
    hash_map: dict[str, list[str]] | None = None,
    **_,
) -> list[str]:
    """
    Flag file as a duplicate if another file shares its content hash.

    Relies on a pre-built hash_map from build_hash_map().
    Per-file cost: O(1) dict lookup once the map is built.
    Build cost:    O(N·S) where S = average file size.
    """
    if hash_map is None:
        return []

    content_hash = _get_stored_hash(record)
    if content_hash is None:
        return []

    if len(hash_map.get(content_hash, [])) > 1:
        return [f"duplicate:{content_hash[:8]}"]
    return []


def build_hash_map(store: FileStore) -> dict[str, list[str]]:
    """
    Compute SHA-256 for every file and return a map: hash → [path_str, ...].
    Hashes are also stored as tags on each FileRecord for O(1) retrieval.

    Time complexity: O(N·S) — each file read once in 64 KB chunks.
    Memory:          O(N)   — one 64-char hex string stored per record.
    """
    hash_map: dict[str, list[str]] = defaultdict(list)
    hashed = skipped = 0

    for path_str, record in store.items():
        h = _hash_file(record.path)
        if h is None:
            skipped += 1
            continue
        record.add_tag(f"hash:{h}")
        hash_map[h].append(path_str)
        hashed += 1

    log.info(
        "build_hash_map: hashed=%d skipped=%d dup_groups=%d",
        hashed, skipped,
        sum(1 for v in hash_map.values() if len(v) > 1),
    )
    return dict(hash_map)


def _hash_file(path: Path) -> str | None:
    """SHA-256 of file contents, 64 KB at a time. Returns None on IO error."""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_HASH_CHUNK):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (OSError, PermissionError) as exc:
        log.debug("Cannot hash %s: %s", path, exc)
        return None


def _get_stored_hash(record: FileRecord) -> str | None:
    """Retrieve the content hash previously stored as a tag. O(T), T tiny."""
    for tag in record.tags:
        if tag.startswith("hash:"):
            return tag[5:]
    return None


# ── Rule: misplaced files ─────────────────────────────────────────────────────

def rule_misplaced(
    record: FileRecord,
    *,
    keyword_map: dict[str, set[str]] = FOLDER_KEYWORDS,
    **_,
) -> list[str]:
    """
    Flag files whose name implies they belong in a different folder.

    Time complexity: O(W·K)
        W = words in stem, K = keyword_map size (fixed ~13) → effectively O(W).
    """
    issues: list[str] = []
    words = set(re.split(r"[^a-z0-9]+", record.path.stem.lower())) - {""}
    current_folder = record.parent_folder.lower()

    for word in words:
        expected = keyword_map.get(word)
        if expected and current_folder not in expected:
            issues.append(f"misplaced:{word}→{current_folder}")

    return issues


# ── Registry & orchestrator ───────────────────────────────────────────────────

RULE_REGISTRY: dict[str, RuleFunc] = {
    "poor_naming": rule_poor_naming,
    "outdated":    rule_outdated,
    "duplicate":   rule_duplicate,
    "misplaced":   rule_misplaced,
}


def apply_all_rules(
    store: FileStore,
    *,
    enabled: set[str] | None = None,
    rule_config: dict[str, dict] | None = None,
) -> dict[str, list[str]]:
    """
    Run every enabled rule against every FileRecord.

    Issues are appended to each record in-place and also returned as a
    summary dict for the caller.

    Args:
        store       : The central FileStore.
        enabled     : Rule names to run. None = all rules.
        rule_config : Per-rule kwargs, e.g.:
                        {"outdated": {"max_age_days": 180},
                         "duplicate": {"hash_map": hmap}}

    Returns:
        dict[path_str → list[issue_str]] — only files with issues.

    Time complexity:
        O(N·S) dominated by hashing; all other rules are O(N).
    """
    active = {
        name: fn
        for name, fn in RULE_REGISTRY.items()
        if enabled is None or name in enabled
    }
    cfg = rule_config or {}

    # Auto-build hash map if duplicate rule is active and none was supplied
    if "duplicate" in active and "hash_map" not in cfg.get("duplicate", {}):
        log.info("apply_all_rules: building hash map.")
        hmap = build_hash_map(store)
        cfg.setdefault("duplicate", {})["hash_map"] = hmap

    summary: dict[str, list[str]] = {}

    for path_str, record in store.items():
        file_issues: list[str] = []

        for rule_name, rule_fn in active.items():
            try:
                found = rule_fn(record, **cfg.get(rule_name, {}))
            except Exception as exc:
                log.warning("Rule %r raised on %s: %s", rule_name, path_str, exc)
                found = []

            for issue in found:
                record.add_issue(issue)
                file_issues.append(issue)

        if file_issues:
            summary[path_str] = file_issues

    log.info(
        "apply_all_rules: %d/%d files flagged.",
        len(summary), len(store),
    )
    return summary