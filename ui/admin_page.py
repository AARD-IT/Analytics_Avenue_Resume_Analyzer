"""Admin page — protected by ADMIN_PASSWORD."""
import pandas as pd
import streamlit as st

from core import store
from core.config import Config, get_secret
from core.taxonomy import CATEGORIES
from services import ingest, mailer
from ui import theme


def _require_admin():
    if st.session_state.get("user_role") != "admin":
        st.error("Admins only.")
        st.stop()


def _progress_cb():
    bar = st.progress(0.0, "Starting…")
    return lambda f, t: bar.progress(min(max(f, 0.0), 1.0), t)


def _report(rep: dict):
    if rep.get("error"):
        st.error(rep["error"])
        return
    msg = (f"Processed **{rep.get('ok', 0)}** · skipped {rep.get('skipped', 0)} · "
           f"no text {rep.get('no_text', 0)} · errors {rep.get('errors', 0)}")
    if rep.get("llm_calls") is not None:
        msg += f" · AI calls {rep['llm_calls']} · cost ${rep.get('cost_usd', 0):.4f}"
    if "new_files" in rep:
        msg = f"Drive: {rep['listed']} files listed, {rep['new_files']} new. " + msg
    st.success(msg)
    if not rep.get("llm"):
        st.warning("No AI key configured — files were processed in free rules-only mode (lower accuracy).")
    if rep.get("stopped"):
        st.warning(f"Stopped early: {rep['stopped']}")
    for e in rep.get("drive_errors", [])[:10]:
        st.error(e)


# ------------------------------------------------------------------ sections
def resumes_tab():
    df = store.candidates_df(include_duplicates=True)
    outdated = int((df["model"].isin(["legacy", "heuristic"])).sum()) if len(df) else 0
    c = st.columns(4)
    c[0].markdown(theme.stat(int(df["duplicate_of"].isna().sum() + (df["duplicate_of"] == "").sum()) if len(df) else 0,
                             "Active candidates"), unsafe_allow_html=True)
    c[1].markdown(theme.stat(len(df) - outdated, "AI-extracted"), unsafe_allow_html=True)
    c[2].markdown(theme.stat(outdated, "Need AI upgrade"), unsafe_allow_html=True)
    c[3].markdown(theme.stat(len(store.failed_files()), "Files with issues"), unsafe_allow_html=True)
    st.write("")

    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("#### Upload resumes")
        files = st.file_uploader("PDF, DOCX or TXT", type=["pdf", "docx", "txt"], accept_multiple_files=True,
                                 key="up_files")
        if st.button(f"Process {len(files or [])} file(s)", type="primary", disabled=not files):
            items = [(ingest.safe_name(f.name), f.getvalue(), "upload", "") for f in files]
            with st.spinner("Reading resumes…"):
                _report(ingest.run_batch(items, user=st.session_state.user_email, progress=_progress_cb()))
    with right, st.container(border=True):
        st.markdown("#### Google Drive folders")
        s = store.get_settings()
        cur = s.get("drive_folders") or ([Config.DEFAULT_DRIVE_FOLDER] if Config.DEFAULT_DRIVE_FOLDER else [])
        txt = st.text_area("One folder link or ID per line", "\n".join(cur), height=96, key="drive_txt")
        a, b = st.columns(2)
        if a.button("Save folders", width="stretch"):
            store.save_settings({"drive_folders": [ingest.folder_id_from(x) for x in txt.splitlines() if x.strip()]})
            st.success("Saved.")
        if b.button("Sync now", type="primary", width="stretch"):
            with st.spinner("Syncing Drive…"):
                _report(ingest.sync_drive(user=st.session_state.user_email, progress=_progress_cb()))
        sa = get_secret("gcp_service_account")
        if sa:
            st.caption(f"Share each folder (Viewer) with: `{dict(sa).get('client_email', '')}`")
        else:
            st.caption("Add a Google service account in secrets to enable Drive sync.")

    with st.container(border=True):
        st.markdown("#### AI upgrade")
        est = outdated * 0.0026
        st.write(f"**{outdated}** resume(s) were read by the old/free extractor. Re-read them with "
                 f"`{store.get_settings().get('extract_model')}` for accurate categories, sectors, projects, "
                 f"education and location — one-time cost ≈ **${est:.2f}**.")
        if st.button("Upgrade now", disabled=not outdated or not Config.llm_provider()):
            with st.spinner("Re-reading resumes with AI…"):
                _report(ingest.reprocess_all(user=st.session_state.user_email, progress=_progress_cb()))

    st.markdown("#### Candidates")
    q = st.text_input("Filter by name / email", key="adm_q")
    view = df.copy()
    if q:
        view = view[view["name"].str.contains(q, case=False) | view["email"].str.contains(q, case=False)]
    view = view.assign(Delete=False, duplicate=view["duplicate_of"].notna() & (view["duplicate_of"] != ""))
    cols = ["Delete", "name", "email", "phone", "category", "total_experience", "internship_experience", "location",
            "model", "source", "duplicate", "filename"]
    ed = st.data_editor(
        view[cols], hide_index=True, width="stretch", height=420, key="adm_cand",
        column_config={
            "Delete": st.column_config.CheckboxColumn("Delete", width="small"),
            "category": st.column_config.SelectboxColumn("Category", options=CATEGORIES),
            "total_experience": st.column_config.NumberColumn("Exp", min_value=0.0, max_value=50.0, step=0.1, format="%.1f"),
            "internship_experience": st.column_config.NumberColumn("Intern", min_value=0.0, max_value=20.0, step=0.1, format="%.1f"),
        },
        disabled=["model", "source", "duplicate", "filename"],
    )
    a, b, _ = st.columns([1, 1, 3])
    if a.button("Save changes"):
        changed = 0
        orig = view.set_index(view.index)
        for i in ed.index:
            cid = view.loc[i, "id"]
            upd = {}
            for col in ("name", "email", "phone", "category", "total_experience", "internship_experience", "location"):
                if ed.loc[i, col] != orig.loc[i, col]:
                    upd[col] = ed.loc[i, col]
            if upd:
                store.update_candidate(cid, upd)
                changed += 1
        st.success(f"Saved {changed} change(s).")
        st.rerun()
    n_del = int(ed["Delete"].sum())
    if b.button(f"Delete {n_del} selected", disabled=not n_del):
        for i in ed.index[ed["Delete"]]:
            store.delete_candidate(view.loc[i, "id"])
        st.success(f"Deleted {n_del}.")
        st.rerun()
    ff = store.failed_files()
    if len(ff):
        with st.expander(f"{len(ff)} file(s) with issues"):
            st.dataframe(ff, hide_index=True, width="stretch")


def openings_tab():
    st.caption("Job openings are used in emails ({role_title}, {job_location}, {link}). Drag order with the **Order** column.")
    ops = store.openings()
    df = pd.DataFrame(ops, columns=["_id", "order", "title", "category", "location", "mode", "link", "description", "active"])
    df = df.rename(columns={"_id": "id"})
    ed = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key="adm_ops",
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
            "order": st.column_config.NumberColumn("Order", min_value=1, step=1, width="small"),
            "title": st.column_config.TextColumn("Title", required=True),
            "category": st.column_config.SelectboxColumn("Category", options=CATEGORIES),
            "mode": st.column_config.SelectboxColumn("Mode", options=["On-site", "Hybrid", "Remote"]),
            "link": st.column_config.LinkColumn("Link (JD / form)"),
            "description": st.column_config.TextColumn("Description", width="large"),
            "active": st.column_config.CheckboxColumn("Active"),
        },
    )
    if st.button("Save openings", type="primary"):
        keep = set()
        for _, r in ed.iterrows():
            if not str(r.get("title") or "").strip():
                continue
            op = {k: (None if pd.isna(r[k]) else r[k]) for k in ["title", "category", "location", "mode", "link",
                                                                  "description", "active", "order"]}
            op["active"] = bool(op["active"]) if op["active"] is not None else True
            op["order"] = int(op["order"] or 99)
            for k in ("location", "mode", "link", "description", "category"):
                op[k] = op[k] or ""
            if isinstance(r.get("id"), str) and r["id"]:
                op["_id"] = r["id"]
            keep.add(store.save_opening(op))
        for o in ops:
            if o["_id"] not in keep:
                store.delete_opening(o["_id"])
        st.success("Openings saved.")
        st.rerun()


def templates_tab():
    tpls = store.templates()
    s = store.get_settings()
    names = [t["_id"] for t in tpls]
    a, b = st.columns([2, 1])
    choice = a.selectbox("Template", names + ["+ New template"], key="tpl_sel")
    is_new = choice == "+ New template"
    t = {"_id": "", "subject": "", "body": "", "order": len(tpls) + 1} if is_new else next(x for x in tpls if x["_id"] == choice)
    if "_tpl_pending" in st.session_state:
        p = st.session_state.pop("_tpl_pending")
        st.session_state[f"tpl_s_{choice}"], st.session_state[f"tpl_b_{choice}"] = p
    b.markdown(f"<br>Default: **{s.get('default_template')}**", unsafe_allow_html=True)
    left, right = st.columns([1.3, 1])
    with left:
        name = st.text_input("Name", t["_id"], key=f"tpl_n_{choice}", disabled=not is_new)
        subj = st.text_input("Subject", t["subject"], key=f"tpl_s_{choice}")
        body = st.text_area("Body", t["body"], height=340, key=f"tpl_b_{choice}")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Save", type="primary", width="stretch"):
            if not name.strip():
                st.error("Name required.")
            else:
                store.save_template(name, subj, body, order=t.get("order", 99))
                st.success("Saved.")
                st.rerun()
        if c2.button("Set as default", width="stretch", disabled=is_new):
            store.save_settings({"default_template": choice})
            st.rerun()
        if c3.button("Improve with AI", width="stretch", disabled=not Config.llm_provider(),
                     help="1 small AI call — rewrites the template, placeholders are kept."):
            try:
                st.session_state["_tpl_pending"] = mailer.polish_template(subj, body, st.session_state.user_email)
                st.rerun()
            except Exception as e:
                st.error(str(e))
        if c4.button("Delete", width="stretch", disabled=is_new or len(tpls) <= 1):
            store.delete_template(choice)
            st.rerun()
    with right:
        st.markdown("**Live preview** (sample candidate)")
        df = store.candidates_df()
        if len(df):
            sample = store.get_candidate(df.sort_values("total_experience", ascending=False).iloc[0]["id"])
            ops = store.openings(active_only=True)
            ctx = mailer.build_context(sample, ops[0] if ops else {})
            with st.container(border=True):
                st.markdown(f"**{mailer.render(subj, ctx)}**")
                st.text(mailer.render(body, ctx))
        with st.expander("Placeholders"):
            st.markdown("\n".join(f"- `{{{k}}}` — {v}" for k, v in mailer.PLACEHOLDERS.items()))


def settings_tab():
    s = store.get_settings()
    models = list(Config.PRICES.keys())
    with st.form("settings"):
        st.markdown("##### Company & email")
        a, b, c = st.columns(3)
        company = a.text_input("Company name", s["company_name"])
        sender = b.text_input("Sender name", s["sender_name"])
        reply = c.text_input("Reply-to email", s.get("reply_to", ""))
        a, b, c = st.columns(3)
        batch = a.number_input("Max emails per batch", 1, 200, int(s["max_email_batch"]))
        cool = b.number_input("Don't re-email within (days)", 0, 365, int(s["email_cooldown_days"]))
        delay = c.number_input("Delay between emails (s)", 0.0, 10.0, float(s["email_delay_seconds"]), 0.5)
        st.markdown("##### AI models & budget")
        a, b, c = st.columns(3)
        ext = a.selectbox("Resume extraction", models, index=models.index(s["extract_model"]) if s["extract_model"] in models else 0)
        chat = b.selectbox("Chat / routing", models, index=models.index(s["chat_model"]) if s["chat_model"] in models else 0)
        fb = c.selectbox("Fallback", models, index=models.index(s["fallback_model"]) if s["fallback_model"] in models else 0)
        a, b, _ = st.columns(3)
        budget = a.number_input("Daily AI budget (USD, 0 = no limit)", 0.0, 1000.0, float(s["daily_budget_usd"]), 0.5)
        per = b.number_input("Candidates per answer", 3, 30, int(s["results_per_answer"]))
        if st.form_submit_button("Save settings", type="primary"):
            store.save_settings({"company_name": company, "sender_name": sender, "reply_to": reply,
                                 "max_email_batch": int(batch), "email_cooldown_days": int(cool),
                                 "email_delay_seconds": float(delay), "extract_model": ext, "chat_model": chat,
                                 "fallback_model": fb, "daily_budget_usd": float(budget), "results_per_answer": int(per)})
            st.success("Saved.")
    st.markdown("##### System status")
    rows = [
        ("Database", "MongoDB Atlas (connected)" if store.persistent() else "In-memory — set MONGODB_URI so data survives restarts"),
        ("AI provider", (Config.llm_provider() or "not configured").title()),
        ("Email (SMTP)", f"Configured ({Config.FROM_EMAIL})" if Config.smtp_ready() else "Not configured"),
        ("Google Drive", "Configured" if get_secret("gcp_service_account") or get_secret("GCP_SERVICE_ACCOUNT") else "Not configured"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["Component", "Status"]), hide_index=True, width="stretch")


def usage_tab():
    u = store.usage_df(30)
    q = store.questions_df(30)
    c = st.columns(4)
    c[0].markdown(theme.stat(f"${store.cost_today():.4f}", "AI cost today"), unsafe_allow_html=True)
    c[1].markdown(theme.stat(f"${u['cost'].sum():.3f}" if len(u) else "$0", "AI cost · 30 days"), unsafe_allow_html=True)
    c[2].markdown(theme.stat(len(q), "Questions · 30 days"), unsafe_allow_html=True)
    free = (100 * (1 - q["llm"].mean())) if len(q) else 100
    c[3].markdown(theme.stat(f"{free:.0f}%", "Answered without AI"), unsafe_allow_html=True)
    st.write("")
    if len(u):
        u["day"] = pd.to_datetime(u["at"]).dt.strftime("%Y-%m-%d")
        st.markdown("**AI cost per day (USD)**")
        st.bar_chart(u.groupby("day")["cost"].sum(), color="#1565D8", height=220)
        st.dataframe(u.groupby(["kind", "model"]).agg(calls=("cost", "size"), tokens=("tokens", "sum"),
                                                      cost=("cost", "sum")).reset_index(), hide_index=True, width="stretch")
    st.markdown("**Recent questions**")
    st.dataframe(q.head(200), hide_index=True, width="stretch")
    st.markdown("**Email log**")
    lg = store.email_log_df()
    st.dataframe(lg, hide_index=True, width="stretch")
    if len(lg):
        st.download_button("Export email log", lg.to_csv(index=False), "email_log.csv")
    with st.expander("Agent graph (LangGraph)"):
        from agent.graph import mermaid
        st.code(mermaid() or "n/a", language="text")


def admin_page(on_lock=None):
    _require_admin()
    theme.brand_bar("Admin console · resumes, openings, templates & settings", pill="Admin")
    if on_lock:
        _, right = st.columns([6, 1])
        if right.button("Lock admin", width="stretch"):
            on_lock()
    if not store.persistent():
        st.warning("Running on an **in-memory database** — set `MONGODB_URI` in secrets so uploads, templates and "
                   "email logs survive restarts.")
    tabs = st.tabs(["Resumes", "Job openings", "Email templates", "Settings", "Usage & logs"])
    with tabs[0]:
        resumes_tab()
    with tabs[1]:
        openings_tab()
    with tabs[2]:
        templates_tab()
    with tabs[3]:
        settings_tab()
    with tabs[4]:
        usage_tab()
