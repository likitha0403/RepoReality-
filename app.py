from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import unified_diff
from html import escape
from pathlib import PurePosixPath

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from reporeality.analyzer import (
    MAX_COMPRESSED_ARCHIVE_SIZE,
    RepoArchiveError,
    analyze_repository,
    summarize_claims,
)
from reporeality.preview import build_readme_preview
from reporeality.repomap import build_repo_map, build_repo_map_dot
from reporeality.impact import assess_integrity_impact
from reporeality.report import build_html_report

st.set_page_config(
    page_title="RepoReality · Documentation integrity",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');
    :root { --bg:#0d1117; --panel:#11161d; --border:#1f2630; --muted:#8b949e; --text:#c9d1d9; --green:#3fb950; --red:#f85149; --amber:#d29922; }
    .stApp { background:var(--bg); color:var(--text); font-family:'DM Sans',sans-serif; }
    [data-testid="stSidebar"] { background:#0b0f14; border-right:1px solid var(--border); }
    [data-testid="stSidebar"] * { color:var(--text); }
    .block-container { max-width:1440px; padding-top:1.7rem; padding-bottom:3rem; }
    .eyebrow { color:var(--green); font-family:'DM Mono',monospace; font-size:.72rem; letter-spacing:.16em; text-transform:uppercase; }
    .hero { font-size:3.15rem; line-height:1.12; font-weight:700; letter-spacing:-.045em; margin:.4rem 0 .6rem; padding:1.35rem 1.5rem .65rem; border:1px solid rgba(45,212,191,.16); border-bottom:0; border-radius:14px 14px 0 0; background:radial-gradient(ellipse at 18% 0%,rgba(45,212,191,.12),transparent 58%),linear-gradient(135deg,#111a23 0%,#11161d 58%,#10201f 100%); box-shadow:0 -8px 36px rgba(45,212,191,.055); }
    .hero span { color:#7ee787; text-shadow:0 0 24px rgba(63,185,80,.28); }
    .hero + .subhead { max-width:none; padding:0 1.5rem 1.3rem; margin-top:-.6rem; border:1px solid rgba(45,212,191,.16); border-top:0; border-radius:0 0 14px 14px; background:linear-gradient(135deg,#111a23 0%,#11161d 58%,#10201f 100%); color:#c9d1d9; font-size:1.08rem; line-height:1.65; }
    [data-testid="stHeader"], [data-testid="stDecoration"] { background:#0d1117; }
    [data-testid="stFileUploader"], [data-testid="stFileUploaderDropzone"] { background:#1a2230 !important; border:1px dashed #2b3543 !important; border-radius:8px !important; }
    [data-testid="stFileUploader"] { padding:.35rem; }
    [data-testid="stFileUploaderDropzone"] * { color:#c9d1d9 !important; }
    [data-testid="stFileUploaderDropzone"] button { background:#11161d !important; border:1px solid #2b3543 !important; color:#e6edf3 !important; border-radius:8px; }
    [data-testid="stFileUploaderDropzone"] button:hover { background:#212830 !important; border-color:#3fb950 !important; color:#e6edf3 !important; }
    [data-testid="stFileUploaderFile"] { background:#1a2230 !important; border:1px solid #2b3543 !important; border-radius:8px; }
    [data-testid="stFileUploaderFile"] *, [data-testid="stFileUploaderFile"] p, [data-testid="stFileUploaderFile"] small { color:#e6edf3 !important; }
    [data-testid="stTextInput"] input, [data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stMultiSelect"] [data-baseweb="select"] > div { background:#1a2230 !important; color:#e6edf3 !important; border:1px solid #2b3543 !important; border-radius:8px !important; }
    [data-testid="stSelectbox"] [data-baseweb="select"] svg, [data-testid="stMultiSelect"] [data-baseweb="select"] svg { fill:#e6edf3 !important; }
    [data-testid="stSelectbox"] [role="option"], [data-testid="stSelectbox"] [role="listbox"], [data-testid="stMultiSelect"] [role="option"], [data-testid="stMultiSelect"] [role="listbox"], [data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="popover"] ul { background:#1a2230 !important; color:#e6edf3 !important; }
    [data-testid="stTextInput"] label, [data-testid="stSelectbox"] label, [data-testid="stMultiSelect"] label, [data-testid="stFileUploader"] label { color:#c9d1d9 !important; }
    [data-testid="stTabs"] { position:sticky; top:0; z-index:20; background:rgba(13,17,23,.94); backdrop-filter:blur(12px); border-bottom:1px solid var(--border); }
    [data-testid="stTabs"] [role="tablist"] { border-bottom:1px solid var(--border); gap:1rem; }
    [data-testid="stTabs"] button[role="tab"] { color:#e6edf3 !important; opacity:1; font-size:1.05rem; font-weight:600; padding:10px 18px; border-radius:8px 8px 0 0; transition:color .15s ease,background .15s ease,transform .15s ease; }
    [data-testid="stTabs"] button[role="tab"]:hover { color:#ffffff !important; background:rgba(45,212,191,.09) !important; }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] { color:#ffffff !important; font-weight:700; border-bottom:3px solid transparent; border-image:linear-gradient(90deg,#2dd4bf,#3fb950) 1; background:linear-gradient(180deg,rgba(45,212,191,.12),rgba(63,185,80,.035)) !important; }
    .subhead { color:var(--muted); font-size:1rem; max-width:680px; }
    .section-title { color:#ffffff; font-weight:600; font-size:1.2rem; margin:1.15rem 0 .7rem; }
    .overview-kpi, .repo-panel, .checks-card, .guidance-card, [data-testid="stVerticalBlockBorderWrapper"] { background:#11161d; border:1px solid var(--border); border-radius:12px; box-shadow:0 8px 24px rgba(0,0,0,.16); transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease; }
    .overview-kpi { border-top:2px solid #303a46; padding:16px; min-height:88px; }
    .overview-kpi.verified { border-top-color:var(--green); } .overview-kpi.failed { border-top-color:var(--red); } .overview-kpi.unverified { border-top-color:var(--amber); }
    .overview-kpi-label { color:var(--muted); font-size:.64rem; text-transform:uppercase; letter-spacing:.09em; }
    .overview-kpi-value { color:#e6edf3; font-size:1.7rem; font-weight:700; line-height:1.15; margin-top:.35rem; }
    .repo-panel, [class*="st-key-summary_status_card"], [class*="st-key-summary_bar_card"] { background:#11161d; border:1px solid var(--border); border-radius:12px; padding:16px; }
    .repo-panel { min-height:118px; }
    [class*="st-key-summary_status_card"], [class*="st-key-summary_bar_card"] { min-height:430px; }
    .repo-panel-title, .summary-card-title { color:#e6edf3; font-weight:600; font-size:1rem; margin-bottom:.7rem; }
    .repo-panel-row { display:flex; justify-content:space-between; gap:.8rem; padding:.55rem 0; border-bottom:1px solid #1f2630; color:var(--muted); font-size:.82rem; }
    .repo-panel-row:last-child { border-bottom:0; }
    .repo-panel-value { color:#c9d1d9; text-align:right; overflow-wrap:anywhere; }
    .checks-card { padding:16px; margin-top:.8rem; }
    .checks-card-title { color:#e6edf3; font-weight:600; font-size:.86rem; margin-bottom:.45rem; }
    .checks-card ul { color:#c9d1d9; margin:0; padding-left:1.15rem; font-size:.78rem; line-height:1.65; }
    .finding-label { color:#8b949e; font-size:.68rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }
    .finding-claim { color:#e6edf3; font-family:'DM Mono',monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
    .finding-status { display:inline-block; padding:.2rem .55rem; border-radius:999px; font-size:.75rem; font-weight:600; }
    .finding-evidence { color:#c9d1d9; line-height:1.6; overflow-wrap:anywhere; }
    .guidance-card { padding:16px; margin:.9rem 0; }
    .impact-card-title { color:#ffffff; font-weight:700; font-size:1rem; margin:.15rem 0 .7rem; }
    .impact-section-label { color:#c9d1d9; font-size:.75rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase; margin:.75rem 0 .25rem; }
    .guidance-card-header { display:flex; align-items:center; gap:.65rem; color:#e6edf3; font-weight:600; margin-bottom:.8rem; overflow-wrap:anywhere; }
    .guidance-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr)); gap:.65rem; }
    .guidance-field { background:#0d1117; border:1px solid #1f2630; border-radius:7px; padding:.7rem; min-width:0; }
    .guidance-field-label { color:#8b949e; font-size:.66rem; font-weight:600; letter-spacing:.07em; text-transform:uppercase; margin-bottom:.35rem; }
    .guidance-field-value { color:#e6edf3; font-size:.84rem; line-height:1.5; white-space:pre-wrap; overflow-wrap:anywhere; }
    .chart-legend { display:flex; flex-direction:column; gap:.5rem; margin:.3rem .4rem; }
    .chart-legend-item { display:flex; align-items:center; justify-content:space-between; color:#c9d1d9; font-size:.82rem; }
    .chart-legend-name { display:flex; align-items:center; gap:.5rem; }
    .chart-legend-dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
    .chart-legend-count { color:#e6edf3; font-family:'DM Mono',monospace; }
    .repo-chip { border:1px solid var(--border); border-radius:999px; color:#c9d1d9; padding:.35rem .65rem; font-family:'DM Mono',monospace; font-size:.78rem; display:inline-block; margin-top:.6rem; }
    .overview-kpi:hover, .repo-panel:hover, .checks-card:hover, .guidance-card:hover, [data-testid="stVerticalBlockBorderWrapper"]:hover { border-color:rgba(45,212,191,.45); box-shadow:0 10px 30px rgba(0,0,0,.25),0 0 18px rgba(45,212,191,.08); }
    [data-testid="stVerticalBlockBorderWrapper"] { padding:16px; }
    .stButton > button, .stDownloadButton > button { border-radius:9px; border:1px solid #303a46; background:#161b22; color:#e6edf3; font-weight:600; transition:transform .16s ease,box-shadow .16s ease,background .16s ease; }
    .stDownloadButton > button { background:linear-gradient(110deg,#0d8a8a,#238636) !important; border-color:#2dd4bf !important; color:#ffffff !important; }
    div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2) [data-testid="stDownloadButton"]) > div:nth-child(3) [data-testid="stDownloadButton"] > button { background:#161b22 !important; border:1px solid #303a46 !important; color:#e6edf3 !important; }
    .stButton > button:hover, .stDownloadButton > button:hover { transform:translateY(-2px); box-shadow:0 7px 18px rgba(0,0,0,.28); border-color:#2dd4bf; color:#fff; }
    div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2) [data-testid="stDownloadButton"]) > div:nth-child(3) [data-testid="stDownloadButton"] > button:hover { background:#212830 !important; }
    .results-table-wrap { width:100%; overflow-x:auto; border:1px solid var(--border); border-radius:10px; }
    .results-table { width:100%; border-collapse:collapse; background:#11161d; color:#e6edf3; font-size:.82rem; }
    .results-table th { background:#11161d; color:#8b949e; font-size:.65rem; font-weight:600; letter-spacing:.08em; text-align:left; text-transform:uppercase; white-space:nowrap; padding:.7rem .75rem; border-bottom:1px solid #1f2630; }
    .results-table td { background:#11161d; color:#e6edf3; vertical-align:top; padding:.7rem .75rem; border-bottom:1px solid #1f2630; white-space:normal; overflow-wrap:anywhere; word-break:break-word; }
    .results-table tr:last-child td { border-bottom:0; }
    .results-table th:first-child, .results-table td:first-child { min-width:110px; white-space:nowrap; }
    .status-badge { display:inline-block; padding:.2rem .52rem; border:1px solid transparent; border-radius:999px; font-size:.72rem; font-weight:600; line-height:1.35; white-space:nowrap; }
    .status-badge.status-verified { color:#3fb950; background:rgba(63,185,80,.12); border-color:rgba(63,185,80,.28); }
    .status-badge.status-failed { color:#f85149; background:rgba(248,81,73,.12); border-color:rgba(248,81,73,.28); }
    .status-badge.status-unverified { color:#d29922; background:rgba(210,153,34,.13); border-color:rgba(210,153,34,.3); }
    .results-table code.mono-cell { display:inline-block; max-width:100%; box-sizing:border-box; background:#1a2230 !important; color:#e6edf3 !important; border:1px solid #2b3543; border-radius:6px; padding:.18rem .38rem; font-family:'DM Mono',monospace; font-size:.77rem; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; }
    .archive-files-table { width:100%; border-collapse:collapse; background:#11161d; color:#e6edf3; font-size:.84rem; }
    .archive-files-table th { background:#11161d; color:#8b949e; font-size:.66rem; font-weight:600; letter-spacing:.08em; text-align:left; text-transform:uppercase; white-space:nowrap; padding:.7rem .75rem; border-bottom:1px solid #1f2630; }
    .archive-files-table td { background:#11161d; color:#e6edf3; vertical-align:top; padding:.65rem .75rem; border-bottom:1px solid #1f2630; overflow-wrap:anywhere; }
    .archive-files-table tr:last-child td { border-bottom:0; }
    .archive-files-table .archive-file-path { font-family:'DM Mono',monospace; font-size:.8rem; }
    .archive-files-wrap { width:100%; overflow-x:auto; border:1px solid #1f2630; border-radius:10px; }
    .archive-status { display:inline-block; padding:.15rem .45rem; border-radius:999px; font-size:.72rem; font-weight:600; white-space:nowrap; }
    .archive-status-ok { color:#3fb950; background:rgba(63,185,80,.12); }
    .archive-status-corrupt { color:#f85149; background:rgba(248,81,73,.14); }
    .archive-status-problem { color:#d29922; background:rgba(210,153,34,.14); }
    .suggestion-empty { color:#8b949e; font-style:italic; }
    .note { color:var(--muted); font-size:.83rem; line-height:1.5; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("<div class='eyebrow'>◈ REPOREALITY</div>", unsafe_allow_html=True)
    st.markdown("### Repository check")
    st.write("Inspect README claims against the files in a repository archive.")
    uploaded = st.file_uploader("Upload repository ZIP", type=["zip"], help="The archive is inspected in memory. Repository code and commands are never executed.")
    st.markdown("---")
    st.markdown("**Checks included**")
    st.markdown("- Documented file paths\n- Common install commands\n- Declared dependencies\n- Configuration variables")
    st.markdown("---")
    st.markdown("<div class='note'>Private by design · no API keys · uploaded code is never run</div>", unsafe_allow_html=True)

st.markdown("<div class='eyebrow'>DOCUMENTATION INTEGRITY / 01</div>", unsafe_allow_html=True)
st.markdown("<div class='hero'>Trust what your README <span>promises.</span></div>", unsafe_allow_html=True)
st.markdown("<div class='subhead'>A deterministic documentation check that compares repository claims with the files that actually ship.</div>", unsafe_allow_html=True)
st.write("")

if uploaded is None:
    st.info("Upload a repository ZIP from the sidebar to begin an analysis.")
    st.markdown(
        """
        <div class="metric-card">
          <div class="section-title">Evidence, not guesswork</div>
          <div class="note" style="margin-top:.5rem">RepoReality reads README.md and supported text files from your archive. It verifies only claims it can check deterministically and labels uncertain checks clearly.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

if uploaded.size > MAX_COMPRESSED_ARCHIVE_SIZE:
    st.error(
        f"Compressed archive exceeds the {MAX_COMPRESSED_ARCHIVE_SIZE // (1024 * 1024)} MB limit."
    )
    st.stop()

try:
    analysis = analyze_repository(uploaded.getvalue(), PurePosixPath(uploaded.name).stem)
except RepoArchiveError as error:
    st.error(str(error))
    st.stop()

st.markdown(f"<span class='repo-chip'>◉ &nbsp;{escape(analysis.repository_name)}</span>", unsafe_allow_html=True)
st.write("")
if analysis.readme_path is None:
    st.warning("No README.md was found. The archive is valid, but there are no documentation claims to verify.")
    st.caption(f"Enumerated {len(analysis.files)} archive files.")
readme_content = analysis.readme_content or ""

summary = summarize_claims(analysis.claims)
status_order = ["Verified", "Failed", "Unverified"]
status_colors = {"Verified": "#3fb950", "Failed": "#f85149", "Unverified": "#d29922"}
rows = [
    {
        "Status": claim.status,
        "Claim type": claim.kind,
        "Source": claim.source_document,
        "README claim": claim.claim,
        "Line": claim.line,
        "Evidence": claim.evidence,
        "Evidence-backed suggestion": claim.correction or "—",
    }
    for claim in analysis.claims
]
dataframe = pd.DataFrame(rows, columns=[
    "Status", "Claim type", "Source", "README claim", "Line", "Evidence", "Evidence-backed suggestion",
])
row_status_counts = dataframe["Status"].value_counts()

overview_tab, results_tab, summary_tab, files_tab, map_tab = st.tabs(
    ["Overview", "Results", "Analysis summary", "Files", "Map"]
)

with overview_tab:
    total_claims = len(dataframe)
    verified_count = int(row_status_counts.get("Verified", 0))
    failed_count = int(row_status_counts.get("Failed", 0))
    unverified_count = int(row_status_counts.get("Unverified", 0))
    verified_rate = verified_count / total_claims * 100 if total_claims else 0
    documentation_score = verified_rate

    overview_columns = st.columns([4, 1.35], gap="medium")
    kpi_columns = overview_columns[0].columns(6, gap="small")
    kpis = [
        ("TOTAL CLAIMS", total_claims, "total"),
        ("VERIFIED", verified_count, "verified"),
        ("FAILED", failed_count, "failed"),
        ("UNVERIFIED", unverified_count, "unverified"),
        ("FILES SCANNED", len(analysis.files), "total"),
        (
            "README LINES",
            int(dataframe.loc[dataframe["Source"] == analysis.readme_path, "Line"].nunique())
            if analysis.readme_path
            else 0,
            "total",
        ),
    ]
    for column, (label, value, tone) in zip(kpi_columns, kpis):
        column.markdown(
            f"<div class='overview-kpi {tone}'><div class='overview-kpi-label'>{label}</div>"
            f"<div class='overview-kpi-value'>{value}</div></div>",
            unsafe_allow_html=True,
        )

    overview_columns[1].markdown(
        "<div class='repo-panel'><div class='repo-panel-title'>Repository overview</div>"
        f"<div class='repo-panel-row'><span>Repository</span><span class='repo-panel-value'>{escape(analysis.repository_name)}</span></div>"
        f"<div class='repo-panel-row'><span>Files scanned</span><span class='repo-panel-value'>{len(analysis.files)}</span></div>"
        f"<div class='repo-panel-row'><span>Documentation Check Score</span><span class='repo-panel-value'>{documentation_score:.1f}%</span></div>"
        "<div class='note'>100 × Verified / Total checks. Measures only the checks performed by RepoReality.</div></div>",
        unsafe_allow_html=True,
    )
    overview_columns[1].markdown(
        "<div class='checks-card'><div class='checks-card-title'>Checks included</div><ul>"
        "<li>Documented file paths</li><li>Common install commands</li>"
        "<li>Declared dependencies</li><li>Configuration variables</li>"
        "</ul></div>",
        unsafe_allow_html=True,
    )

with summary_tab:
    chart_columns = st.columns(2, gap="medium")
    with chart_columns[0]:
        with st.container(border=True, key="summary_status_card"):
            st.markdown("<div class='summary-card-title'>Claims by status</div>", unsafe_allow_html=True)
            st.markdown("<div class='summary-card-caption'>Evidence results across detected claims</div>", unsafe_allow_html=True)
            status_values = [verified_count, failed_count, unverified_count]
            status_donut = go.Figure(
                data=[
                    go.Pie(
                        labels=status_order,
                        values=status_values,
                        hole=.7,
                        sort=False,
                        marker={"colors":[status_colors[status] for status in status_order]},
                        textinfo="none",
                        hovertemplate="%{label}: %{value} claims<extra></extra>",
                        showlegend=False,
                    )
                ]
            )
            status_donut.update_layout(
                template="plotly_dark",
                height=250,
                margin={"l":4,"r":4,"t":4,"b":4},
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font={"color":"#c9d1d9"},
                hoverlabel={"bgcolor":"#161b22","bordercolor":"#303a46","font":{"color":"#e6edf3"}},
                annotations=[{
                    "text":f"<b>{len(dataframe)}</b><br><span style='font-size:11px'>total claims</span>",
                    "x":.5,"y":.5,"showarrow":False,"font":{"size":22,"color":"#c9d1d9"},
                }],
            )
            st.plotly_chart(status_donut, use_container_width=True, config={"displayModeBar":False})
            st.markdown(
                "<div class='chart-legend'>"
                f"<div class='chart-legend-item'><span class='chart-legend-name'><i class='chart-legend-dot' style='background:{status_colors['Verified']}'></i>Verified</span><span class='chart-legend-count'>{verified_count}</span></div>"
                f"<div class='chart-legend-item'><span class='chart-legend-name'><i class='chart-legend-dot' style='background:{status_colors['Failed']}'></i>Failed</span><span class='chart-legend-count'>{failed_count}</span></div>"
                f"<div class='chart-legend-item'><span class='chart-legend-name'><i class='chart-legend-dot' style='background:{status_colors['Unverified']}'></i>Unverified</span><span class='chart-legend-count'>{unverified_count}</span></div>"
                "</div>",
                unsafe_allow_html=True,
            )

    with chart_columns[1]:
        with st.container(border=True, key="summary_type_card"):
            st.markdown("<div class='summary-card-title'>Claim type breakdown</div>", unsafe_allow_html=True)
            st.markdown("<div class='summary-card-caption'>Claim counts split by verification status</div>", unsafe_allow_html=True)
            if dataframe.empty:
                st.info("No claims available to chart.")
            else:
                type_counts = (
                    dataframe.groupby(["Claim type", "Status"], as_index=False)
                    .size()
                    .rename(columns={"size":"Claims"})
                )
                type_chart = px.bar(
                    type_counts,
                    x="Claims",
                    y="Claim type",
                    color="Status",
                    orientation="h",
                    barmode="stack",
                    text="Claims",
                    category_orders={"Status":status_order},
                    color_discrete_map=status_colors,
                    template="plotly_dark",
                    hover_data={"Claims":True,"Status":True,"Claim type":True},
                )
                type_chart.update_traces(textposition="inside", insidetextanchor="middle")
                type_chart.update_layout(
                    height=345,
                    margin={"l":8,"r":8,"t":8,"b":8},
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font={"color":"#c9d1d9"},
                    hoverlabel={"bgcolor":"#161b22","bordercolor":"#303a46","font":{"color":"#e6edf3"}},
                    xaxis={"title":"Claims","tickfont":{"color":"#c9d1d9"},"title_font":{"color":"#c9d1d9"},"gridcolor":"#1f2630","zerolinecolor":"#1f2630"},
                    yaxis={"title":None,"tickfont":{"color":"#c9d1d9"},"categoryorder":"total ascending"},
                    legend={"font":{"color":"#c9d1d9"},"title_font":{"color":"#c9d1d9"}},
                )
                st.plotly_chart(type_chart, use_container_width=True, config={"displayModeBar":False})

with files_tab:
    st.markdown("<div class='section-title'>Archive files</div>", unsafe_allow_html=True)
    file_count = sum(item.entry_type == "File" for item in analysis.file_metadata)
    folder_count = sum(item.entry_type == "Folder" for item in analysis.file_metadata)

    def archive_entry_status(item):
        issues = [issue for issue in analysis.integrity_issues if issue.path == item.path]
        problem_type = issues[0].problem_type if issues else item.inspection_status
        details = issues[0].details if issues else item.inspection_details
        if problem_type in {"CRC failure", "Unreadable member"}:
            return "Corrupt", details, 0
        if problem_type == "Encrypted entry":
            return "Encrypted", details, 1
        if problem_type == "Unsupported compression":
            return "Unsupported compression", details, 1
        if problem_type == "Unsafe path":
            return "Invalid path", details, 1
        if problem_type == "Duplicate or case-colliding path":
            return "Duplicate/collision", details, 1
        if item.entry_type == "Folder" and problem_type == "Directory entry":
            return "OK", "", 2
        if problem_type in {"Inspected (UTF-8)", "Inspected (binary)"}:
            return "OK", "", 2
        reason = details or problem_type or "No inspection result available"
        return f"Not inspected ({reason})", details or reason, 1

    archive_rows = []
    for index, item in enumerate(analysis.file_metadata):
        status, details, priority = archive_entry_status(item)
        archive_rows.append((priority, item.path.casefold(), index, item, status, details))
    archive_rows.sort(key=lambda row: (row[0], row[1], row[2]))
    corrupt_count = sum(row[4] == "Corrupt" for row in archive_rows)
    ok_count = sum(row[4] == "OK" for row in archive_rows)
    problem_count = len(archive_rows) - corrupt_count - ok_count
    st.markdown(
        f"**{len(archive_rows)} entries: {file_count} files, {folder_count} folders. "
        f"Corrupt: {corrupt_count}, Other problems: {problem_count}, OK: {ok_count}**"
    )
    if corrupt_count == 0 and problem_count == 0:
        st.caption("No integrity problems detected in the entries that could be inspected")

    file_controls = st.columns([2, 1], gap="medium")
    file_search = file_controls[0].text_input(
        "Search files",
        placeholder="Search paths or types",
        key="archive_file_search",
    ).strip().casefold()
    file_types = sorted({item.file_type for item in analysis.file_metadata})
    selected_file_type = file_controls[1].selectbox(
        "Type filter",
        options=["All types", *file_types],
        key="archive_file_type",
    )
    visible_files = [
        row
        for row in archive_rows
        if (not file_search or file_search in row[3].path.casefold() or file_search in row[3].file_type.casefold() or file_search in row[4].casefold() or file_search in row[5].casefold())
        and (selected_file_type == "All types" or row[3].file_type == selected_file_type)
    ]
    st.caption(f"Showing {len(visible_files)} matching entries")
    file_table = [
        "<div class='archive-files-wrap'><table class='archive-files-table'>"
        "<thead><tr><th scope='col'>Path</th><th scope='col'>Type</th><th scope='col'>Size</th><th scope='col'>Status</th><th scope='col'>Details</th></tr></thead><tbody>"
    ]
    if visible_files:
        for _, _, _, item, status, details in visible_files:
            status_class = "archive-status-ok" if status == "OK" else "archive-status-corrupt" if status == "Corrupt" else "archive-status-problem"
            file_table.append(
                "<tr>"
                f"<td class='archive-file-path'>{escape(item.path)}</td>"
                f"<td>{escape(item.file_type)}</td>"
                f"<td>{item.size_bytes:,} B</td>"
                f"<td><span class='archive-status {status_class}'>{escape(status)}</span></td>"
                f"<td>{escape(details) if details else '—'}</td>"
                "</tr>"
            )
    else:
        file_table.append(
            "<tr><td colspan='5'>No files match the selected search and type.</td></tr>"
        )
    file_table.append("</tbody></table></div>")
    st.markdown("".join(file_table), unsafe_allow_html=True)

with map_tab:
    decoded_files = dict(
        getattr(analysis, "decoded_files", ())
        or getattr(analysis, "document_contents", ())
    )
    try:
        repo_map = build_repo_map(analysis, decoded_files)
    except Exception as error:
        st.warning(f"Could not build the repository map from the available analysis data: {error}")
        repo_map = {"nodes": (), "edges": ()}
    map_nodes = repo_map["nodes"]
    map_edges = repo_map["edges"]
    node_filter = st.selectbox(
        "Map filter",
        options=["All", "Gaps only", "Documentation only"],
        key="repo_map_filter",
    )
    legend_items = (
        ("Documentation", "#1f6feb"),
        ("Entry point", "#238636"),
        ("Manifest", "#8957e5"),
        ("Python module", "#0d8a8a"),
        ("Gap", "#da3633"),
        ("Created locally", "#6e7681"),
    )
    legend = "".join(
        f"<span style='display:inline-block;background:{color};color:#fff;border-radius:999px;"
        f"padding:.22rem .58rem;margin:.12rem;font-size:.75rem'>{escape(label)}</span>"
        for label, color in legend_items
    )
    st.markdown(f"<div>{legend}</div>", unsafe_allow_html=True)

    if node_filter == "Gaps only":
        gap_edges = [edge for edge in map_edges if edge["status"] == "gap"]
        included_ids = {str(edge["target"]) for edge in gap_edges}
        included_ids.update(str(edge["source"]) for edge in gap_edges)
        visible_nodes = tuple(node for node in map_nodes if str(node["id"]) in included_ids)
        visible_edges = tuple(gap_edges)
    elif node_filter == "Documentation only":
        visible_nodes = tuple(node for node in map_nodes if node["kind"] == "Documentation")
        visible_ids = {str(node["id"]) for node in visible_nodes}
        visible_edges = tuple(
            edge for edge in map_edges
            if str(edge["source"]) in visible_ids and str(edge["target"]) in visible_ids
        )
    else:
        visible_nodes = map_nodes
        visible_edges = map_edges
    gap_count = sum(node["kind"] == "Gap" for node in visible_nodes)
    st.caption(f"{len(visible_nodes)} nodes, {len(visible_edges)} links, {gap_count} gaps")
    if not visible_edges:
        st.info("No connections could be established")
    else:
        st.graphviz_chart(
            build_repo_map_dot(visible_nodes, visible_edges),
            use_container_width=True,
        )
    st.caption(
        "Links are drawn only where the repository contains evidence. This map is not a full understanding of the code."
    )
    map_gaps = [
        claim for claim in analysis.claims
        if claim.kind == "File path" and claim.status == "Failed"
    ]
    st.markdown("<div class='section-title'>Gaps</div>", unsafe_allow_html=True)
    if map_gaps:
        for claim in map_gaps:
            suggested_fix = claim.guidance()["Suggested fix"]
            st.markdown(
                f"- **{escape(claim.source_document)} line {claim.line}:** "
                f"`{escape(claim.claim)}` — {escape(suggested_fix)}"
            )
    else:
        st.caption("No missing documented file paths were detected.")

with results_tab:
    header, html_export, json_export = st.columns([3.5, 2, 1.35])
    header.markdown("<div class='section-title'>Analysis results</div>", unsafe_allow_html=True)
    readme_source = analysis.readme_path or "Not found"
    header.caption(f"README source: `{readme_source}` · {len(analysis.files)} archive files enumerated")
    document_summaries = []
    for document in analysis.documents_checked:
        summary_text = f"{document.path} ({document.claim_count} claims)"
        if document.status != "Checked":
            summary_text += f" — {document.status}"
        document_summaries.append(summary_text)
    if document_summaries:
        st.caption("Documents checked: " + " · ".join(document_summaries))
    integrity_impact = sorted(
        assess_integrity_impact(analysis),
        key=lambda item: (
            0 if item["error_type"] in {"CRC failure", "Unreadable member"} else 1,
            str(item["path"]).casefold(),
        ),
    )
    report = {
        "repository": analysis.repository_name,
        "readme": analysis.readme_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "claims": [claim.to_dict() for claim in analysis.claims],
        "documents_checked": [
            {
                "path": document.path,
                "status": document.status,
                "claim_count": document.claim_count,
                "reason": document.reason,
            }
            for document in analysis.documents_checked
        ],
        "integrity_impact": integrity_impact,
    }
    html_export.download_button(
        "↓  Download report (HTML)",
        data=build_html_report(
            analysis,
            summary,
            report["generated_at"],
        ),
        file_name=f"{analysis.repository_name}-reporeality-report.html",
        mime="text/html",
        use_container_width=True,
    )
    json_export.download_button(
        "Download JSON (for developers)",
        data=json.dumps(report, indent=2),
        file_name=f"{analysis.repository_name}-reporeality-report.json",
        mime="application/json",
        use_container_width=True,
    )

    if integrity_impact:
        st.markdown("<div class='section-title'>Impact &amp; Recovery</div>", unsafe_allow_html=True)
        for item in integrity_impact:
            with st.container(border=True):
                st.markdown(
                    f"<div class='impact-card-title'>{escape(str(item['path']))}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div class='impact-section-label'>Integrity Error</div>", unsafe_allow_html=True)
                st.text(f"{item['error_type']}: {item['details']}")
                st.markdown("<div class='impact-section-label'>Evidence</div>", unsafe_allow_html=True)
                st.text(item["what_is_confirmed"])
                st.markdown("<div class='impact-section-label'>Potential Impact</div>", unsafe_allow_html=True)
                st.text(item["potential_impact"])
                if item["references"]:
                    st.caption("References found in readable repository text:")
                    for reference in item["references"]:
                        st.text(f"{reference['path']}:{reference['line']}: {reference['text']}")
                else:
                    st.caption("No references were found in readable text; this does not prove the file is unused.")
                st.markdown("<div class='impact-section-label'>Affected Checks</div>", unsafe_allow_html=True)
                if item["affected_checks"]:
                    for check in item["affected_checks"]:
                        st.text(
                            f"{check['source']}:{check['line']} · {check['kind']} · "
                            f"{check['status']}: {check['claim']}"
                        )
                        st.caption(str(check["evidence"]))
                else:
                    st.text("No directly affected verification check was identified in the analyzer results.")
                st.markdown("<div class='impact-section-label'>Recommended Recovery</div>", unsafe_allow_html=True)
                st.code(item["recovery"], language=None)
                st.markdown("<div class='impact-section-label'>Limitations</div>", unsafe_allow_html=True)
                st.text(item["limitations"])

    if analysis.readme_path is None and not analysis.claims:
        st.info("No README.md was found. The archive file inventory is available in the Files tab.")
    elif not analysis.claims:
        if analysis.readme_path is None:
            st.info("No readable README.md or eligible documentation claims were found.")
        else:
            st.success("README found. No supported file-path, installation, dependency, or configuration claims were detected.")
    else:
        source_options = sorted({claim.source_document for claim in analysis.claims}, key=str.casefold)
        selected_source = st.selectbox(
            "Filter by source",
            options=["All sources", *source_options],
            key="claim_source_filter",
        )
        visible_claim_indices = [
            index for index, claim in enumerate(analysis.claims)
            if selected_source == "All sources" or claim.source_document == selected_source
        ]
        st.markdown("<div class='section-title'>Finding details</div>", unsafe_allow_html=True)
        selected_index = st.selectbox(
            "Select a finding",
            options=visible_claim_indices,
            format_func=lambda index: (
                f"{analysis.claims[index].source_document} · Line {analysis.claims[index].line} · {analysis.claims[index].kind} · "
                f"{analysis.claims[index].claim}"
            ),
            key="finding_selector",
        )
        selected_claim = analysis.claims[selected_index]
        status_color = status_colors.get(selected_claim.status, "#8b949e")
        with st.container(border=True):
            st.markdown("<div class='finding-label'>README claim</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='finding-claim'>{escape(selected_claim.claim)}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<span class='finding-status' style='color:{status_color};background:{status_color}20'>"
                f"{escape(selected_claim.status)}</span>",
                unsafe_allow_html=True,
            )
            st.caption(f"{selected_claim.source_document} · Line {selected_claim.line} · {selected_claim.kind}")
            st.markdown("<div class='finding-label'>Supporting evidence</div>", unsafe_allow_html=True)
            if selected_claim.evidence.strip():
                st.markdown(
                    f"<div class='finding-evidence'>{escape(selected_claim.evidence)}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.info("The analyzer did not provide supporting evidence for this finding; it cannot be verified from the available result.")

        guidance_claims = [
            claim for claim in analysis.claims
            if claim.status in {"Failed", "Unverified"}
            and (selected_source == "All sources" or claim.source_document == selected_source)
        ]
        if guidance_claims:
            st.markdown("<div class='section-title'>Why and how to fix</div>", unsafe_allow_html=True)
            guidance_cards = []
            guidance_fields = (
                "README says",
                "Rule checked",
                "What we found",
                "Why it is flagged",
                "Type",
                "Suggested fix",
            )
            for claim in guidance_claims:
                status_color = status_colors.get(claim.status, "#8b949e")
                fields = "".join(
                    "<div class='guidance-field'>"
                    f"<div class='guidance-field-label'>{escape(label)}</div>"
                    f"<div class='guidance-field-value'>{escape(claim.guidance()[label])}</div>"
                    "</div>"
                    for label in guidance_fields
                )
                guidance_cards.append(
                    "<div class='guidance-card'>"
                    "<div class='guidance-card-header'>"
                    f"<span class='finding-status' style='color:{status_color};background:{status_color}20'>"
                    f"{escape(claim.status)}</span>"
                    f"<span>{escape(claim.source_document)} · Line {claim.line} · {escape(claim.kind)}</span>"
                    "</div>"
                    f"<div class='guidance-grid'>{fields}</div>"
                    "</div>"
                )
            st.markdown("".join(guidance_cards), unsafe_allow_html=True)

        st.markdown("<div class='section-title'>Review before repair</div>", unsafe_allow_html=True)
        if selected_claim.status != "Failed" or not selected_claim.correction:
            st.caption("This finding has no evidence-backed README correction to preview.")
        else:
            document_content = dict(analysis.document_contents).get(selected_claim.source_document, readme_content)
            proposed_readme = build_readme_preview(document_content, selected_claim)
            if proposed_readme is None:
                st.info(
                    "A suggestion is available, but it cannot be matched uniquely to the reported README line. "
                    "No draft was generated."
                )
            else:
                st.caption(f"Evidence-backed suggestion: {selected_claim.correction}")
                original_column, proposed_column = st.columns(2)
                original_column.text_area(
                    "Original README",
                    value=                    document_content,
                    height=320,
                    disabled=True,
                    key=f"original_readme_{selected_index}",
                )
                proposed_column.text_area(
                    "Proposed README draft",
                    value=proposed_readme,
                    height=320,
                    disabled=True,
                    key=f"proposed_readme_{selected_index}",
                )
                diff = "".join(
                    unified_diff(
                        document_content.splitlines(keepends=True),
                        proposed_readme.splitlines(keepends=True),
                        fromfile=selected_claim.source_document,
                        tofile=f"{PurePosixPath(selected_claim.source_document).stem}.corrected{PurePosixPath(selected_claim.source_document).suffix}",
                    )
                )
                with st.expander("Review the exact change"):
                    st.code(diff or "No changes.", language="diff")

                include_correction = st.checkbox(
                    "Include this correction in the downloadable draft",
                    value=True,
                    key=f"include_correction_{selected_index}",
                )
                if include_correction:
                    st.download_button(
                        f"Download corrected {PurePosixPath(selected_claim.source_document).name} draft",
                        data=proposed_readme,
                        file_name=f"{PurePosixPath(selected_claim.source_document).stem}.corrected{PurePosixPath(selected_claim.source_document).suffix}",
                        mime="text/markdown",
                        key=f"download_corrected_readme_{selected_index}",
                    )
                    st.caption(
                        "The uploaded repository archive is not modified. Review the draft, then apply it manually if appropriate."
                    )
                else:
                    st.caption("This correction is excluded from the downloadable draft.")

        status_classes = {
            "Verified": "status-verified",
            "Failed": "status-failed",
            "Unverified": "status-unverified",
        }
        table_columns = [
            "Status",
            "Claim type",
            "Source",
            "README claim",
            "Line",
            "Evidence",
            "Evidence-backed suggestion",
        ]
        table_html = [
            "<div class='results-table-wrap'><table class='results-table'><thead><tr>",
            "".join(f"<th scope='col'>{escape(column)}</th>" for column in table_columns),
            "</tr></thead><tbody>",
        ]
        visible_dataframe = dataframe[
            (dataframe["Source"] == selected_source) if selected_source != "All sources"
            else [True] * len(dataframe)
        ]
        for row in visible_dataframe.to_dict(orient="records"):
            table_html.append("<tr>")
            for column in table_columns:
                value = escape(str(row[column]))
                if column == "Status":
                    css_class = status_classes.get(str(row[column]), "")
                    table_html.append(f"<td><span class='status-badge {css_class}'>{value}</span></td>")
                elif column in {"README claim", "Evidence", "Source"}:
                    table_html.append(f"<td><code class='mono-cell'>{value}</code></td>")
                elif column == "Evidence-backed suggestion" and str(row[column]) in {"—", "", "None"}:
                    table_html.append("<td><span class='suggestion-empty'>n/a</span></td>")
                else:
                    table_html.append(f"<td>{value}</td>")
            table_html.append("</tr>")
        table_html.append("</tbody></table></div>")
        st.markdown("".join(table_html), unsafe_allow_html=True)
        with st.expander("What these statuses mean"):
            st.markdown("- **Verified** — supporting evidence was found in the archive.\n- **Failed** — a supported deterministic check found contradictory or missing evidence.\n- **Unverified** — the claim could not be checked reliably by the available rules.")
