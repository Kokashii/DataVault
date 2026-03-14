"""
search_engine.py

Gmail-style query parser and search engine.

Supported filter syntax
───────────────────────
  type:pdf              files with extension "pdf"
  folder:docs           files whose parent folder is "docs"
  issue:duplicate       files with any issue starting with "duplicate"
  old:true              files with any "outdated:*" issue
  term:resume           files whose filename contains the token "resume"

Combine filters with spaces for implicit AND:
  type:pdf issue:duplicate
  folder:docs term:resume old:true

Why this beats linear scan
───────────────────────────
Linear scan: O(N·F) — check every file against every filter.
Index lookup: each filter resolves in O(1) to a set, then sets are
intersected smallest-first: O(F · |S_min|) where |S_min| << N.

With 100k files, 3 filters, smallest bucket size 120:
  Linear : 300,000 evaluations
  Index  : ~240 operations  (≈1,250× faster)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from indexer import FileIndex, tokenize
from models import FileRecord, FileStore

log = logging.getLogger(__name__)

VALID_PREFIXES: frozenset[str] = frozenset({"type", "folder", "issue", "old", "term"})

_FILTER_RE = re.compile(r"^([a-z]+):([a-zA-Z0-9_\-\.]+)$")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Filter:
    """A single parsed filter clause, e.g. Filter("type", "pdf")."""
    prefix: str
    value:  str


@dataclass(slots=True)
class ParsedQuery:
    filters:        list[Filter]
    unknown_tokens: list[str]

    @property
    def is_empty(self) -> bool:
        return not self.filters


@dataclass(slots=True)
class QueryResult:
    """Returned by search(). Bundles records with execution metadata."""
    records:        list[FileRecord]
    matched_paths:  set[str]
    filters_used:   list[Filter]
    set_sizes:      dict[str, int]
    unknown_tokens: list[str]


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_query(query: str) -> ParsedQuery:
    """
    Parse a space-separated query string into Filter objects.

    Time complexity: O(T) where T = number of whitespace-separated tokens.

    Examples:
        parse_query("type:pdf")
            → ParsedQuery(filters=[Filter("type","pdf")], unknown=[])

        parse_query("type:pdf issue:duplicate old:true")
            → ParsedQuery(filters=[...], unknown=[])

        parse_query("type:pdf unknown:foo bareword")
            → ParsedQuery(filters=[Filter("type","pdf")],
                          unknown_tokens=["unknown:foo", "bareword"])
    """
    filters:        list[Filter] = []
    unknown_tokens: list[str]   = []

    for raw in query.strip().lower().split():
        m = _FILTER_RE.match(raw)
        if m is None:
            unknown_tokens.append(raw)
            continue

        prefix, value = m.group(1), m.group(2)
        if prefix not in VALID_PREFIXES:
            unknown_tokens.append(raw)
            continue

        filters.append(Filter(prefix=prefix, value=value))

    return ParsedQuery(filters=filters, unknown_tokens=unknown_tokens)


# ── Filter resolver ───────────────────────────────────────────────────────────

def _resolve_filter(f: Filter, index: FileIndex) -> tuple[set[str], str]:
    """
    Map one Filter to a set of matching path strings via the index.
    Each case is O(1) except issue/old which scan bucket keys: O(B),
    B = distinct issue types (always a small constant).

    Returns (path_set, label_for_debug).
    """
    prefix, value = f.prefix, f.value
    label = f"{prefix}:{value}"

    if prefix == "type":
        return index.by_extension[value], label

    if prefix == "folder":
        return index.by_folder[value], label

    if prefix == "issue":
        # Prefix-match: "issue:duplicate" matches "duplicate:a1b2c3d4"
        matched: set[str] = set()
        for key, paths in index.by_issue.items():
            if key.startswith(value):
                matched |= paths
        return matched, label

    if prefix == "old":
        if value == "true":
            outdated: set[str] = set()
            for key, paths in index.by_issue.items():
                if key.startswith("outdated:"):
                    outdated |= paths
            return outdated, label
        else:
            all_paths = {p for paths in index.by_extension.values() for p in paths}
            outdated_paths: set[str] = set()
            for key, paths in index.by_issue.items():
                if key.startswith("outdated:"):
                    outdated_paths |= paths
            return all_paths - outdated_paths, label

    if prefix == "term":
        tokens = tokenize(value)
        if not tokens:
            return set(), label
        sets = [index.by_term[t] for t in tokens if t in index.by_term]
        if not sets:
            return set(), label
        sets.sort(key=len)
        result = sets[0].copy()
        for s in sets[1:]:
            result &= s
        return result, label

    log.warning("_resolve_filter: unhandled prefix %r", prefix)
    return set(), label


# ── Query executor ────────────────────────────────────────────────────────────

def execute_query(
    parsed: ParsedQuery,
    index: FileIndex,
) -> tuple[set[str], dict[str, int]]:
    """
    Execute a ParsedQuery using set intersection (smallest-set-first).

    Time complexity: O(F · |S_min|)
        F      = number of filters
        |S_min| = size of the smallest resolved set
    """
    if parsed.is_empty:
        return set(), {}

    resolved: list[tuple[set[str], str]] = [
        _resolve_filter(f, index) for f in parsed.filters
    ]

    set_sizes = {label: len(s) for s, label in resolved}

    resolved.sort(key=lambda pair: len(pair[0]))

    result, _ = resolved[0]
    result = result.copy()

    for candidate_set, label in resolved[1:]:
        result &= candidate_set
        if not result:
            log.debug("execute_query: short-circuit after %r", label)
            break

    return result, set_sizes


# ── Public API ────────────────────────────────────────────────────────────────

def search(
    query: str,
    index: FileIndex,
    store: FileStore,
) -> QueryResult:
    """
    Parse a query string, execute against the index, return matching records.

    Time complexity: O(T + F · |S_min| + |R|)
        T       = tokens in query string
        F       = valid filter count
        |S_min| = smallest resolved set
        |R|     = result count

    Example:
        result = search("type:pdf issue:duplicate", index, store)
        for rec in result.records:
            print(rec.path, rec.issues)
    """
    parsed = parse_query(query)

    if parsed.unknown_tokens:
        log.warning("search: ignoring unknown tokens: %s", parsed.unknown_tokens)

    if parsed.is_empty:
        return QueryResult(
            records=[], matched_paths=set(),
            filters_used=[], set_sizes={},
            unknown_tokens=parsed.unknown_tokens,
        )

    matched_paths, set_sizes = execute_query(parsed, index)
    records = index.get_records(matched_paths)

    log.info(
        "search(%r): %d filter(s) → %d result(s)  sizes=%s",
        query, len(parsed.filters), len(records), set_sizes,
    )

    return QueryResult(
        records=records,
        matched_paths=matched_paths,
        filters_used=parsed.filters,
        set_sizes=set_sizes,
        unknown_tokens=parsed.unknown_tokens,
    )