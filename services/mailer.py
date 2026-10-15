"""
Email: template personalisation (pure Python, 0 tokens) + SMTP sending + logging.
"""
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from core import store
from core.config import Config

PLACEHOLDERS = {
    "first_name": "Candidate first name", "full_name": "Candidate full name",
    "category": "Job category", "experience": "'3.5 years of experience' / 'early-career profile'",
    "top_skills": "Top 5 skills", "current_role": "Latest title", "current_company": "Latest company",
    "sector": "Industry sector", "project_names": "Projects (comma list)", "project_list": "Projects (bullets)",
    "role_title": "Job opening title", "job_location": "Opening location / mode", "link": "Opening link",
    "company_name": "Your company", "sender_name": "Sender / team name",
}
OPTIONAL = ("link", "job_location", "current_role", "current_company")


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def experience_phrase(y) -> str:
    y = float(y or 0)
    if y < 0.5:
        return "early-career profile"
    if y < 1:
        return f"{int(round(y * 12))} months of experience"
    return f"{y:g} years of experience"


def first_name(name: str) -> str:
    parts = [p for p in re.split(r"[\s.]+", (name or "").strip()) if p]
    if not parts:
        return "there"
    first = parts[0]
    if len(first) <= 2 and len(parts) > 1:
        first = parts[1] if len(parts[1]) > 2 else parts[-1]
    return first.title()


def build_context(c: dict, opening: dict = None, sector: str = None, settings: dict = None) -> dict:
    settings = settings or store.get_settings()
    projs = [p for p in c.get("projects", []) if not str(p.get("name", "")).startswith("(auto")]
    if sector:
        sp = [p for p in projs if p.get("sector") == sector]
        projs = sp or projs
    else:
        sp = [p for p in projs if p.get("sector") not in ("General", "Technology")]
        projs = sp or projs
    names = [p["name"] for p in projs][:4]
    main_sector = sector or (projs[0]["sector"] if projs and projs[0].get("sector") not in ("General",) else "your domain")
    op = opening or {}
    loc = " · ".join(x for x in [op.get("location", ""), op.get("mode", "")] if x)
    return {
        "first_name": first_name(c.get("name")), "full_name": c.get("name", ""),
        "category": c.get("category") or "data",
        "experience": experience_phrase(c.get("total_experience")),
        "top_skills": ", ".join(list(c.get("skills") or [])[:5]) or "your technical skills",
        "current_role": c.get("current_role", ""), "current_company": c.get("current_company", ""),
        "sector": main_sector,
        "project_names": ", ".join(names) or "your recent projects",
        "project_list": "\n".join(f"• {n}" for n in names) or "• your recent projects",
        "role_title": op.get("title") or c.get("category") or "open",
        "job_location": loc, "link": op.get("link", ""),
        "company_name": settings.get("company_name", "Analytics Avenue"),
        "sender_name": settings.get("sender_name", "Talent Team"),
    }


def render(template: str, ctx: dict) -> str:
    for k in OPTIONAL:
        if not ctx.get(k):
            template = re.sub(r"\s*\([^()\n]*\{" + k + r"\}[^()\n]*\)", "", template)
            template = re.sub(r"^[^\n]*\{" + k + r"\}[^\n]*(\n|$)", "", template, flags=re.M)
    out = template.format_map(_Safe(ctx))
    out = re.sub(r"(?<=\S) {2,}", " ", out)
    out = re.sub(r" +([,.])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def draft(c: dict, template: dict, opening: dict = None, sector: str = None) -> dict:
    ctx = build_context(c, opening, sector)
    return {
        "candidate_id": c.get("_id") or c.get("id"), "name": c.get("name", ""), "email": c.get("email", ""),
        "subject": render(template["subject"], ctx), "body": render(template["body"], ctx),
        "template": template["_id"], "opening": (opening or {}).get("title", ""),
    }


def _html(body: str) -> str:
    h = escape(body).replace("\n", "<br>")
    h = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', h)
    return ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.55;color:#0f1f3d">'
            f"{h}</div>")


class Mailer:
    def __enter__(self):
        if not Config.smtp_ready():
            raise RuntimeError("Email is not configured — add SMTP_USER, SMTP_PASSWORD, FROM_EMAIL in secrets.")
        ctx = ssl.create_default_context()
        if Config.SMTP_PORT == 465:
            self.s = smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, context=ctx, timeout=30)
        else:
            self.s = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30)
            self.s.starttls(context=ctx)
        self.s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        return self

    def send(self, to, subject, body, sender_name, reply_to=""):
        m = EmailMessage()
        m["From"] = formataddr((sender_name, Config.FROM_EMAIL))
        m["To"] = to
        m["Subject"] = subject
        m["Message-ID"] = make_msgid()
        if reply_to:
            m["Reply-To"] = reply_to
        m.set_content(body)
        m.add_alternative(_html(body), subtype="html")
        self.s.send_message(m)

    def __exit__(self, *a):
        try:
            self.s.quit()
        except Exception:
            pass


def send(drafts: list, sent_by: str, progress=None) -> dict:
    """Guardrails: batch cap, cooldown, valid email. Logs every attempt."""
    st = store.get_settings()
    cap, cool = int(st.get("max_email_batch", 25)), int(st.get("email_cooldown_days", 30))
    res = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}
    todo = []
    for d in drafts[:cap]:
        if not re.match(r"[^@\s]+@[^@\s]+\.[a-z]{2,}$", d.get("email") or "", re.I):
            res["skipped"] += 1
            res["errors"].append(f"{d.get('name')}: no valid email")
        elif cool and not d.get("force") and store.recently_contacted(d["candidate_id"], cool):
            res["skipped"] += 1
            res["errors"].append(f"{d.get('name')}: already emailed in the last {cool} days")
        else:
            todo.append(d)
    if len(drafts) > cap:
        res["skipped"] += len(drafts) - cap
        res["errors"].append(f"Only the first {cap} emails were sent (batch limit).")
    if not todo:
        return res
    with Mailer() as m:
        for i, d in enumerate(todo):
            try:
                m.send(d["email"], d["subject"], d["body"], st.get("sender_name"), st.get("reply_to"))
                store.log_email(d["candidate_id"], d["email"], d["name"], d["subject"], d["body"],
                                d.get("template", ""), d.get("opening", ""), sent_by, "sent")
                res["sent"] += 1
            except Exception as e:
                store.log_email(d["candidate_id"], d["email"], d["name"], d["subject"], d["body"],
                                d.get("template", ""), d.get("opening", ""), sent_by, "failed", str(e))
                res["failed"] += 1
                res["errors"].append(f"{d['email']}: {e}")
            if progress:
                progress((i + 1) / len(todo), f"{i + 1}/{len(todo)} → {d['email']}")
            if i < len(todo) - 1:
                time.sleep(float(st.get("email_delay_seconds", 1.5)))
    return res


def polish_template(subject: str, body: str, user: str) -> tuple:
    """Optional: 1 AI call to improve a TEMPLATE (placeholders kept, so personalisation stays free)."""
    from core.llm import LLM
    prompt = ("Improve this recruiting email template: professional, warm, concise (under 150 words). "
              "Keep EVERY {placeholder} exactly as written and add no new ones. "
              'Return JSON {"subject": str, "body": str}.\n\n'
              f"SUBJECT: {subject}\n\nBODY:\n{body}")
    data = LLM("chat", user=user).json_chat("You are a recruiting copywriter.", prompt, max_tokens=700, temperature=0.4)
    return data.get("subject", subject), data.get("body", body)
