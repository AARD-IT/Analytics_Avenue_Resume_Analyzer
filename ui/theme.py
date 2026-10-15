"""Blue & white Analytics Avenue theme + shared HTML snippets."""
import base64
from functools import lru_cache
from html import escape

import streamlit as st

from core.config import Config

BLUE_900 = "#0B2E6F"
BLUE_700 = "#0F4CB8"
BLUE_600 = "#1565D8"
BLUE_100 = "#E8F0FD"
BLUE_50 = "#F4F8FE"
INK = "#0F1F3D"
MUTED = "#5B6B88"
BORDER = "#DCE6F5"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stButton button, input, textarea {{ font-family: 'Inter', sans-serif; }}
.stApp {{ background: #FFFFFF; }}
.block-container {{ padding-top: 4.2rem; padding-bottom: 6rem; max-width: 100%; padding-left: 3rem; padding-right: 3rem; }}
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"] {{ display: none !important; }}
[data-testid="stToolbarActions"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: rgba(255,255,255,0.92); backdrop-filter: blur(6px); border-bottom: 1px solid {BORDER}; }}

/* ---------- brand bar ---------- */
.aa-brand {{ display:flex; align-items:center; gap:16px; padding:16px 24px; border-radius:14px;
  background: linear-gradient(120deg, {BLUE_900} 0%, {BLUE_700} 55%, {BLUE_600} 100%); color:#fff;
  box-shadow: 0 10px 30px rgba(21,101,216,0.18); margin-bottom: 18px; }}
.aa-brand img {{ height:46px; filter: drop-shadow(0 2px 6px rgba(0,0,0,.25)); }}
.aa-brand .t1 {{ font-size:1.45rem; font-weight:800; letter-spacing:-.02em; line-height:1.1; }}
.aa-brand .t2 {{ font-size:.86rem; opacity:.85; font-weight:500; }}
.aa-brand .sp {{ flex:1; }}
.aa-pill {{ background: rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.25); padding:6px 14px;
  border-radius:8px; font-size:.8rem; font-weight:600; white-space:nowrap; letter-spacing:.01em; }}
.aa-kpis {{ display:flex; gap:28px; }}
.aa-kpi {{ text-align:right; }} .aa-kpi .n {{ font-size:1.35rem; font-weight:800; line-height:1; }}
.aa-kpi .k {{ font-size:.7rem; opacity:.8; text-transform:uppercase; letter-spacing:.08em; margin-top:4px; }}
.lbl {{ display:inline-block; font-size:.68rem; font-weight:700; color:{MUTED}; text-transform:uppercase;
  letter-spacing:.07em; margin-right:6px; }}
.sep {{ display:inline-block; width:1px; height:12px; background:{BORDER}; margin:0 12px; vertical-align:middle; }}

/* ---------- chat ---------- */
[data-testid="stChatMessage"] {{ background: transparent; padding: .35rem 0; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {{
  background: {BLUE_600}; color: #fff; border-radius: 16px 16px 4px 16px; padding: 10px 16px; max-width: 760px; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] p {{ color:#fff; }}
[data-testid="stChatMessageAvatarUser"] {{ background: {BLUE_100}; color:{BLUE_700}; }}
[data-testid="stChatMessageAvatarAssistant"] {{ background: {BLUE_600}; }}
[data-testid="stChatInput"] textarea {{ font-size: 0.98rem; }}
[data-testid="stChatInput"] {{ border: 1.5px solid {BLUE_600}33; border-radius: 14px; }}

/* ---------- candidate card ---------- */
.aa-card {{ border:1px solid {BORDER}; border-radius:14px; padding:14px 16px 10px; background:#fff;
  box-shadow: 0 2px 10px rgba(15,31,61,0.04); }}
.aa-card:hover {{ border-color:{BLUE_600}66; box-shadow: 0 6px 18px rgba(21,101,216,0.10); }}
.aa-row {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.aa-num {{ background:{BLUE_600}; color:#fff; font-weight:700; border-radius:8px; padding:1px 8px; font-size:.8rem; }}
.aa-name {{ font-weight:700; font-size:1.05rem; color:{INK}; }}
.aa-role {{ color:{MUTED}; font-size:.86rem; }}
.aa-chip {{ display:inline-block; background:{BLUE_100}; color:{BLUE_700}; border-radius:999px; padding:2px 10px;
  font-size:.76rem; font-weight:600; margin:2px 4px 2px 0; }}
.aa-chip.g {{ background:#EAF7EF; color:#16794A; }}
.aa-chip.o {{ background:#FFF4E5; color:#9A5B00; }}
.aa-contact {{ font-size:.86rem; color:{INK}; margin:6px 0 2px; }}
.aa-contact a {{ color:{BLUE_700}; text-decoration:none; }}
.aa-proj {{ font-size:.84rem; color:{INK}; margin:3px 0; }}
.aa-proj .s {{ color:{BLUE_700}; font-weight:600; }}
.aa-proj .d {{ color:{MUTED}; }}
.aa-score {{ margin-left:auto; font-weight:800; color:{BLUE_700}; font-size:1.05rem; }}
.aa-meta {{ font-size:.74rem; color:{MUTED}; margin-top:4px; }}

/* ---------- misc ---------- */
.aa-hero {{ text-align:center; padding: 40px 10px 14px; }}
.aa-hero h2 {{ color:{INK}; font-weight:800; letter-spacing:-.02em; margin-bottom:4px; }}
.aa-hero p {{ color:{MUTED}; }}
.aa-stat {{ border:1px solid {BORDER}; border-radius:14px; padding:14px 16px; background:{BLUE_50}; }}
.aa-stat .v {{ font-size:1.6rem; font-weight:800; color:{BLUE_700}; }}
.aa-stat .l {{ font-size:.8rem; color:{MUTED}; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }}
.aa-foot {{ font-size:.72rem; color:{MUTED}; margin-top:2px; }}
div[data-testid="stMetric"] {{ background:{BLUE_50}; border:1px solid {BORDER}; border-radius:14px; padding:12px 16px; }}
div[data-testid="stMetricValue"] {{ color:{BLUE_700}; font-weight:800; }}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{ background:{BLUE_600}; border-color:{BLUE_600}; }}
.stButton > button {{ border-radius:10px; font-weight:600; }}
.stButton > button[kind="primary"]:disabled {{ background:{BLUE_100}; color:{MUTED}; border-color:{BORDER}; }}
section[data-testid="stSidebar"] {{ background: {BLUE_50}; border-right: 1px solid {BORDER}; }}
.aa-login {{ max-width:440px; margin: 6vh auto 0; border:1px solid {BORDER}; border-radius:20px; padding:28px 28px 18px;
  box-shadow: 0 16px 40px rgba(15,31,61,.08); background:#fff; text-align:center; }}
.aa-login img {{ height:64px; }}
.aa-login h2 {{ color:{INK}; font-weight:800; margin:.4rem 0 0; }}
.aa-login p {{ color:{MUTED}; }}
@media (max-width: 640px) {{
  .aa-kpis {{ display:none; }}
  .aa-brand {{ padding:14px; }} .aa-brand .t1 {{ font-size:1.1rem; }} .aa-pill {{ display:none; }}
  .block-container {{ padding-left: 1rem !important; padding-right: 1rem !important; }}
}}
</style>
"""


@lru_cache(maxsize=4)
def img_b64(name: str) -> str:
    p = Config.ASSETS / name
    return base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def brand_bar(subtitle="Candidate search assistant", pill="", kpis=None):
    logo = logo_uri()
    right = ""
    if kpis:
        right = '<div class="aa-kpis">' + "".join(
            f'<div class="aa-kpi"><div class="n">{escape(str(v))}</div><div class="k">{escape(k)}</div></div>'
            for k, v in kpis) + "</div>"
    elif pill:
        right = f'<span class="aa-pill">{escape(pill)}</span>'
    st.markdown(
        f"""<div class="aa-brand"><img src="{logo}" alt="Analytics Avenue"/>
        <div><div class="t1">Analytics Avenue &nbsp;|&nbsp; Resume Analyzer</div><div class="t2">{escape(subtitle)}</div></div>
        <div class="sp"></div>{right}</div>""",
        unsafe_allow_html=True,
    )


def logo_uri() -> str:
    svg = Config.ASSETS / "logo.svg"
    if svg.exists():
        return "data:image/svg+xml;base64," + base64.b64encode(svg.read_bytes()).decode()
    return "data:image/png;base64," + img_b64("logo_icon.png")


def chip(text, cls=""):
    return f'<span class="aa-chip {cls}">{escape(str(text))}</span>' if text else ""


def stat(value, label):
    return f'<div class="aa-stat"><div class="v">{escape(str(value))}</div><div class="l">{escape(label)}</div></div>'
