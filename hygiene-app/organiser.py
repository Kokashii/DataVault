"""
organiser.py

Subfolder organiser for the file hygiene tool.

Given a FileStore, this module groups files by a chosen strategy and
proposes moving them into new subfolders named after the group key.
No directories are created and no files are moved until
apply_organise_plan() is explicitly called.

Grouping strategies
───────────────────
  "extension"   pdf/ · docx/ · png/ · …
  "keyword"     resume/ · invoice/ · photo/ · …  (from FOLDER_KEYWORDS)
  "year"        2021/ · 2022/ · 2023/ · …  (by last-modified year)
  "first_letter" a/ · b/ · c/ · …  (alphabetical buckets)

Each strategy returns a list of MoveJob objects.  The UI chooses which
strategy to use via a selectbox and may toggle individual jobs off before
calling apply_organise_plan().

Data flow
─────────
  build_organise_plan(store, strategy, root)
      → list[MoveJob]           (pure, no I/O)

  validate_organise_plan(jobs)
      → list[MoveJob]           (sets job.conflict, still no I/O)

  apply_organise_plan(jobs)
      → OrganiseResult          (creates dirs + moves files)
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from models import FileRecord, FileStore

log = logging.getLogger(__name__)

# ── Keyword map (mirrors hygiene_rules.FOLDER_KEYWORDS) ──────────────────────
# Maps a keyword found in a filename stem → the canonical subfolder name to
# create.  First match wins, so order matters for overlapping terms.

KEYWORD_FOLDERS: list[tuple[str, str]] = [
    ("resume",     "resume"),
    ("cv",         "resume"),
    ("invoice",    "invoices"),
    ("receipt",    "receipts"),
    ("tax",        "taxes"),
    ("budget",     "budget"),
    ("screenshot", "screenshots"),
    ("photo",      "photos"),
    ("img",        "photos"),
    ("contract",   "contracts"),
    ("report",     "reports"),
    ("notes",      "notes"),
    ("backup",     "backups"),
]

STRATEGIES: list[str] = ["extension", "keyword", "year", "first_letter"]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MoveJob:
    """
    One proposed file-move into a new subfolder.

    Fields
    ──────
    record       : Source FileRecord.
    old_path     : Absolute path before move.
    subfolder    : Name of the new (or existing) subfolder, e.g. "pdf".
    new_dir      : Absolute path of the target directory.
    new_path     : Absolute path of the file after move.
    skip         : True → user opted this file out.
    conflict     : Non-empty string if the move cannot proceed.
    """
    record:    FileRecord
    old_path:  Path
    subfolder: str
    new_dir:   Path
    new_path:  Path
    skip:      bool = False
    conflict:  str  = ""

    @property
    def display_arrow(self) -> str:
        return f"{self.old_path.name}  →  {self.subfolder}/{self.old_path.name}"


@dataclass
class OrganiseResult:
    """Summary returned by apply_organise_plan()."""
    moved:   list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[MoveJob]           = field(default_factory=list)
    errors:  list[tuple[Path, str]]  = field(default_factory=list)
    created_dirs: list[Path]         = field(default_factory=list)

    @property
    def n_moved(self)        -> int: return len(self.moved)
    @property
    def n_skipped(self)      -> int: return len(self.skipped)
    @property
    def n_errors(self)       -> int: return len(self.errors)
    @property
    def n_created_dirs(self) -> int: return len(self.created_dirs)


# ── Plan builders (one per strategy) ─────────────────────────────────────────

def build_organise_plan(
    store:    FileStore,
    strategy: str,
    root:     str | Path,
) -> list[MoveJob]:
    """
    Build a list of MoveJob objects for all files in *store* that live
    directly inside *root* (non-recursive — only the files already in the
    top-level of root are candidates, to avoid recursing into already-
    organised subfolders unintentionally).

    Args:
        store    : The central FileStore.
        strategy : One of STRATEGIES.
        root     : The directory whose direct children will be organised.

    Returns:
        Sorted list of MoveJob (by subfolder then filename).

    Raises:
        ValueError if strategy is unknown.
    """
    root = Path(root).resolve()

    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}. Choose from {STRATEGIES}.")

    _dispatcher = {
        "extension":    _group_by_extension,
        "keyword":      _group_by_keyword,
        "year":         _group_by_year,
        "first_letter": _group_by_first_letter,
    }
    grouper = _dispatcher[strategy]

    jobs: list[MoveJob] = []

    for path_str, record in store.items():
        # Only files whose immediate parent IS root
        if record.path.parent.resolve() != root:
            continue

        subfolder = grouper(record)
        if subfolder is None:
            continue

        new_dir  = root / subfolder
        new_path = new_dir / record.path.name

        # Skip files already in the right place
        if record.path.parent == new_dir:
            continue

        jobs.append(MoveJob(
            record=record,
            old_path=record.path,
            subfolder=subfolder,
            new_dir=new_dir,
            new_path=new_path,
        ))

    jobs.sort(key=lambda j: (j.subfolder.lower(), j.old_path.name.lower()))
    return jobs


# ── Grouper functions (pure, return a subfolder name or None to skip) ─────────

def _group_by_extension(record: FileRecord) -> str | None:
    """Put each file into a folder named after its lowercase extension."""
    return record.extension.lower() if record.extension else "no_extension"


def _group_by_keyword(record: FileRecord) -> str | None:
    """
    Scan the filename stem for known keywords and return the canonical
    folder name for the first match.  Returns None if no keyword matches
    (file stays where it is).
    """
    stem_lower = record.path.stem.lower()
    words = set(re.split(r"[^a-z0-9]+", stem_lower)) - {""}
    for keyword, folder_name in KEYWORD_FOLDERS:
        if keyword in words:
            return folder_name
    return None


def _group_by_year(record: FileRecord) -> str | None:
    """Put each file into a folder named after its last-modified year."""
    return str(record.modified_at.year)


def _group_by_first_letter(record: FileRecord) -> str | None:
    """Put each file into a folder named after the first letter of its stem."""
    first = record.path.stem[:1].lower()
    return first if first.isalpha() else "_other"


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_organise_plan(jobs: list[MoveJob]) -> list[MoveJob]:
    """
    Annotate each MoveJob with a conflict description where needed.
    Mutates in-place; returns the same list.

    Checks:
      1. Source file still exists.
      2. Destination file already exists (would overwrite).
      3. Two jobs in this batch map to the same destination path.

    Time complexity: O(N) with a set for collision detection.
    """
    seen: set[Path] = set()

    for job in jobs:
        job.conflict = ""

        if not job.old_path.exists():
            job.conflict = "source file no longer exists"
            continue

        if job.new_path.exists():
            job.conflict = f"destination already exists in {job.subfolder}/"
            continue

        if job.new_path in seen:
            job.conflict = f"collision with another file in this batch → {job.subfolder}/"
            continue

        seen.add(job.new_path)

    return jobs


# ── Executor ──────────────────────────────────────────────────────────────────

def apply_organise_plan(jobs: list[MoveJob]) -> OrganiseResult:
    """
    Create subfolders and move files for all jobs where skip=False
    and conflict is empty.

    Directory creation uses mkdir(parents=True, exist_ok=True) so it is
    safe to call even if the folder was created by an earlier job in the
    same batch.

    shutil.move() is used instead of Path.rename() because the source and
    destination may be on different filesystems.

    Returns:
        OrganiseResult with moved, skipped, errors, and created_dirs lists.

    Time complexity: O(N) — one mkdir + one move per qualifying job.
    """
    result    = OrganiseResult()
    made_dirs: set[Path] = set()

    for job in jobs:
        if job.skip:
            result.skipped.append(job)
            log.debug("Skipped (user opted out): %s", job.old_path.name)
            continue

        if job.conflict:
            result.skipped.append(job)
            log.debug("Skipped (conflict): %s — %s", job.old_path.name, job.conflict)
            continue

        if not job.old_path.exists():
            result.errors.append((job.old_path, "file disappeared before move"))
            continue

        if job.new_path.exists():
            result.errors.append((job.old_path, f"destination appeared at write time: {job.new_path.name}"))
            continue

        try:
            job.new_dir.mkdir(parents=True, exist_ok=True)
            if job.new_dir not in made_dirs:
                made_dirs.add(job.new_dir)
                result.created_dirs.append(job.new_dir)

            shutil.move(str(job.old_path), str(job.new_path))
            result.moved.append((job.old_path, job.new_path))
            log.info("Moved: %s  →  %s/%s", job.old_path.name, job.subfolder, job.old_path.name)

        except OSError as exc:
            result.errors.append((job.old_path, str(exc)))
            log.error("Move failed for %s: %s", job.old_path, exc)

    log.info(
        "apply_organise_plan: moved=%d  skipped=%d  errors=%d  dirs_created=%d",
        result.n_moved, result.n_skipped, result.n_errors, result.n_created_dirs,
    )
    return result


# ── Summary helper ────────────────────────────────────────────────────────────

def plan_summary(jobs: list[MoveJob]) -> dict[str, int]:
    """
    Return a dict of subfolder_name → file_count for display.
    Only counts jobs without conflicts.
    O(N).
    """
    counts: dict[str, int] = {}
    for job in jobs:
        if not job.conflict:
            counts[job.subfolder] = counts.get(job.subfolder, 0) + 1
    return dict(sorted(counts.items()))