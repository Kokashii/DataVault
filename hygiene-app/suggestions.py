"""
suggestions.py

Maps hygiene issues on a FileRecord to structured ActionSuggestion objects.

Registry pattern: SUGGESTION_MAP maps issue prefix → factory function.
To add a new issue type: write a factory, add one entry to SUGGESTION_MAP.

Time complexity
───────────────
  suggest_for_record   O(I·M)   I = issues on record, M = max suggestions/issue
  suggest_for_store    O(N·I·M) single pass, no cross-file work
  apply_suggestion     O(1)     single filesystem syscall
"""

from __future__ import annotations

import logging
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from models import FileRecord, FileStore

log = logging.getLogger(__name__)


# ── Action kinds ──────────────────────────────────────────────────────────────

class ActionKind(str, Enum):
    """
    Inherits from str so values are JSON-serialisable and compare equal
    to plain strings in tests: suggestion.kind == "rename"  → True.
    """
    RENAME  = "rename"
    ARCHIVE = "archive"
    DELETE  = "delete"
    MOVE    = "move"
    MERGE   = "merge"
    IGNORE  = "ignore"
    REVIEW  = "review"


# ── Suggestion dataclass ──────────────────────────────────────────────────────

@dataclass(slots=True)
class ActionSuggestion:
    """
    A single structured recommendation for one file.

    safe_to_auto : True only for deterministic, reversible actions
                   (e.g. replacing spaces with underscores).
                   DELETE is NEVER safe_to_auto by design.
    confidence   : 0.0–1.0 heuristic score for UI display.
    """
    kind:          ActionKind
    reason:        str
    issue_ref:     str

    proposed_name: str | None  = None   # set for RENAME
    proposed_path: Path | None = None   # set for MOVE / ARCHIVE
    duplicate_of:  str | None  = None   # set for DELETE / MERGE
    safe_to_auto:  bool        = False
    confidence:    float       = 1.0

    def __str__(self) -> str:
        parts = [f"[{self.kind.value.upper()}]", self.reason]
        if self.proposed_name:
            parts.append(f"→ {self.proposed_name}")
        if self.proposed_path:
            parts.append(f"→ {self.proposed_path}")
        if self.duplicate_of:
            parts.append(f"(keep: {self.duplicate_of})")
        parts.append(f"({self.confidence:.0%})")
        return "  ".join(parts)


# ── Name sanitiser ────────────────────────────────────────────────────────────

def sanitise_filename(name: str) -> str:
    """
    Produce a clean stem by applying normalisation in order:
      1. Whitespace → underscores
      2. Collapse repeated adjacent words
      3. Remove OS-illegal characters
      4. Collapse multiple separators
      5. Strip leading/trailing separators
      6. Lowercase

    Time complexity: O(|name|) — fixed number of regex passes.

    Examples:
        sanitise_filename("My Resume Final")    → "my_resume_final"
        sanitise_filename("report_FINAL_final") → "report_final"
        sanitise_filename("Invoice (Copy)")     → "invoice_copy"
    """
    s = name
    s = re.sub(r"\s+", "_", s)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\b(\w{3,})[_\-\s]+\1\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r'[<>:"/\\|?*]', "", s)
    s = re.sub(r"[_\-]{2,}", "_", s)
    s = re.sub(r"\.{2,}", "_", s)
    s = s.strip("_-. ").lower()
    return s or "untitled"


def _archive_path(record: FileRecord, archive_root: Path | None = None) -> Path:
    """Propose _archive/<year>/<filename> next to the file's parent. O(1)."""
    root = archive_root or record.path.parent.parent / "_archive"
    return root / str(record.modified_at.year) / record.name


# ── Suggestion factories ──────────────────────────────────────────────────────

SuggestionFactory = Callable[[FileRecord, str], list[ActionSuggestion]]


def _suggest_poor_naming(record: FileRecord, issue: str) -> list[ActionSuggestion]:
    """
    Propose RENAME with a sanitised filename, plus IGNORE.
    Only spaces→underscores rename is marked safe_to_auto (purely mechanical).
    """
    clean_stem    = sanitise_filename(record.path.stem)
    proposed_name = f"{clean_stem}{record.path.suffix.lower()}"

    reasons = {
        "poor_name:spaces":            "filename contains spaces",
        "poor_name:repeated_word":     "filename has a repeated word",
        "poor_name:inconsistent_caps": "filename has inconsistent capitalisation",
    }
    reason = reasons.get(issue, "filename does not follow conventions")
    auto   = (issue == "poor_name:spaces")

    return [
        ActionSuggestion(
            kind=ActionKind.RENAME,
            reason=reason,
            issue_ref=issue,
            proposed_name=proposed_name,
            safe_to_auto=auto,
            confidence=1.0,
        ),
        ActionSuggestion(
            kind=ActionKind.IGNORE,
            reason="keep current name if intentional",
            issue_ref=issue,
            safe_to_auto=True,
            confidence=0.5,
        ),
    ]


def _suggest_outdated(record: FileRecord, issue: str) -> list[ActionSuggestion]:
    """
    Primary: ARCHIVE to _archive/<year>/.
    Secondary: REVIEW.
    Tertiary: DELETE suggestion added only for files older than 5 years.
    """
    m = re.search(r"(\d+)d", issue)
    age_days   = int(m.group(1)) if m else 0
    age_desc   = f"{age_days} days" if age_days else "a long time"
    archive_to = _archive_path(record)

    suggestions = [
        ActionSuggestion(
            kind=ActionKind.ARCHIVE,
            reason=f"not modified in {age_desc}",
            issue_ref=issue,
            proposed_path=archive_to,
            safe_to_auto=False,
            confidence=0.85,
        ),
        ActionSuggestion(
            kind=ActionKind.REVIEW,
            reason="confirm whether this file is still needed",
            issue_ref=issue,
            safe_to_auto=False,
            confidence=1.0,
        ),
    ]

    if age_days > 365 * 5:
        suggestions.insert(1, ActionSuggestion(
            kind=ActionKind.DELETE,
            reason=f"file is {age_days // 365} years old — may be obsolete",
            issue_ref=issue,
            safe_to_auto=False,   # DELETE is NEVER safe_to_auto
            confidence=0.4,
        ))

    return suggestions


def _suggest_duplicate(record: FileRecord, issue: str) -> list[ActionSuggestion]:
    """
    Propose DELETE (this copy), MERGE (keep better-named copy), or IGNORE.
    """
    hash_hint = issue.split(":")[-1] if ":" in issue else ""
    return [
        ActionSuggestion(
            kind=ActionKind.DELETE,
            reason=f"exact content duplicate (group {hash_hint})",
            issue_ref=issue,
            safe_to_auto=False,
            confidence=0.95,
        ),
        ActionSuggestion(
            kind=ActionKind.MERGE,
            reason="keep the copy with the better name or location",
            issue_ref=issue,
            safe_to_auto=False,
            confidence=0.7,
        ),
        ActionSuggestion(
            kind=ActionKind.IGNORE,
            reason="intentional duplicate — keep both",
            issue_ref=issue,
            safe_to_auto=True,
            confidence=0.3,
        ),
    ]


def _suggest_misplaced(record: FileRecord, issue: str) -> list[ActionSuggestion]:
    """
    Propose MOVE to a sibling folder named after the triggering keyword.
    """
    m = re.match(r"misplaced:([^→]+)", issue)
    keyword  = m.group(1) if m else ""
    proposed = record.path.parent.parent / keyword if keyword else None
    reason   = (
        f'"{keyword}" files may belong in a dedicated folder'
        if keyword else "file may be in the wrong folder"
    )
    return [
        ActionSuggestion(
            kind=ActionKind.MOVE,
            reason=reason,
            issue_ref=issue,
            proposed_path=proposed,
            safe_to_auto=False,
            confidence=0.65,
        ),
        ActionSuggestion(
            kind=ActionKind.IGNORE,
            reason="current location is intentional",
            issue_ref=issue,
            safe_to_auto=True,
            confidence=0.4,
        ),
    ]


def _suggest_default(record: FileRecord, issue: str) -> list[ActionSuggestion]:
    """Fallback: emit REVIEW so unknown issues are never silently dropped."""
    return [
        ActionSuggestion(
            kind=ActionKind.REVIEW,
            reason=f"unrecognised issue: {issue}",
            issue_ref=issue,
            safe_to_auto=False,
            confidence=0.5,
        )
    ]


# ── Registry ──────────────────────────────────────────────────────────────────

SUGGESTION_MAP: dict[str, SuggestionFactory] = {
    "poor_name": _suggest_poor_naming,
    "outdated":  _suggest_outdated,
    "duplicate": _suggest_duplicate,
    "misplaced": _suggest_misplaced,
}


# ── Public API ────────────────────────────────────────────────────────────────

def suggest_for_record(record: FileRecord) -> list[ActionSuggestion]:
    """
    Produce all suggestions for one FileRecord.
    O(I·M): I = issues, M = max suggestions per issue (≤ 3, fixed).

    Example:
        record.add_issue("poor_name:spaces")
        record.add_issue("misplaced:resume→downloads")
        for s in suggest_for_record(record):
            print(s)
    """
    suggestions: list[ActionSuggestion] = []
    for issue in record.issues:
        prefix  = issue.split(":")[0]
        factory = SUGGESTION_MAP.get(prefix, _suggest_default)
        suggestions.extend(factory(record, issue))
    return suggestions


def suggest_for_store(
    store: FileStore,
    *,
    only_with_issues: bool = True,
) -> dict[str, list[ActionSuggestion]]:
    """
    Run suggest_for_record() over every file in the store. O(N·I·M).

    Returns dict[path_str → list[ActionSuggestion]].
    """
    result: dict[str, list[ActionSuggestion]] = {}
    for path_str, record in store.items():
        if only_with_issues and not record.issues:
            continue
        suggestions = suggest_for_record(record)
        if suggestions or not only_with_issues:
            result[path_str] = suggestions
    log.info(
        "suggest_for_store: %d/%d files have suggestions.",
        len(result), len(store),
    )
    return result


def group_by_kind(
    suggestions: list[ActionSuggestion],
) -> dict[ActionKind, list[ActionSuggestion]]:
    """
    Group a flat suggestion list by ActionKind. O(S).
    Useful for UI rendering: show all RENAMEs together, then MOVEs, etc.
    """
    grouped: dict[ActionKind, list[ActionSuggestion]] = defaultdict(list)
    for s in suggestions:
        grouped[s.kind].append(s)
    return dict(grouped)


def format_suggestions(
    suggestions: list[ActionSuggestion],
    record: FileRecord | None = None,
) -> str:
    """
    Render suggestions as a human-readable text block.
    Optionally prefixes with the file name and path.
    O(S) where S = number of suggestions.
    """
    lines: list[str] = []
    if record:
        lines += [record.name, str(record.path), ""]
    if not suggestions:
        lines.append("  (no suggestions)")
        return "\n".join(lines)
    for s in suggestions:
        lines.append(f"  {s}")
    return "\n".join(lines)


def apply_suggestion(
    suggestion: ActionSuggestion,
    record: FileRecord,
    *,
    dry_run: bool = True,
) -> bool:
    """
    Optionally execute a suggestion on disk.

    dry_run=True (default): log only, touch nothing.
    DELETE is blocked unless safe_to_auto=True AND dry_run=False
    (safe_to_auto is never True for DELETE by design — double gate).

    Returns True if action was applied (or would be in dry_run).
    Time complexity: O(1) — single syscall.
    """
    src = record.path
    act = suggestion.kind
    tag = "DRY" if dry_run else "LIVE"

    if act == ActionKind.RENAME:
        if not suggestion.proposed_name:
            return False
        dst = src.parent / suggestion.proposed_name
        log.info("[%s] RENAME %s → %s", tag, src.name, dst.name)
        if not dry_run:
            try:
                src.rename(dst)
            except OSError as exc:
                log.error("RENAME failed: %s", exc); return False

    elif act == ActionKind.MOVE:
        if not suggestion.proposed_path:
            return False
        dst = suggestion.proposed_path / record.name
        log.info("[%s] MOVE %s → %s", tag, src, dst)
        if not dry_run:
            try:
                suggestion.proposed_path.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as exc:
                log.error("MOVE failed: %s", exc); return False

    elif act == ActionKind.ARCHIVE:
        if not suggestion.proposed_path:
            return False
        dst = suggestion.proposed_path
        log.info("[%s] ARCHIVE %s → %s", tag, src, dst)
        if not dry_run:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as exc:
                log.error("ARCHIVE failed: %s", exc); return False

    elif act == ActionKind.DELETE:
        if suggestion.safe_to_auto and not dry_run:
            log.info("[LIVE] DELETE %s", src)
            try:
                src.unlink()
            except OSError as exc:
                log.error("DELETE failed: %s", exc); return False
        else:
            log.info("[BLOCKED] DELETE %s — requires explicit confirmation", src)
            return False

    else:
        log.info("[%s] %s %s (no filesystem action)", tag, act.value.upper(), src.name)

    return True