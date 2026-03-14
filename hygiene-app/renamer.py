"""
renamer.py

Rename execution engine for the file hygiene tool.

Responsibilities
────────────────
  build_rename_plan()   Collect all RENAME suggestions from the action_map
                        into a structured RenameJob list. No disk writes.

  validate_plan()       Check every job for conflicts before touching anything:
                        - source file still exists
                        - destination name doesn't already exist in the same dir
                        - destination name doesn't collide with another job

  apply_rename_plan()   Execute a list of RenameJob objects, skipping any that
                        are marked skip=True. Returns a RenameResult summary.

Design principles
─────────────────
- Zero disk writes until apply_rename_plan() is explicitly called.
- Each RenameJob is fully self-contained so the UI can display, edit,
  and deselect individual renames without re-running the rules engine.
- Conflict detection runs in a single O(N) pass using a set of seen
  destination paths to catch same-directory collisions.
- apply_rename_plan() never raises: every error is captured into
  RenameResult.errors so the UI always gets a complete result object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models    import FileRecord, FileStore
from suggestions import ActionKind, ActionSuggestion

log = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class RenameJob:
    """
    One proposed rename operation.

    Fields
    ──────
    record        : The source FileRecord (pre-rename state).
    old_path      : Absolute Path before rename (= record.path).
    new_name      : Proposed filename after rename (stem + ext, no dir).
    new_path      : Absolute Path after rename (same dir, new name).
    suggestion    : The ActionSuggestion that generated this job.
    skip          : Set to True by the UI to opt this file out.
    conflict      : Human-readable conflict description, or "" if clear.
    """
    record:     FileRecord
    old_path:   Path
    new_name:   str
    new_path:   Path
    suggestion: ActionSuggestion
    skip:       bool = False
    conflict:   str  = ""

    @property
    def display_arrow(self) -> str:
        return f"{self.old_path.name}  →  {self.new_name}"


@dataclass
class RenameResult:
    """
    Summary returned by apply_rename_plan().
    """
    applied: list[tuple[Path, Path]]   = field(default_factory=list)
    skipped: list[RenameJob]           = field(default_factory=list)
    errors:  list[tuple[Path, str]]    = field(default_factory=list)

    @property
    def n_applied(self) -> int: return len(self.applied)
    @property
    def n_skipped(self) -> int: return len(self.skipped)
    @property
    def n_errors(self)  -> int: return len(self.errors)


# ── Plan builder ──────────────────────────────────────────────────────────────

def build_rename_plan(
    store:      FileStore,
    action_map: dict[str, list[ActionSuggestion]],
) -> list[RenameJob]:
    """
    Scan action_map for RENAME suggestions and build a RenameJob per file.

    Only the highest-confidence RENAME suggestion per file is included —
    multiple rename suggestions for the same file would conflict with each
    other and confuse the user.

    Args:
        store      : The central FileStore (path_str → FileRecord).
        action_map : Output of suggest_for_store() (path_str → suggestions).

    Returns:
        List of RenameJob, one per file that has at least one RENAME suggestion.
        Jobs are sorted by old filename for stable, readable display.

    Time complexity: O(N·M)  N = files with suggestions, M = suggestions/file.
    """
    jobs: list[RenameJob] = []

    for path_str, suggestions in action_map.items():
        record = store.get(path_str)
        if record is None:
            continue

        # Pick the best (highest-confidence) RENAME suggestion for this file
        rename_sug = max(
            (s for s in suggestions if s.kind == ActionKind.RENAME and s.proposed_name),
            key=lambda s: s.confidence,
            default=None,
        )
        if rename_sug is None:
            continue

        new_name = rename_sug.proposed_name          # e.g. "my_resume.pdf"
        new_path = record.path.parent / new_name

        jobs.append(RenameJob(
            record=record,
            old_path=record.path,
            new_name=new_name,
            new_path=new_path,
            suggestion=rename_sug,
        ))

    jobs.sort(key=lambda j: j.old_path.name.lower())
    return jobs


# ── Conflict validator ────────────────────────────────────────────────────────

def validate_plan(jobs: list[RenameJob]) -> list[RenameJob]:
    """
    Annotate each RenameJob with a conflict description if any problem is found.
    Mutates jobs in-place (sets job.conflict) and returns the same list.

    Checks:
      1. Source file exists on disk right now.
      2. No-op rename (old name == new name).
      3. Target path already exists on disk.
      4. Two jobs in this plan would produce the same destination path.

    Time complexity: O(N)  — one pass with a set for collision detection.
    """
    seen_destinations: set[Path] = set()

    for job in jobs:
        job.conflict = ""  # reset from any prior validation run

        # 1. Source must still exist
        if not job.old_path.exists():
            job.conflict = "source file no longer exists"
            continue

        # 2. No-op
        if job.old_path.name == job.new_name:
            job.conflict = "new name is identical to current name"
            continue

        # 3. Destination already on disk
        if job.new_path.exists():
            job.conflict = f"destination already exists: {job.new_name}"
            continue

        # 4. Collision with another job in this plan
        if job.new_path in seen_destinations:
            job.conflict = f"collision with another rename in this batch: {job.new_name}"
            continue

        seen_destinations.add(job.new_path)

    return jobs


# ── Executor ──────────────────────────────────────────────────────────────────

def apply_rename_plan(jobs: list[RenameJob]) -> RenameResult:
    """
    Execute all jobs where skip=False and conflict="".

    Each rename is atomic at the OS level (pathlib Path.rename uses
    os.rename under the hood, which is atomic on POSIX).

    Args:
        jobs : List of RenameJob, typically post-validate_plan().

    Returns:
        RenameResult with applied, skipped, and error lists.

    Time complexity: O(N)  — one rename syscall per qualifying job.
    """
    result = RenameResult()

    for job in jobs:
        # Skip if user opted out or a conflict was detected
        if job.skip:
            result.skipped.append(job)
            log.debug("Skipped (user opted out): %s", job.old_path.name)
            continue

        if job.conflict:
            result.skipped.append(job)
            log.debug("Skipped (conflict): %s — %s", job.old_path.name, job.conflict)
            continue

        # Double-check source still exists right before the write
        if not job.old_path.exists():
            result.errors.append((job.old_path, "file disappeared before rename"))
            log.warning("File disappeared: %s", job.old_path)
            continue

        # Guard: don't overwrite
        if job.new_path.exists():
            result.errors.append((job.old_path, f"destination appeared at write time: {job.new_name}"))
            log.warning("Destination appeared at write time: %s", job.new_path)
            continue

        try:
            job.old_path.rename(job.new_path)
            result.applied.append((job.old_path, job.new_path))
            log.info("Renamed: %s  →  %s", job.old_path.name, job.new_name)
        except OSError as exc:
            result.errors.append((job.old_path, str(exc)))
            log.error("Rename failed for %s: %s", job.old_path, exc)

    log.info(
        "apply_rename_plan: applied=%d  skipped=%d  errors=%d",
        result.n_applied, result.n_skipped, result.n_errors,
    )
    return result