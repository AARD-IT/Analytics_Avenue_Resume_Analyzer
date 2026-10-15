"""Reusable UI pieces: candidate card, resume viewer, email composer, drafts approval."""
from html import escape

import pandas as pd
import streamlit as st

from core import store
from core.config import Config
from services import mailer
from services.ingest import pdf_pages_png
from ui.theme import chip


def _cand(cid):
    df = store.candidates_df(include_duplicates=True)
    row = df[df["id"] == cid]
    return row.iloc[0].to_dict() if len(row) else {}


def _fmt_date(v):
    try:
        return pd.to_datetime(v).strftime("%d %b %Y")
    except Exception:
        return ""


# ------------------------------------------------------------------ dialogs
@st.dialog("Resume", width="large")
def resume_dialog(cid: str):
    c = _cand(cid)
    fn, data = store.get_file(cid)
    st.markdown(f"### {escape(c.get('name', ''))}")
    if not data:
        st.warning("The original file isn't stored for this candidate.")
        return
    a, b = st.columns(2)
    a.download_button("Download", data, file_name=fn, width="stretch", key=f"dl_{cid}")
    if c.get("drive_file_id"):
        b.link_button("Open in Google Drive", f"https://drive.google.com/file/d/{c['drive_file_id']}/view", width="stretch")
    pages = pdf_pages_png(data, fn)
    if pages:
        for p in pages:
            st.image(p, width="stretch")
    else:
        st.info("Preview is available for PDF files. Use Download for this file type.")


@st.dialog("Email candidate", width="large")
def email_dialog(cid: str, sector: str = None, user: str = ""):
    c = store.get_candidate(cid)
    if not c:
        st.error("Candidate not found.")
        return
    if not c.get("email"):
        st.error("This candidate has no email address on their resume.")
        return
    tpls = store.templates()
    ops = store.openings(active_only=True)
    s = store.get_settings()
    names = [t["_id"] for t in tpls]
    default = names.index(s.get("default_template")) if s.get("default_template") in names else 0
    a, b = st.columns(2)
    tname = a.selectbox("Template", names, index=default, key=f"ed_t_{cid}")
    op_titles = ["— none —"] + [f"{o['title']} · {o.get('location', '')}" for o in ops]
    guess = 0
    for i, o in enumerate(ops):
        if o.get("category") == c.get("category"):
            guess = i + 1
            break
    oi = b.selectbox("Job opening", range(len(op_titles)), index=guess, format_func=lambda i: op_titles[i], key=f"ed_o_{cid}")
    opening = ops[oi - 1] if oi else {}
    tpl = next(t for t in tpls if t["_id"] == tname)
    d = mailer.draft(c, tpl, opening, sector)
    ver = f"{cid}_{tname}_{oi}"
    st.markdown(f"**To:** {escape(c.get('name', ''))} &lt;{escape(c['email'])}&gt;", unsafe_allow_html=True)
    subject = st.text_input("Subject", d["subject"], key=f"ed_s_{ver}")
    body = st.text_area("Message", d["body"], height=300, key=f"ed_b_{ver}")
    recent = store.recently_contacted(cid, int(s.get("email_cooldown_days", 30)))
    force = False
    if recent:
        st.warning(f"Already emailed on {_fmt_date(recent['at'])}.")
        force = st.checkbox("Send anyway", key=f"ed_f_{cid}")
    if not Config.smtp_ready():
        st.error("Email sending isn't configured (SMTP secrets). Copy the text above instead.")
        return
    if st.button("Send email", type="primary", width="stretch", key=f"ed_send_{cid}",
                 disabled=bool(recent) and not force):
        d.update(subject=subject, body=body, force=force)
        with st.spinner("Sending…"):
            try:
                res = mailer.send([d], sent_by=user)
            except Exception as e:
                st.error(f"Could not send: {e}")
                return
        if res["sent"]:
            st.success(f"Email sent to {c['email']}")
            st.session_state.setdefault("toasts", []).append(f"Email sent to {c.get('name')}")
        else:
            st.error("; ".join(res["errors"]) or "Not sent.")


# ------------------------------------------------------------------ card
def candidate_card(info: dict, key: str, user: str, pos: int = None, detail: bool = False, compact: bool = False):
    c = _cand(info["id"])
    if not c:
        return
    sectors = info.get("sectors") or []
    projs = [p for p in c["projects"] if not str(p.get("name", "")).startswith("(auto")]
    rel = [p for p in projs if sectors and p.get("sector") in sectors] or \
          [p for p in projs if p.get("sector") not in ("General", "Technology")] or projs
    exp = c["total_experience"]
    exp_txt = "Fresher" if exp < 0.5 and not c["internship_experience"] else f"{exp:g} yrs exp"
    chips = [chip(c["category"]), chip(exp_txt)]
    if c["internship_experience"]:
        chips.append(chip(f"{c['internship_experience']:g} yrs internship", "o"))
    for s in (c["project_sectors"] or [])[:3]:
        chips.append(chip(s, "g" if s in sectors else ""))
    if c.get("location"):
        chips.append(chip(c["location"]))
    score = f'<span class="aa-score">{info["match"]}% match</span>' if info.get("match") is not None else ""
    num = f'<span class="aa-num">#{pos}</span>' if pos else ""
    role = " @ ".join(x for x in [c.get("current_role"), c.get("current_company")] if x)
    email, phone = c.get("email") or "", c.get("phone") or ""
    contact = ('<span class="lbl">Email</span>' + (f'<a href="mailto:{escape(email)}">{escape(email)}</a>' if email else "—")
               + '<span class="sep"></span><span class="lbl">Phone</span>'
               + (f'<a href="tel:{escape(phone.replace(" ", ""))}">{escape(phone)}</a>' if phone else "—"))
    n_proj = 4 if detail else (0 if compact else 2)
    proj_html = "".join(
        f'<div class="aa-proj"><b>{escape(p["name"])}</b> <span class="s">· {escape(p.get("sector", ""))}</span>'
        + (f' <span class="d">— {escape(p.get("description", "")[:140])}</span>' if detail or sectors else "") + "</div>"
        for p in rel[:n_proj])
    skills = ", ".join(c["skills"][:8])
    matched = info.get("matched_skills") or []
    extra = f'<div class="aa-meta"><span class="lbl">Matched skills</span>{escape(", ".join(matched))}</div>' if matched else ""
    if not compact:
        extra += f'<div class="aa-meta"><span class="lbl">Skills</span>{escape(skills)}</div>' if skills else ""
    last = store.last_contacted().get(c["id"])
    if last:
        extra += f'<div class="aa-meta"><span class="lbl">Last emailed</span>{_fmt_date(last)}</div>'
    st.markdown(f"""<div class="aa-card">
        <div class="aa-row">{num}<span class="aa-name">{escape(c['name'])}</span>
        <span class="aa-role">{escape(role)}</span>{score}</div>
        <div style="margin-top:6px">{''.join(chips)}</div>
        <div class="aa-contact">{contact}</div>{proj_html}{extra}</div>""", unsafe_allow_html=True)
    b1, b2, b3, _ = st.columns([1, 0.8, 0.8, 6])
    if b1.button("View resume", key=f"r_{key}", width="stretch"):
        resume_dialog(c["id"])
    if b2.button("Email", key=f"e_{key}", width="stretch", disabled=not email):
        email_dialog(c["id"], sectors[0] if sectors else None, user)
    if not detail and b3.button("Details", key=f"d_{key}", width="stretch"):
        st.session_state[f"open_{key}"] = not st.session_state.get(f"open_{key}", False)
    if detail or st.session_state.get(f"open_{key}"):
        details_block(c["id"])


def details_block(cid: str):
    c = store.get_candidate(cid)
    with st.container(border=True):
        if c.get("summary"):
            st.markdown(f"*{escape(c['summary'])}*")
        w = c.get("work") or []
        if w:
            st.markdown("**Work history**")
            st.dataframe(pd.DataFrame([{
                "Role": x.get("role"), "Company": x.get("company"), "Industry": x.get("industry"),
                "From": x.get("start"), "To": x.get("end"),
                "Type": "Internship" if x.get("is_internship") else "Job"} for x in w]),
                hide_index=True, width="stretch")
        p = [x for x in c.get("projects") or [] if not str(x.get("name", "")).startswith("(auto")]
        if p:
            st.markdown("**Projects**")
            st.dataframe(pd.DataFrame([{"Project": x.get("name"), "Sector": x.get("sector"),
                                        "Tech": x.get("tech_stack"), "About": x.get("description")} for x in p]),
                         hide_index=True, width="stretch")
        if c.get("education"):
            st.markdown("**Education:** " + " · ".join(
                f"{e.get('degree')} — {e.get('institution')} {e.get('year')}" for e in c["education"]))
        if c.get("certifications"):
            st.markdown("**Certifications:** " + ", ".join(c["certifications"]))
        st.caption(f"All skills: {', '.join(c.get('skills') or [])}")


# ------------------------------------------------------------------ bulk drafts (HITL)
def drafts_editor(payload: dict, key: str):
    """Shows drafts from the paused graph. Returns ('send', drafts) | ('cancel', None) | (None, None)."""
    drafts = payload["drafts"]
    s = store.get_settings()
    cool = int(s.get("email_cooldown_days", 30))
    st.markdown(f"**{len(drafts)} email draft(s) ready** · template *{payload.get('template')}*"
                + (f" · opening *{payload.get('opening')}*" if payload.get("opening") else "")
                + " — review, edit, then send.")
    edited = []
    for i, d in enumerate(drafts):
        flag = ""
        if not d.get("email"):
            flag = "  (no email address)"
        elif store.recently_contacted(d["candidate_id"], cool):
            flag = f"  (emailed in the last {cool} days, will be skipped)"
        with st.expander(f"{i + 1}. {d['name']} <{d.get('email') or '—'}>{flag}", expanded=(i == 0)):
            inc = st.checkbox("Include", value=bool(d.get("email")), key=f"{key}_inc_{i}")
            sub = st.text_input("Subject", d["subject"], key=f"{key}_s_{i}")
            body = st.text_area("Message", d["body"], height=240, key=f"{key}_b_{i}")
            if inc:
                edited.append({**d, "subject": sub, "body": body})
    if not Config.smtp_ready():
        st.error("Email sending isn't configured (SMTP secrets) — you can copy the drafts above.")
    a, b, _ = st.columns([1.3, 1, 3])
    if a.button(f"Send {len(edited)} email(s)", type="primary", key=f"{key}_send",
                disabled=not edited or not Config.smtp_ready(), width="stretch"):
        return "send", edited
    if b.button("Cancel", key=f"{key}_cancel", width="stretch"):
        return "cancel", None
    return None, None
