"""Chat page — the main HR experience (full width, no sidebar)."""
import uuid

import pandas as pd
import streamlit as st
from langgraph.types import Command

from agent.graph import build_graph
from core import store
from core.config import Config
from ui import components as C
from ui import theme

SUGGESTIONS = [
    "Data analysts with 3+ years in manufacturing",
    "How many BDE freshers?",
    "Data engineers 2 to 5 years with Spark",
    "Candidates with healthcare projects",
    "Who knows Power BI and SQL?",
    "Overview of all candidates",
]
BOT_AVATAR = str(Config.ASSETS / "logo_icon.png")
USER_AVATAR = str(Config.ASSETS / "user.png")


@st.cache_resource
def get_graph():
    return build_graph()


def _thread():
    ss = st.session_state
    if "thread_id" not in ss:
        ss.thread_id = str(uuid.uuid4())
        ss.chat = []
        ss.pending = None
    return {"configurable": {"thread_id": ss.thread_id}}


def _run(message=None, resume=None):
    ss = st.session_state
    cfg = _thread()
    inp = Command(resume=resume) if resume is not None else {"message": message, "user": ss.user_email}
    try:
        out = get_graph().invoke(inp, cfg)
    except Exception as e:
        ss.chat.append({"role": "assistant", "resp": {"text": f"Something went wrong: {e}", "kind": "text"}})
        ss.pending = None
        return
    if out.get("__interrupt__"):
        ss.pending = out["__interrupt__"][0].value
        return
    ss.pending = None
    ss.chat.append({"role": "assistant", "resp": out.get("response") or {"text": "…", "kind": "text"}})


def _footer(resp):
    cost = resp.get("cost") or 0
    src = "AI-assisted" if resp.get("route") == "llm" or cost else "Answered instantly"
    return f'<div class="aa-foot">{src} · cost {"$%.5f" % cost if cost else "$0"}</div>'


def render_response(resp: dict, idx: int, user: str):
    kind = resp.get("kind")
    if resp.get("text"):
        st.markdown(resp["text"])
    if resp.get("breakdown"):
        st.caption(resp["breakdown"])
    if kind in ("stats", "count") and resp.get("table"):
        st.dataframe(pd.DataFrame(resp["table"]), hide_index=True, width="stretch")
        if resp.get("table2"):
            st.markdown("**By project industry sector**")
            t2 = pd.DataFrame(resp["table2"])
            st.dataframe(t2, hide_index=True, width="stretch", column_config={
                "Candidates": st.column_config.ProgressColumn("Candidates", format="%d", min_value=0,
                                                              max_value=int(t2["Candidates"].max()))})
    if kind == "compare" and resp.get("table"):
        st.dataframe(pd.DataFrame(resp["table"]).set_index("Candidate").T, width="stretch")
    if resp.get("cards"):
        off = resp.get("offset", 0)
        for j, info in enumerate(resp["cards"]):
            pos = info.get("pos") or (off + j + 1 if kind == "list" else None)
            C.candidate_card(info, key=f"{idx}_{j}", user=user, pos=pos, detail=info.get("detail", False),
                             compact=resp.get("compact", False))
    if kind == "resume":
        for cid in resp.get("ids", []):
            c = store.get_candidate(cid)
            fn, data = store.get_file(cid)
            st.markdown(f"**{c.get('name', '')}** — {fn or ''}")
            a, b, _ = st.columns([1, 1, 4])
            if a.button("View resume", key=f"rv_{idx}_{cid}", width="stretch"):
                C.resume_dialog(cid)
            if data:
                b.download_button("Download", data, file_name=fn, key=f"rd_{idx}_{cid}", width="stretch")
    st.markdown(_footer(resp), unsafe_allow_html=True)


def chat_page():
    ss = st.session_state
    user = ss.user_email
    _thread()
    for t in ss.pop("toasts", []):
        st.toast(t)

    df = store.candidates_df()
    theme.brand_bar("Candidate search assistant",
                    kpis=[("Candidates", len(df)), ("Openings", len(store.openings(active_only=True))),
                          ("Sectors", len({s for x in df["project_sectors"] for s in x}))])

    if ss.chat or ss.get("pending"):
        _, right = st.columns([5, 1])
        if right.button("New conversation", width="stretch"):
            for k in ("thread_id", "chat", "pending"):
                ss.pop(k, None)
            st.rerun()

    # ---------------- empty state ----------------
    if not ss.chat and not ss.get("pending") and not ss.get("queued"):
        st.markdown("""<div class="aa-hero"><h2>Who are you looking for today?</h2>
            <p>Search by job category, experience, industry projects, skills or location — then contact candidates directly.</p></div>""",
                    unsafe_allow_html=True)
        _, mid, _ = st.columns([1, 6, 1])
        with mid:
            cols = st.columns(3)
            for i, s in enumerate(SUGGESTIONS):
                if cols[i % 3].button(s, key=f"hero_{i}", width="stretch"):
                    ss.queued = s
                    st.rerun()
            st.markdown('<div class="aa-foot" style="text-align:center;margin-top:14px">Type <b>help</b> to see everything '
                        'the assistant can do. Emails are never sent without your confirmation.</div>',
                        unsafe_allow_html=True)

    # ---------------- history ----------------
    for i, m in enumerate(ss.chat):
        if m["role"] == "user":
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(m["text"])
        else:
            with st.chat_message("assistant", avatar=BOT_AVATAR):
                render_response(m["resp"], i, user)
                if st.button("Close", key=f"close_{i}"):
                    ss.chat = ss.chat[:max(i - 1, 0)] + ss.chat[i + 1:]
                    st.rerun()

    # ---------------- pending approval (LangGraph interrupt) ----------------
    if ss.get("pending"):
        with st.chat_message("assistant", avatar=BOT_AVATAR):
            action, drafts = C.drafts_editor(ss.pending, key=f"pend_{len(ss.chat)}")
        if action == "send":
            with st.spinner("Sending emails…"):
                _run(resume={"action": "send", "drafts": drafts})
            st.rerun()
        elif action == "cancel":
            _run(resume={"action": "cancel"})
            st.rerun()

    # ---------------- input ----------------
    prompt = st.chat_input("Ask about candidates — e.g. “BDE freshers in Chennai”, or paste “JD: …” to match a job description",
                           disabled=bool(ss.get("pending")))
    prompt = prompt or ss.pop("queued", None)
    if prompt:
        ss.chat.append({"role": "user", "text": prompt})
        with st.spinner("Searching…"):
            _run(message=prompt)
        st.rerun()
