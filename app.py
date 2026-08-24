#!/usr/bin/env python3
"""Streamlit app for Oxbow Reentry PDF Index — search, browse, and run pipeline."""

import os
import subprocess
import sys

import pandas as pd
import streamlit as st

from changelog import snapshot_before, record_run, load_changelog

# ---------------------
# Config
# ---------------------
SEARCH_INDEX = "search_index_full.csv"
SEARCH_INDEX_FALLBACK = "search_index.csv"

PIPELINE_STEPS = [
    ("Download PDFs from SharePoint", "sharepoint_graph_client.py"),
    ("Build search index", "build_search_index.py"),
    ("Generate anonymous links", "generate_anonymous_links.py"),
    ("Upload to Knack", "knack_uploader.py"),
    ("Repair category links in Knack", "repair_reentry_category_links.py"),
    ("Repair category descriptions in Knack", "repair_reentry_category_descriptions.py"),
    ("Repair document links in Knack", "repair_reentry_document_links.py"),
]

# Env vars that run_pipeline.py normally sets for subprocesses
PIPELINE_ENV = {**os.environ, "SAVE_CSV": "true", "EXTRACT_TEXT": "true"}


@st.cache_data
def load_index() -> pd.DataFrame:
    path = SEARCH_INDEX if os.path.exists(SEARCH_INDEX) else SEARCH_INDEX_FALLBACK
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df


def search_index(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Case-insensitive search across filename and extracted text."""
    if df.empty or not query.strip():
        return df
    q = query.lower()
    mask = (
        df["filename"].str.lower().str.contains(q, na=False)
        | df.get("extracted_text", pd.Series(dtype=str)).str.lower().str.contains(q, na=False)
    )
    return df[mask]


# ---------------------
# Page config
# ---------------------
st.set_page_config(page_title="Oxbow Reentry Resources", page_icon="📄", layout="wide")
st.title("📄 Oxbow Reentry Resource Index")

df = load_index()

if df.empty:
    st.warning("No search index found. Run the pipeline first to build the index.")
else:
    # ---------------------
    # Sidebar filters
    # ---------------------
    st.sidebar.header("Filters")
    categories = sorted(df["parent_category"].dropna().unique())
    selected_cats = st.sidebar.multiselect("Category", categories, default=[])

    # ---------------------
    # Search
    # ---------------------
    query = st.text_input("🔍 Search documents", placeholder="e.g. sober living, employment, court...")

    filtered = df.copy()
    if selected_cats:
        filtered = filtered[filtered["parent_category"].isin(selected_cats)]
    if query:
        filtered = search_index(filtered, query)

    # ---------------------
    # Stats row
    # ---------------------
    col1, col2, col3 = st.columns(3)
    col1.metric("Documents", len(filtered))
    col2.metric("Categories", filtered["parent_category"].nunique())
    col3.metric("Total Pages", int(filtered["page_count"].sum()) if "page_count" in filtered.columns else "—")

    # ---------------------
    # Results table
    # ---------------------
    display_cols = ["filename", "parent_category", "page_count", "extraction_method", "last_modified"]
    display_cols = [c for c in display_cols if c in filtered.columns]

    st.dataframe(
        filtered[display_cols].reset_index(drop=True),
        width="stretch",
        height=400,
    )

    # ---------------------
    # Document detail expander
    # ---------------------
    if not filtered.empty:
        st.subheader("Document Detail")
        doc_names = filtered["filename"].tolist()
        selected_doc = st.selectbox("Select a document", doc_names)
        if selected_doc:
            row = filtered[filtered["filename"] == selected_doc].iloc[0]
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Category:** {row.get('parent_category', '—')}")
                st.markdown(f"**Pages:** {row.get('page_count', '—')}")
                st.markdown(f"**Size:** {row.get('file_size_mb', '—')} KB")
                st.markdown(f"**Extraction:** {row.get('extraction_method', '—')}")
                st.markdown(f"**Modified:** {row.get('last_modified', '—')}")
            with col_b:
                web_url = row.get("web_url", "")
                if web_url:
                    st.markdown(f"[Open in SharePoint]({web_url})")
            if "extracted_text" in row.index and pd.notna(row["extracted_text"]):
                with st.expander("Extracted Text", expanded=False):
                    st.text(row["extracted_text"][:5000])

# ---------------------
# Pipeline runner
# ---------------------
st.divider()
st.subheader("⚙️ Pipeline")

with st.expander("Run pipeline steps", expanded=False):
    if st.button("▶️ Run All Steps", type="primary"):
        before_df = snapshot_before()
        progress = st.progress(0)
        for i, (label, script) in enumerate(PIPELINE_STEPS):
            st.write(f"**Step {i+1}/{len(PIPELINE_STEPS)}:** {label}")
            with st.spinner(f"Running {script}..."):
                result = subprocess.run(
                    [sys.executable, script],
                    capture_output=True,
                    text=True,
                    env=PIPELINE_ENV,
                )
            if result.returncode != 0:
                st.error(f"Failed at: {label}")
                st.error(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
                break
            st.success(f"✓ {label}")
            progress.progress((i + 1) / len(PIPELINE_STEPS))
        else:
            entry = record_run(before_df)
            st.balloons()
            st.success(
                f"Pipeline completed! "
                f"+{len(entry['added'])} added, "
                f"~{len(entry['modified'])} modified, "
                f"-{len(entry['removed'])} removed"
            )
        load_index.clear()

    st.divider()

    for label, script in PIPELINE_STEPS:
        col_btn, col_status = st.columns([3, 1])
        with col_btn:
            if st.button(label, key=script):
                with st.spinner(f"Running {script}..."):
                    result = subprocess.run(
                        [sys.executable, script],
                        capture_output=True,
                        text=True,
                        env=PIPELINE_ENV,
                    )
                if result.returncode == 0:
                    col_status.success("✓")
                    st.code(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
                else:
                    col_status.error("✗")
                    st.error(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
                load_index.clear()

    st.caption("After running steps, refresh the page to see updated data.")

# ---------------------
# Change Log
# ---------------------
st.divider()
st.subheader("📋 Change Log")

changelog = load_changelog()
if not changelog:
    st.info("No pipeline runs recorded yet. Run the pipeline to start tracking changes.")
else:
    for entry in changelog[:10]:
        ts = entry.get("timestamp", "unknown")
        added = entry.get("added", [])
        removed = entry.get("removed", [])
        modified = entry.get("modified", [])
        unchanged = entry.get("unchanged_count", 0)
        total = entry.get("total_documents", 0)

        has_changes = added or removed or modified
        summary = (
            f"+{len(added)} added, ~{len(modified)} modified, -{len(removed)} removed"
            if has_changes else "No changes"
        )

        with st.expander(f"**{ts}** — {summary} ({total} total docs)"):
            if added:
                st.markdown("**Added:**")
                for f in added:
                    st.markdown(f"- 🟢 {f}")
            if modified:
                st.markdown("**Modified:**")
                for f in modified:
                    st.markdown(f"- 🟡 {f}")
            if removed:
                st.markdown("**Removed:**")
                for f in removed:
                    st.markdown(f"- 🔴 {f}")
            if not has_changes:
                st.markdown(f"All {unchanged} documents unchanged.")
            dupes = entry.get("duplicates", [])
            if dupes:
                st.markdown("**\u26a0\ufe0f Duplicates in SharePoint** (same file in multiple folders):")
                dupes_df = pd.DataFrame(dupes)
                st.dataframe(dupes_df, use_container_width=True, hide_index=True)
