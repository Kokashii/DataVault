"""
app.py  —  DataVault · Hackathon UI

Run:  streamlit run app.py

Design: dark sidebar, bold stat cards, clean scannable table, inline action panels.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import streamlit as st

from hygiene_rules import apply_all_rules, build_hash_map
from indexer       import build_indexes
from models        import FileRecord, FileStore
from organiser     import (
    MoveJob, OrganiseResult, STRATEGIES,
    build_organise_plan, validate_organise_plan,
    apply_organise_plan, plan_summary,
)
from renamer       import RenameJob, RenameResult, build_rename_plan, validate_plan, apply_rename_plan
from scanner       import scan_directory
from search_engine import search
from suggestions   import (
    ActionKind,
    ActionSuggestion,
    format_suggestions,
    group_by_kind,
    suggest_for_store,
)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DataVault",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Syne:wght@700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'JetBrains Mono', monospace;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #0f1117 !important;
    border-right: 1px solid #1e2130;
}
[data-testid="stSidebar"] * { color: #c8cdd8 !important; }
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stCheckbox label {
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #4b5563 !important;
}
[data-testid="stSidebar"] input {
    background: #1a1d27 !important;
    border: 1px solid #2a2f40 !important;
    color: #e2e8f0 !important;
    border-radius: 6px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.8rem !important;
}
[data-testid="stSidebar"] .stButton > button {
    background: #3b82f6 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 6px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 700 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.05em;
    padding: 0.55rem 1rem !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #2563eb !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: #1a1d27 !important;
    border-color: #2a2f40 !important;
    border-radius: 6px !important;
    font-size: 0.78rem !important;
}

/* ── Stat cards ── */
.stat-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-bottom: 24px;
}
.stat-card {
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 18px 20px 14px;
    position: relative;
    overflow: hidden;
}
.stat-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent, #e5e7eb);
    border-radius: 10px 10px 0 0;
}
.stat-card.c-blue   { --accent: #3b82f6; }
.stat-card.c-red    { --accent: #ef4444; }
.stat-card.c-purple { --accent: #8b5cf6; }
.stat-card.c-amber  { --accent: #f59e0b; }
.stat-card.c-green  { --accent: #10b981; }
.stat-label {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #9ca3af;
    margin-bottom: 6px;
}
.stat-value {
    font-family: 'Syne', sans-serif;
    font-weight: 800;
    font-size: 2.1rem;
    line-height: 1;
    color: #111827;
}
.stat-sub {
    font-size: 0.66rem;
    color: #9ca3af;
    margin-top: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* ── Table ── */
.fh-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.77rem;
    font-family: 'JetBrains Mono', monospace;
}
.fh-table thead tr {
    background: #f9fafb;
    border-bottom: 2px solid #e5e7eb;
}
.fh-table thead th {
    padding: 10px 14px;
    text-align: left;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: #6b7280;
    white-space: nowrap;
}
.fh-table tbody tr {
    border-bottom: 1px solid #f3f4f6;
}
.fh-table tbody tr:hover { background: #f9fafb; }
.fh-table tbody tr.flagged-row { background: #fffbfb; }
.fh-table td {
    padding: 11px 14px;
    vertical-align: middle;
    color: #111827;
}
.fh-table td.dim   { color: #9ca3af; }
.fh-table td.fname { font-weight: 500; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fh-table td.path  { color: #9ca3af; font-size: 0.68rem; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Tags ── */
.tag {
    display: inline-block;
    font-size: 0.61rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    padding: 2px 7px;
    border-radius: 4px;
    margin-right: 3px;
    white-space: nowrap;
}
.tn  { background:#fef3c7; color:#92400e; }
.td  { background:#fee2e2; color:#991b1b; }
.tdu { background:#ede9fe; color:#5b21b6; }
.tm  { background:#d1fae5; color:#065f46; }
.tc  { background:#f0fdf4; color:#166534; }
.tun { background:#f3f4f6; color:#374151; }
.a-rename  { background:#dbeafe; color:#1e40af; }
.a-archive { background:#e0f2fe; color:#075985; }
.a-delete  { background:#fee2e2; color:#991b1b; }
.a-move    { background:#d1fae5; color:#065f46; }
.a-merge   { background:#ede9fe; color:#5b21b6; }
.a-ignore  { background:#f3f4f6; color:#6b7280; }
.a-review  { background:#fef3c7; color:#92400e; }

/* ── Section header ── */
.sec-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 20px 0 10px;
}
.sec-head h3 {
    font-family: 'Syne', sans-serif !important;
    font-size: 1rem !important;
    font-weight: 800 !important;
    color: #111827;
    margin: 0;
    letter-spacing: -0.02em;
}
.pill {
    background: #f3f4f6;
    color: #6b7280;
    font-size: 0.67rem;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 20px;
    letter-spacing: 0.03em;
}

/* ── Action panel ── */
.ap {
    background: #f8faff;
    border: 1px solid #dbeafe;
    border-radius: 8px;
    padding: 14px 18px;
    margin: 2px 0 10px;
    font-size: 0.75rem;
}
.ap-kind-label {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #6b7280;
    margin-bottom: 8px;
}
.ap-row {
    display: flex;
    gap: 10px;
    align-items: baseline;
    margin-bottom: 5px;
    line-height: 1.5;
}
.ap-reason { color: #374151; }
.ap-target { color: #1d4ed8; font-style: italic; }
.ap-conf   { color: #9ca3af; font-size: 0.66rem; font-family: 'JetBrains Mono', monospace; }

/* ── Hero (welcome) ── */
.hero { padding: 44px 0 28px; }
.hero-title {
    font-family: 'Syne', sans-serif;
    font-size: 2.6rem;
    font-weight: 800;
    color: #111827;
    letter-spacing: -0.04em;
    line-height: 1.1;
    margin-bottom: 12px;
}
.hero-title span { color: #3b82f6; }
.hero-sub { font-size: 0.8rem; color: #6b7280; line-height: 1.7; max-width: 520px; }
.feat-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    margin-top: 26px;
    max-width: 520px;
}
.feat {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 0.72rem;
    color: #374151;
    line-height: 1.5;
}
.feat strong { display: block; color: #111827; font-weight: 700; margin-bottom: 1px; }

hr.fh { border: none; border-top: 1px solid #f3f4f6; margin: 18px 0; }

/* ── Rename panel ── */
.rename-panel {
    background: #fffbeb;
    border: 1.5px solid #fcd34d;
    border-radius: 10px;
    padding: 20px 24px;
    margin: 20px 0 4px;
}
.rename-panel-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 4px;
}
.rename-panel-title {
    font-family: 'Syne', sans-serif;
    font-size: 1rem;
    font-weight: 800;
    color: #78350f;
    letter-spacing: -0.02em;
}
.rename-panel-sub {
    font-size: 0.7rem;
    color: #92400e;
    margin-bottom: 16px;
    line-height: 1.6;
}
.rename-row {
    display: grid;
    grid-template-columns: 1fr 24px 1fr 110px;
    align-items: center;
    gap: 10px;
    padding: 9px 12px;
    background: #fff;
    border: 1px solid #fde68a;
    border-radius: 6px;
    margin-bottom: 6px;
    font-size: 0.75rem;
}
.rename-row.conflict-row {
    background: #fff1f2;
    border-color: #fca5a5;
}
.rr-old  { color: #374151; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rr-arr  { color: #9ca3af; text-align: center; }
.rr-new  { color: #1d4ed8; font-style: italic; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rr-conf { color: #dc2626; font-size: 0.66rem; }

/* Result banner */
.result-banner {
    border-radius: 8px;
    padding: 14px 18px;
    margin: 12px 0;
    font-size: 0.78rem;
    line-height: 1.6;
}
.result-banner.success { background: #f0fdf4; border: 1px solid #86efac; color: #166534; }
.result-banner.warning { background: #fffbeb; border: 1px solid #fcd34d; color: #78350f; }
.result-banner.error   { background: #fef2f2; border: 1px solid #fca5a5; color: #991b1b; }

/* ── Organiser panel ── */
.org-panel {
    background: #f0fdf4;
    border: 1.5px solid #86efac;
    border-radius: 10px;
    padding: 20px 24px;
    margin: 20px 0 4px;
}
.org-panel-title {
    font-family: 'Syne', sans-serif;
    font-size: 1rem;
    font-weight: 800;
    color: #14532d;
    letter-spacing: -0.02em;
    margin-bottom: 4px;
}
.org-panel-sub {
    font-size: 0.7rem;
    color: #166534;
    margin-bottom: 0;
    line-height: 1.6;
}
/* Subfolder group heading inside preview */
.org-group-head {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: #166534;
    background: #dcfce7;
    border: 1px solid #86efac;
    border-radius: 5px 5px 0 0;
    padding: 5px 12px;
    margin-top: 10px;
    margin-bottom: 0;
}
.org-row {
    display: grid;
    grid-template-columns: 1fr 20px 1fr 80px;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    background: #fff;
    border: 1px solid #bbf7d0;
    border-top: none;
    font-size: 0.75rem;
}
.org-row:last-child { border-radius: 0 0 5px 5px; }
.org-row.conflict-row { background: #fff1f2; border-color: #fca5a5; }
.or-old  { color: #374151; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.or-arr  { color: #9ca3af; text-align: center; }
.or-new  { color: #15803d; font-style: italic; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.or-conf { color: #dc2626; font-size: 0.65rem; }

/* Strategy pills */
.strategy-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: pointer;
    border: 1px solid #e5e7eb;
    background: #f9fafb;
    color: #374151;
    margin-right: 6px;
}
.strategy-pill.active {
    background: #166534;
    color: #fff;
    border-color: #166534;
}

/* ── Custom tab bar ── */
.tab-bar-radio > label {
    display: inline-flex !important;
    align-items: center;
    padding: 8px 20px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    color: #6b7280 !important;
    background: #f9fafb !important;
    border: 1px solid #e5e7eb !important;
    border-bottom: none !important;
    border-radius: 8px 8px 0 0 !important;
    margin-right: 4px !important;
    cursor: pointer;
    transition: background 0.12s, color 0.12s;
}
.tab-bar-radio > label:has(input:checked) {
    background: #ffffff !important;
    color: #111827 !important;
    border-color: #d1d5db !important;
    font-weight: 700 !important;
}
.tab-bar-radio > label:hover:not(:has(input:checked)) {
    background: #f3f4f6 !important;
    color: #374151 !important;
}
.tab-bar-radio input[type="radio"] { display: none !important; }
.tab-content-box {
    border: 1px solid #e5e7eb;
    border-radius: 0 8px 8px 8px;
    padding: 22px 24px;
    background: #ffffff;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────

for _k, _v in [
    ("store", None), ("index", None), ("action_map", None),
    ("scan_stats", None), ("scanned", False),
    ("active_tab",     "Files & actions"),   # persisted tab selection
    ("rename_plan",    None),
    ("rename_result",  None),
    ("rename_skips",   {}),
    ("rename_page",    1),
    ("rename_cf_page", 1),
    ("org_plan",       None),
    ("org_result",     None),
    ("org_skips",      {}),
    ("org_strategy",   "extension"),
    ("org_page",       1),
    ("org_cf_page",    1),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Cached scan pipeline ──────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _run_scan(directory: str, skip_hidden: bool, max_age_days: int):
    store, stats = scan_directory(directory, skip_hidden=skip_hidden)
    hmap = build_hash_map(store)
    apply_all_rules(store, rule_config={
        "outdated":  {"max_age_days": max_age_days},
        "duplicate": {"hash_map": hmap},
    })
    index      = build_indexes(store)
    action_map = suggest_for_store(store)
    return store, index, action_map, stats


def _do_scan(directory: str, skip_hidden: bool, max_age_days: int) -> None:
    try:
        store, index, action_map, stats = _run_scan(directory, skip_hidden, max_age_days)
        st.session_state.update(
            store=store, index=index, action_map=action_map,
            scan_stats=stats, scanned=True,
            rename_plan=None, rename_result=None, rename_skips={},
            rename_page=1, rename_cf_page=1,
            org_plan=None, org_result=None, org_skips={},
            org_page=1, org_cf_page=1,
        )
    except FileNotFoundError:
        st.error(f"Directory not found: `{directory}`")
    except NotADirectoryError:
        st.error(f"Not a directory: `{directory}`")
    except PermissionError:
        st.error(f"Permission denied: `{directory}`")
    except Exception as exc:
        st.error(f"Scan failed: {exc}")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(
        "<div style='padding:6px 0 18px'>"
        "<span style='font-family:Syne,sans-serif;font-size:1.05rem;font-weight:800;"
        "color:#e2e8f0;letter-spacing:-0.02em'>⬡ DataVault</span><br>"
        "<span style='font-size:0.65rem;color:#374151;letter-spacing:0.07em;"
        "text-transform:uppercase'>AI-powered hygiene tool</span></div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div style='font-size:0.65rem;font-weight:700;letter-spacing:0.1em;"
        "text-transform:uppercase;color:#374151;margin-bottom:5px'>Directory</div>",
        unsafe_allow_html=True,
    )
    directory = st.text_input(
        "dir", value=str(Path.home()),
        placeholder="/Users/you/Documents",
        label_visibility="collapsed",
    )

    with st.expander("⚙ Options"):
        skip_hidden  = st.checkbox("Skip hidden files", value=True)
        max_age_days = st.slider(
            "Flag files older than (days)",
            min_value=30, max_value=1825, value=365, step=30,
        )

    if st.button("▶  Run scan", use_container_width=True, type="primary"):
        if directory.strip():
            with st.spinner("Scanning…"):
                _do_scan(directory.strip(), skip_hidden, max_age_days)

    # Filters — only after scan
    filter_type = filter_folder = filter_issue = "(all)"
    show_flagged_only = True

    if st.session_state.scanned:
        idx = st.session_state.index
        st.markdown("<hr style='border-color:#1e2130;margin:16px 0'/>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.65rem;font-weight:700;letter-spacing:0.1em;"
            "text-transform:uppercase;color:#374151;margin-bottom:10px'>Filters</div>",
            unsafe_allow_html=True,
        )
        filter_type       = st.selectbox("File type",  ["(all)"] + sorted(k for k in idx.by_extension if k != "__none__"))
        filter_folder     = st.selectbox("Folder",     ["(all)"] + sorted(idx.by_folder.keys()))
        filter_issue      = st.selectbox("Issue type", ["(all)"] + sorted({i.split(":")[0] for i in idx.by_issue}))
        show_flagged_only = st.checkbox("Flagged files only", value=True)

        st.markdown("<hr style='border-color:#1e2130;margin:16px 0'/>", unsafe_allow_html=True)
        ms = st.session_state.scan_stats.get("duration_ms", 0)
        st.markdown(
            f"<div style='font-size:0.68rem;color:#374151'>"
            f"Scanned in <strong style='color:#9ca3af'>{ms:.0f} ms</strong></div>",
            unsafe_allow_html=True,
        )

# ── Welcome screen ────────────────────────────────────────────────────────────

if not st.session_state.scanned:
    st.markdown("""
<div class="hero">
  <div class="hero-title">Keep your files<br><span>clean &amp; organised.</span></div>
  <div class="hero-sub">
    Point it at any directory. It scans every file, detects hygiene issues,
    and surfaces suggested actions — all in memory, no database needed.
  </div>
  <div class="feat-grid">
    <div class="feat"><strong>Poor naming</strong>Spaces, repeated words, bad caps</div>
    <div class="feat"><strong>Duplicates</strong>Exact matches via SHA-256</div>
    <div class="feat"><strong>Outdated files</strong>Untouched beyond N days</div>
    <div class="feat"><strong>Misplaced files</strong>Wrong folder for file type</div>
  </div>
</div>
<div style="font-size:0.72rem;color:#9ca3af;margin-top:8px;font-family:'JetBrains Mono',monospace">
  <strong style="color:#6b7280">Search:</strong> &nbsp;
  <code>type:pdf</code> &nbsp; <code>folder:docs</code> &nbsp;
  <code>issue:duplicate</code> &nbsp; <code>old:true</code> &nbsp;
  <code>term:resume</code>
</div>
""", unsafe_allow_html=True)
    st.stop()

# ── Unpack state ──────────────────────────────────────────────────────────────

store      = st.session_state.store
index      = st.session_state.index
action_map = st.session_state.action_map
stats      = st.session_state.scan_stats

# ── Derived metrics ───────────────────────────────────────────────────────────

total     = len(store)
flagged   = sum(1 for r in store.values() if r.issues)
clean     = total - flagged
dup_count = sum(1 for r in store.values() if any(i.startswith("duplicate:") for i in r.issues))
old_count = sum(1 for r in store.values() if any(i.startswith("outdated:")  for i in r.issues))
health    = int(clean / max(total, 1) * 100)
h_color   = "#10b981" if health >= 80 else "#f59e0b" if health >= 50 else "#ef4444"
root_short = str(stats.get("root",""))
if len(root_short) > 38:
    root_short = "…" + root_short[-37:]

# ── Stat cards ────────────────────────────────────────────────────────────────

st.markdown(f"""
<div class="stat-grid">
  <div class="stat-card c-blue">
    <div class="stat-label">Files scanned</div>
    <div class="stat-value">{total:,}</div>
    <div class="stat-sub" title="{stats.get('root','')}">{root_short}</div>
  </div>
  <div class="stat-card c-red">
    <div class="stat-label">Flagged</div>
    <div class="stat-value">{flagged:,}</div>
    <div class="stat-sub">{flagged/max(total,1)*100:.0f}% of total</div>
  </div>
  <div class="stat-card c-purple">
    <div class="stat-label">Duplicates</div>
    <div class="stat-value">{dup_count:,}</div>
    <div class="stat-sub">exact content matches</div>
  </div>
  <div class="stat-card c-amber">
    <div class="stat-label">Outdated</div>
    <div class="stat-value">{old_count:,}</div>
    <div class="stat-sub">not touched in {max_age_days}d</div>
  </div>
  <div class="stat-card c-green">
    <div class="stat-label">Health score</div>
    <div class="stat-value" style="color:{h_color}">{health}%</div>
    <div class="stat-sub">{clean:,} clean · {flagged:,} need attention</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Search bar ────────────────────────────────────────────────────────────────

sc, hc = st.columns([3, 2])
with sc:
    query_input = st.text_input(
        "search", label_visibility="collapsed",
        placeholder="🔍  type:pdf   issue:duplicate   folder:docs   term:resume   old:true",
    )
with hc:
    st.markdown(
        "<div style='padding-top:9px;font-size:0.68rem;color:#9ca3af'>"
        "Gmail-style &nbsp;·&nbsp; space = AND</div>",
        unsafe_allow_html=True,
    )

# ── Build display records ─────────────────────────────────────────────────────

def _get_display_records() -> list[FileRecord]:
    candidates: set[str] | None = None

    if query_input.strip():
        result = search(query_input.strip(), index, store)
        candidates = result.matched_paths
        if result.unknown_tokens:
            st.warning(f"Ignored unknown tokens: {result.unknown_tokens}")

    if filter_type != "(all)":
        b = index.by_extension[filter_type]
        candidates = b if candidates is None else candidates & b

    if filter_folder != "(all)":
        b = index.by_folder[filter_folder]
        candidates = b if candidates is None else candidates & b

    if filter_issue != "(all)":
        ip: set[str] = set()
        for k, paths in index.by_issue.items():
            if k.startswith(filter_issue):
                ip |= paths
        candidates = ip if candidates is None else candidates & ip

    records = (
        [store[p] for p in candidates if p in store]
        if candidates is not None else list(store.values())
    )

    if show_flagged_only and not query_input.strip():
        records = [r for r in records if r.issues]

    records.sort(key=lambda r: (not bool(r.issues), r.name.lower()))
    return records


display_records = _get_display_records()

# ── Tag helpers ───────────────────────────────────────────────────────────────

_ISSUE_CLS = {
    "poor_name": ("naming",    "tn"),
    "outdated":  ("outdated",  "td"),
    "duplicate": ("duplicate", "tdu"),
    "misplaced": ("misplaced", "tm"),
}

_ACTION_CLS: dict[ActionKind, tuple[str, str]] = {
    ActionKind.RENAME:  ("rename",  "a-rename"),
    ActionKind.ARCHIVE: ("archive", "a-archive"),
    ActionKind.DELETE:  ("delete",  "a-delete"),
    ActionKind.MOVE:    ("move",    "a-move"),
    ActionKind.MERGE:   ("merge",   "a-merge"),
    ActionKind.IGNORE:  ("ignore",  "a-ignore"),
    ActionKind.REVIEW:  ("review",  "a-review"),
}


def _issue_tags(r: FileRecord) -> str:
    if not r.issues:
        return '<span class="tag tc">clean</span>'
    seen: set[str] = set()
    out = []
    for issue in r.issues:
        p = issue.split(":")[0]
        if p not in seen:
            lbl, cls = _ISSUE_CLS.get(p, (p, "tun"))
            out.append(f'<span class="tag {cls}">{lbl}</span>')
            seen.add(p)
    return "".join(out)


def _top_action(r: FileRecord) -> str:
    sug = action_map.get(r.path_str, [])
    if not sug:
        return "—"
    best = max(
        (s for s in sug if s.kind != ActionKind.IGNORE),
        key=lambda s: s.confidence,
        default=sug[0],
    )
    lbl, cls = _ACTION_CLS.get(best.kind, (best.kind.value, "a-ignore"))
    return f'<span class="tag {cls}">{lbl}</span>'


def _fmt_size(r: FileRecord) -> str:
    if r.size_mb >= 1:  return f"{r.size_mb:.1f} MB"
    if r.size_kb >= 1:  return f"{r.size_kb:.0f} KB"
    return f"{r.size_bytes} B"

# ── Section header ────────────────────────────────────────────────────────────

active = []
if query_input.strip():       active.append(f'"{query_input.strip()}"')
if filter_type   != "(all)":  active.append(f"type:{filter_type}")
if filter_folder != "(all)":  active.append(f"folder:{filter_folder}")
if filter_issue  != "(all)":  active.append(f"issue:{filter_issue}")
if show_flagged_only and not query_input.strip(): active.append("flagged only")
filter_label = " · ".join(active) if active else "all files"

st.markdown(f"""
<div class="sec-head">
  <h3>Flagged files</h3>
  <span class="pill">{len(display_records):,} &nbsp;·&nbsp; {filter_label}</span>
</div>
""", unsafe_allow_html=True)

# ── Empty state ───────────────────────────────────────────────────────────────

if not display_records:
    st.markdown("""
<div style="text-align:center;padding:52px 0;color:#9ca3af">
  <div style="font-size:2rem;margin-bottom:10px">✓</div>
  <div style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:800;
       color:#374151;margin-bottom:5px">Nothing to show</div>
  <div style="font-size:0.73rem">Adjust the filters or uncheck "Flagged files only".</div>
</div>
""", unsafe_allow_html=True)
    st.stop()

# ── Paginate ──────────────────────────────────────────────────────────────────

PAGE_SIZE   = 50
total_pages = max(1, (len(display_records) + PAGE_SIZE - 1) // PAGE_SIZE)
page = 1
if total_pages > 1:
    page = st.number_input(
        f"Page (1–{total_pages})", min_value=1, max_value=total_pages, value=1, step=1
    )
start    = (page - 1) * PAGE_SIZE
page_rec = display_records[start : start + PAGE_SIZE]

# ── Table ─────────────────────────────────────────────────────────────────────

rows = []
for r in page_rec:
    rc = "flagged-row" if r.issues else ""
    rows.append(f"""
<tr class="{rc}">
  <td class="fname">{r.name}</td>
  <td class="dim">{r.extension.upper() if r.extension else "—"}</td>
  <td class="dim">{_fmt_size(r)}</td>
  <td class="dim">{r.parent_folder}</td>
  <td class="dim">{r.age_days}d</td>
  <td>{_issue_tags(r)}</td>
  <td>{_top_action(r)}</td>
</tr>""")

st.markdown(f"""
<table class="fh-table">
  <thead>
    <tr>
      <th>Filename</th>
      <th>Type</th>
      <th>Size</th>
      <th>Folder</th>
      <th>Age</th>
      <th>Issues</th>
      <th>Top action</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT TAB BAR
# Uses st.radio (hidden inputs, styled as tabs) so the selected tab is stored
# in session_state and survives every rerun triggered by Prev/Next buttons.
# st.tabs() resets to tab 0 on every rerun — this does not.
# ═══════════════════════════════════════════════════════════════════════════════

rename_plan_count = len(st.session_state.rename_plan or [])
org_plan_count    = len(st.session_state.org_plan    or [])
rename_badge      = f" ({rename_plan_count})" if rename_plan_count else ""
org_badge         = f" ({org_plan_count})"    if org_plan_count    else ""

TAB_OPTIONS = [
    "Files & actions",
    f"Rename{rename_badge}",
    f"Organise{org_badge}",
]

active_tab = st.radio(
    "tab",
    options=TAB_OPTIONS,
    index=next(
        (i for i, t in enumerate(TAB_OPTIONS)
         if t.startswith(st.session_state.active_tab.split(" (")[0])),
        0,
    ),
    horizontal=True,
    label_visibility="collapsed",
    key="tab_radio",
)
st.session_state.active_tab = active_tab.split(" (")[0]   # strip badge before storing

st.markdown('<div class="tab-content-box">', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Flagged files table + suggested action expanders
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state.active_tab == "Files & actions":

    # ── Suggested actions expanders ───────────────────────────────────────────

    flagged_page = [r for r in page_rec if r.issues and r.path_str in action_map]

    if flagged_page:
        st.markdown(f"""
<div class="sec-head">
  <h3>Suggested actions</h3>
  <span class="pill">{len(flagged_page)} files on this page</span>
</div>
""", unsafe_allow_html=True)

        for rec in flagged_page:
            suggestions = action_map[rec.path_str]
            grouped     = group_by_kind(suggestions)
            issue_label = " · ".join(sorted({i.split(":")[0] for i in rec.issues}))

            with st.expander(f"  {rec.name}   [{issue_label}]"):
                st.markdown(f"""
<div style="font-size:0.69rem;color:#6b7280;margin-bottom:14px;line-height:2">
  <span style="color:#d1d5db">path</span>&ensp;{rec.path}<br>
  <span style="color:#d1d5db">size</span>&ensp;{_fmt_size(rec)}&emsp;
  <span style="color:#d1d5db">modified</span>&ensp;{rec.modified_at.strftime('%Y-%m-%d')}&emsp;
  <span style="color:#d1d5db">folder</span>&ensp;{rec.parent_folder}
</div>
""", unsafe_allow_html=True)

                for kind, items in grouped.items():
                    lbl, cls = _ACTION_CLS.get(kind, (kind.value, "a-ignore"))
                    action_rows_html = []
                    for s in items:
                        target_html = ""
                        if s.proposed_name:
                            target_html = f'<span class="ap-target">→ {s.proposed_name}</span>'
                        elif s.proposed_path:
                            target_html = f'<span class="ap-target">→ {s.proposed_path}</span>'
                        filled = int(s.confidence * 8)
                        bar    = "█" * filled + "░" * (8 - filled)
                        action_rows_html.append(f"""
<div class="ap-row">
  <span class="ap-reason">{s.reason}</span>
  {target_html}
  <span class="ap-conf">{bar}&nbsp;{s.confidence:.0%}</span>
</div>""")

                    st.markdown(f"""
<div class="ap">
  <div class="ap-kind-label"><span class="tag {cls}">{lbl}</span></div>
  {"".join(action_rows_html)}
</div>
""", unsafe_allow_html=True)
    else:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9ca3af;padding:16px 0'>"
            "No flagged files on this page.</div>",
            unsafe_allow_html=True,
        )

    # ── Pagination footer ─────────────────────────────────────────────────────

    if total_pages > 1:
        st.markdown(
            f"<div style='text-align:center;font-size:0.69rem;color:#9ca3af;margin-top:14px'>"
            f"Showing {start+1}–{min(start+PAGE_SIZE, len(display_records)):,}"
            f" of {len(display_records):,}"
            f"</div>",
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Rename
# ─────────────────────────────────────────────────────────────────────────────

elif st.session_state.active_tab == "Rename":

    st.markdown("""
<div class="rename-panel">
  <div class="rename-panel-title">⟳ Apply rename suggestions</div>
  <div class="rename-panel-sub" style="margin-top:4px">
    Preview every rename before committing. Uncheck files you want to keep as-is.
    No files are touched until you click <strong>Apply</strong>.
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Step 1: Build plan ────────────────────────────────────────────────────

    rp_col, _ = st.columns([2, 5])
    with rp_col:
        if st.button("🔍  Preview rename suggestions", use_container_width=True, key="rn_preview_btn"):
            with st.spinner("Building rename plan…"):
                _plan = build_rename_plan(store, action_map)
                validate_plan(_plan)
                st.session_state.rename_plan   = _plan
                st.session_state.rename_result = None
                st.session_state.rename_skips  = {str(j.old_path): False for j in _plan}
                st.session_state.rename_page   = 1

    plan: list[RenameJob] | None = st.session_state.rename_plan

    if plan is None:
        st.markdown(
            "<div style='font-size:0.72rem;color:#9ca3af;padding:8px 0'>"
            "Click <em>Preview</em> to see all files with rename suggestions.</div>",
            unsafe_allow_html=True,
        )
    elif not plan:
        st.markdown("""
<div class="result-banner success">No rename suggestions — all filenames look clean!</div>
""", unsafe_allow_html=True)
    else:
        # ── Step 2: Paged review table ────────────────────────────────────────

        skips: dict[str, bool] = st.session_state.rename_skips
        clear_jobs    = [j for j in plan if not j.conflict]
        conflict_jobs = [j for j in plan if j.conflict]

        # Pagination for rename rows
        RN_PAGE = 50
        rn_total_pages = max(1, (len(clear_jobs) + RN_PAGE - 1) // RN_PAGE)
        if "rename_page" not in st.session_state:
            st.session_state.rename_page = 1
        rn_page = st.session_state.rename_page

        st.markdown(f"""
<div class="sec-head" style="margin-top:16px">
  <h3>Rename preview</h3>
  <span class="pill">
    {len(clear_jobs)} actionable &nbsp;·&nbsp;
    {len(conflict_jobs)} conflict{'s' if len(conflict_jobs) != 1 else ''}
    &nbsp;·&nbsp; page {rn_page}/{rn_total_pages}
  </span>
</div>
""", unsafe_allow_html=True)

        # Bulk select/deselect + page navigation on one row
        rn_sa, rn_ds, rn_prev, rn_next, _ = st.columns([1, 1, 1, 1, 4])
        with rn_sa:
            if st.button("Select all", use_container_width=True, key="rn_sa"):
                for j in clear_jobs:
                    st.session_state.rename_skips[str(j.old_path)] = False
                st.rerun()
        with rn_ds:
            if st.button("Deselect all", use_container_width=True, key="rn_ds"):
                for j in clear_jobs:
                    st.session_state.rename_skips[str(j.old_path)] = True
                st.rerun()
        with rn_prev:
            if st.button("← Prev", use_container_width=True, key="rn_prev",
                         disabled=(rn_page <= 1)):
                st.session_state.rename_page = rn_page - 1
                st.rerun()
        with rn_next:
            if st.button("Next →", use_container_width=True, key="rn_next",
                         disabled=(rn_page >= rn_total_pages)):
                st.session_state.rename_page = rn_page + 1
                st.rerun()

        rn_start = (rn_page - 1) * RN_PAGE
        rn_slice = clear_jobs[rn_start : rn_start + RN_PAGE]

        if rn_slice:
            st.markdown(
                "<div style='font-size:0.62rem;font-weight:700;letter-spacing:0.08em;"
                "text-transform:uppercase;color:#6b7280;margin:8px 0 4px'>"
                f"Actionable renames — showing {rn_start+1}–"
                f"{min(rn_start+RN_PAGE, len(clear_jobs))} of {len(clear_jobs)}"
                "</div>",
                unsafe_allow_html=True,
            )
            for job in rn_slice:
                key = str(job.old_path)
                cb_col, row_col = st.columns([1, 11])
                with cb_col:
                    checked = not skips.get(key, False)
                    new_val = st.checkbox(
                        "include", value=checked,
                        key=f"cb_{key}", label_visibility="collapsed",
                    )
                    st.session_state.rename_skips[key] = not new_val
                with row_col:
                    st.markdown(f"""
<div class="rename-row">
  <div class="rr-old" title="{job.old_path}">{job.old_path.name}</div>
  <div class="rr-arr">→</div>
  <div class="rr-new" title="{job.new_path}">{job.new_name}</div>
  <div style="font-size:0.66rem;color:#9ca3af">{job.suggestion.confidence:.0%}</div>
</div>
""", unsafe_allow_html=True)

        # Conflicts section (collapsible, all pages)
        if conflict_jobs:
            with st.expander(f"⚠  {len(conflict_jobs)} conflict{'s' if len(conflict_jobs)!=1 else ''} — will be skipped automatically"):
                # Paginate conflicts too
                CF_PAGE = 50
                cf_total = max(1, (len(conflict_jobs) + CF_PAGE - 1) // CF_PAGE)
                if "rename_cf_page" not in st.session_state:
                    st.session_state.rename_cf_page = 1
                cf_page = st.session_state.rename_cf_page
                cf_start = (cf_page - 1) * CF_PAGE

                cfa_col, cfb_col, _ = st.columns([1, 1, 6])
                with cfa_col:
                    if st.button("← Prev", key="cf_prev", use_container_width=True,
                                 disabled=(cf_page <= 1)):
                        st.session_state.rename_cf_page -= 1; st.rerun()
                with cfb_col:
                    if st.button("Next →", key="cf_next", use_container_width=True,
                                 disabled=(cf_page >= cf_total)):
                        st.session_state.rename_cf_page += 1; st.rerun()

                st.markdown(
                    f"<div style='font-size:0.62rem;color:#9ca3af;margin:4px 0 6px'>"
                    f"Page {cf_page}/{cf_total}</div>",
                    unsafe_allow_html=True,
                )
                for job in conflict_jobs[cf_start : cf_start + CF_PAGE]:
                    st.markdown(f"""
<div class="rename-row conflict-row">
  <div class="rr-old">{job.old_path.name}</div>
  <div class="rr-arr">→</div>
  <div class="rr-new">{job.new_name}</div>
  <div class="rr-conf">{job.conflict}</div>
</div>
""", unsafe_allow_html=True)

        # ── Step 3: Apply button ──────────────────────────────────────────────

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        will_apply = sum(
            1 for j in clear_jobs
            if not st.session_state.rename_skips.get(str(j.old_path), False)
        )
        ap_col, _ = st.columns([2, 5])
        with ap_col:
            if st.button(
                f"✏  Apply {will_apply} rename{'s' if will_apply!=1 else ''}",
                use_container_width=True, type="primary",
                disabled=(will_apply == 0), key="rn_apply_btn",
            ):
                for job in plan:
                    job.skip = st.session_state.rename_skips.get(str(job.old_path), False)
                with st.spinner(f"Renaming {will_apply} file(s)…"):
                    result = apply_rename_plan(plan)
                st.session_state.rename_result = result
                _run_scan.clear()
                st.session_state.rename_plan  = None
                st.session_state.rename_skips = {}
                st.session_state.rename_page  = 1
                st.rerun()

    # ── Step 4: Result banner ─────────────────────────────────────────────────

    result: RenameResult | None = st.session_state.rename_result
    if result is not None:
        if result.n_applied > 0:
            st.markdown(f"""
<div class="result-banner success">
  <strong>✓ {result.n_applied} file{'s' if result.n_applied!=1 else ''} renamed.</strong>&nbsp;
  {result.n_skipped} skipped · {result.n_errors} error{'s' if result.n_errors!=1 else ''}.
  Run a fresh scan to update the table.
</div>
""", unsafe_allow_html=True)
        if result.n_errors > 0:
            err_li = "".join(f"<li><code>{p.name}</code> — {m}</li>" for p, m in result.errors)
            st.markdown(f"""
<div class="result-banner error">
  <strong>⚠ {result.n_errors} rename{'s' if result.n_errors!=1 else ''} failed:</strong>
  <ul style="margin:6px 0 0;padding-left:18px">{err_li}</ul>
</div>
""", unsafe_allow_html=True)
        if result.n_applied > 0:
            with st.expander("Rename log"):
                for old, new in result.applied:
                    st.markdown(
                        f"`{old.name}` &nbsp;→&nbsp; `{new.name}`  "
                        f"<span style='font-size:0.68rem;color:#9ca3af'>({old.parent})</span>",
                        unsafe_allow_html=True,
                    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Subfolder organiser
# ─────────────────────────────────────────────────────────────────────────────

elif st.session_state.active_tab == "Organise":

    st.markdown("""
<div class="org-panel">
  <div class="org-panel-title">📁 Subfolder organiser</div>
  <div class="org-panel-sub" style="margin-top:4px">
    Group files into new subfolders by a chosen strategy.
    Preview every move before committing — nothing is touched until
    you click <strong>Apply</strong>.
  </div>
</div>
""", unsafe_allow_html=True)

    enable_org = st.toggle("Enable subfolder organiser", value=False, key="enable_org")

    if not enable_org:
        st.markdown(
            "<div style='font-size:0.72rem;color:#9ca3af;padding:4px 0 8px'>"
            "Toggle on to configure and preview the organiser.</div>",
            unsafe_allow_html=True,
        )
    else:
        STRATEGY_LABELS = {
            "extension":    "By file type — pdf/  docx/  png/ …",
            "keyword":      "By keyword — resume/  invoices/  photos/ …",
            "year":         "By year — 2021/  2022/  2023/ …",
            "first_letter": "Alphabetical — a/  b/  c/ …",
        }

        st.markdown(
            "<div style='font-size:0.65rem;font-weight:700;letter-spacing:0.09em;"
            "text-transform:uppercase;color:#374151;margin:14px 0 6px'>Grouping strategy</div>",
            unsafe_allow_html=True,
        )
        chosen_strategy = st.selectbox(
            "Strategy", options=STRATEGIES,
            format_func=lambda s: STRATEGY_LABELS[s],
            index=STRATEGIES.index(st.session_state.org_strategy),
            label_visibility="collapsed", key="org_strategy_select",
        )
        st.session_state.org_strategy = chosen_strategy

        scanned_root = stats.get("root", str(Path.home()))
        st.markdown(
            "<div style='font-size:0.65rem;font-weight:700;letter-spacing:0.09em;"
            "text-transform:uppercase;color:#374151;margin:10px 0 4px'>Target root</div>",
            unsafe_allow_html=True,
        )
        org_root = st.text_input(
            "org root", value=scanned_root, label_visibility="collapsed",
            key="org_root_input",
            help="Only files whose immediate parent is this directory will be moved.",
        )

        # ── Step 1: Preview button ────────────────────────────────────────────

        op_col, _ = st.columns([2, 5])
        with op_col:
            if st.button("🔍  Preview subfolder plan", use_container_width=True, key="org_preview_btn"):
                with st.spinner("Building organise plan…"):
                    try:
                        _org_jobs = build_organise_plan(store, chosen_strategy, org_root)
                        validate_organise_plan(_org_jobs)
                        st.session_state.org_plan   = _org_jobs
                        st.session_state.org_result = None
                        st.session_state.org_skips  = {str(j.old_path): False for j in _org_jobs}
                        st.session_state.org_page   = 1
                    except Exception as exc:
                        st.error(f"Could not build plan: {exc}")

        org_plan: list[MoveJob] | None = st.session_state.org_plan

        if org_plan is None:
            st.markdown(
                "<div style='font-size:0.72rem;color:#9ca3af;padding:6px 0'>"
                "Click <em>Preview</em> to see how files will be grouped.</div>",
                unsafe_allow_html=True,
            )
        elif not org_plan:
            st.markdown("""
<div class="result-banner success">
  No files in the root directory need organising with this strategy.
</div>
""", unsafe_allow_html=True)
        else:
            # ── Step 2: Paged preview grouped by subfolder ────────────────────

            org_skips: dict[str, bool] = st.session_state.org_skips
            org_clear    = [j for j in org_plan if not j.conflict]
            org_conflict = [j for j in org_plan if j.conflict]
            summary      = plan_summary(org_plan)

            # Pagination
            ORG_PAGE = 50
            org_total_pages = max(1, (len(org_clear) + ORG_PAGE - 1) // ORG_PAGE)
            if "org_page" not in st.session_state:
                st.session_state.org_page = 1
            org_page = st.session_state.org_page

            st.markdown(f"""
<div class="sec-head" style="margin-top:16px">
  <h3>Subfolder preview</h3>
  <span class="pill">
    {len(org_clear)} files &nbsp;·&nbsp; {len(summary)} folder{'s' if len(summary)!=1 else ''}
    &nbsp;·&nbsp; {len(org_conflict)} conflict{'s' if len(org_conflict)!=1 else ''}
    &nbsp;·&nbsp; page {org_page}/{org_total_pages}
  </span>
</div>
""", unsafe_allow_html=True)

            # Bulk + page nav
            os_sa, os_ds, os_prev, os_next, _ = st.columns([1, 1, 1, 1, 4])
            with os_sa:
                if st.button("Select all", use_container_width=True, key="org_sa"):
                    for j in org_clear:
                        st.session_state.org_skips[str(j.old_path)] = False
                    st.rerun()
            with os_ds:
                if st.button("Deselect all", use_container_width=True, key="org_ds"):
                    for j in org_clear:
                        st.session_state.org_skips[str(j.old_path)] = True
                    st.rerun()
            with os_prev:
                if st.button("← Prev", use_container_width=True, key="org_prev",
                             disabled=(org_page <= 1)):
                    st.session_state.org_page = org_page - 1; st.rerun()
            with os_next:
                if st.button("Next →", use_container_width=True, key="org_next",
                             disabled=(org_page >= org_total_pages)):
                    st.session_state.org_page = org_page + 1; st.rerun()

            # Slice the clear jobs for this page
            org_start = (org_page - 1) * ORG_PAGE
            org_slice = org_clear[org_start : org_start + ORG_PAGE]

            # Group the page slice by subfolder
            from collections import defaultdict as _dd
            by_folder: dict[str, list[MoveJob]] = _dd(list)
            for j in org_slice:
                by_folder[j.subfolder].append(j)

            for folder_name, folder_jobs in sorted(by_folder.items()):
                enabled_in_group = sum(
                    1 for j in folder_jobs
                    if not org_skips.get(str(j.old_path), False)
                )
                st.markdown(
                    f"<div class='org-group-head'>📂 {folder_name}/  "
                    f"<span style='font-weight:400;color:#166534'>"
                    f"{enabled_in_group}/{len(folder_jobs)} selected</span></div>",
                    unsafe_allow_html=True,
                )
                for job in folder_jobs:
                    key = str(job.old_path)
                    cb_col, row_col = st.columns([1, 11])
                    with cb_col:
                        checked = not org_skips.get(key, False)
                        new_val = st.checkbox(
                            "include", value=checked,
                            key=f"org_cb_{key}", label_visibility="collapsed",
                        )
                        st.session_state.org_skips[key] = not new_val
                    with row_col:
                        st.markdown(f"""
<div class="org-row">
  <div class="or-old" title="{job.old_path}">{job.old_path.name}</div>
  <div class="or-arr">→</div>
  <div class="or-new">{job.subfolder}/{job.old_path.name}</div>
  <div style="font-size:0.65rem;color:#9ca3af">{_fmt_size(job.record)}</div>
</div>
""", unsafe_allow_html=True)

            # Page footer
            if org_total_pages > 1:
                st.markdown(
                    f"<div style='font-size:0.69rem;color:#9ca3af;margin-top:8px'>"
                    f"Showing {org_start+1}–{min(org_start+ORG_PAGE, len(org_clear)):,}"
                    f" of {len(org_clear):,} files</div>",
                    unsafe_allow_html=True,
                )

            # Conflicts collapsible (all pages)
            if org_conflict:
                with st.expander(f"⚠  {len(org_conflict)} conflict{'s' if len(org_conflict)!=1 else ''} — skipped automatically"):
                    OF_PAGE = 50
                    of_total = max(1, (len(org_conflict) + OF_PAGE - 1) // OF_PAGE)
                    if "org_cf_page" not in st.session_state:
                        st.session_state.org_cf_page = 1
                    of_page = st.session_state.org_cf_page
                    of_start = (of_page - 1) * OF_PAGE

                    ofa_col, ofb_col, _ = st.columns([1, 1, 6])
                    with ofa_col:
                        if st.button("← Prev", key="of_prev", use_container_width=True,
                                     disabled=(of_page <= 1)):
                            st.session_state.org_cf_page -= 1; st.rerun()
                    with ofb_col:
                        if st.button("Next →", key="of_next", use_container_width=True,
                                     disabled=(of_page >= of_total)):
                            st.session_state.org_cf_page += 1; st.rerun()

                    st.markdown(
                        f"<div style='font-size:0.62rem;color:#9ca3af;margin:4px 0 6px'>"
                        f"Page {of_page}/{of_total}</div>",
                        unsafe_allow_html=True,
                    )
                    for job in org_conflict[of_start : of_start + OF_PAGE]:
                        st.markdown(f"""
<div class="org-row conflict-row">
  <div class="or-old">{job.old_path.name}</div>
  <div class="or-arr">→</div>
  <div class="or-new">{job.subfolder}/</div>
  <div class="or-conf">{job.conflict}</div>
</div>
""", unsafe_allow_html=True)

            # ── Step 3: Apply button ──────────────────────────────────────────

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            org_will_move = sum(
                1 for j in org_clear
                if not st.session_state.org_skips.get(str(j.old_path), False)
            )
            oa_col, _ = st.columns([2, 5])
            with oa_col:
                if st.button(
                    f"📁  Apply — move {org_will_move} file{'s' if org_will_move!=1 else ''}",
                    use_container_width=True, type="primary",
                    disabled=(org_will_move == 0), key="org_apply_btn",
                ):
                    for job in org_plan:
                        job.skip = st.session_state.org_skips.get(str(job.old_path), False)
                    with st.spinner(f"Moving {org_will_move} file(s)…"):
                        org_result = apply_organise_plan(org_plan)
                    st.session_state.org_result = org_result
                    _run_scan.clear()
                    st.session_state.org_plan  = None
                    st.session_state.org_skips = {}
                    st.session_state.org_page  = 1
                    st.rerun()

        # ── Step 4: Result banner ─────────────────────────────────────────────

        org_result: OrganiseResult | None = st.session_state.org_result
        if org_result is not None:
            if org_result.n_moved > 0:
                dirs_str = ", ".join(f"`{d.name}/`" for d in org_result.created_dirs)
                st.markdown(f"""
<div class="result-banner success">
  <strong>✓ {org_result.n_moved} file{'s' if org_result.n_moved!=1 else ''} moved
  into {org_result.n_created_dirs} folder{'s' if org_result.n_created_dirs!=1 else ''}.</strong><br>
  Created: {dirs_str or '(none new)'} &nbsp;·&nbsp;
  {org_result.n_skipped} skipped &nbsp;·&nbsp;
  {org_result.n_errors} error{'s' if org_result.n_errors!=1 else ''}.
  Run a fresh scan to update the table.
</div>
""", unsafe_allow_html=True)
            if org_result.n_errors > 0:
                err_li = "".join(
                    f"<li><code>{p.name}</code> — {m}</li>"
                    for p, m in org_result.errors
                )
                st.markdown(f"""
<div class="result-banner error">
  <strong>⚠ {org_result.n_errors} move{'s' if org_result.n_errors!=1 else ''} failed:</strong>
  <ul style="margin:6px 0 0;padding-left:18px">{err_li}</ul>
</div>
""", unsafe_allow_html=True)
            if org_result.n_moved > 0:
                with st.expander("Move log"):
                    for old, new in org_result.moved:
                        st.markdown(
                            f"`{old.name}` &nbsp;→&nbsp; `{new.parent.name}/{new.name}`  "
                            f"<span style='font-size:0.68rem;color:#9ca3af'>({old.parent})</span>",
                            unsafe_allow_html=True,
                        )

# Close the tab content box opened before the if/elif blocks
st.markdown('</div>', unsafe_allow_html=True)