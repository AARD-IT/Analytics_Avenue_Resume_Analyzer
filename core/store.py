"""
MongoDB data layer (MongoDB Atlas in production).

If MONGODB_URI is not set, an in-memory mock is used so the app still runs
locally — data is then lost on restart (a banner warns admins).
"""
import gzip
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.config import Config

log = logging.getLogger("store")
_lock = threading.Lock()
_client = None
_db = None
_cache = {"version": None, "df": None}

DEFAULT_TEMPLATES = [
    {
        "_id": "Interview Invitation", "order": 1,
        "subject": "Interview opportunity – {role_title} at {company_name}",
        "body": """Hi {first_name},

Greetings from {company_name}! We reviewed your profile and your {experience} in {category} stood out — especially your skills in {top_skills}.

We're hiring for a {role_title} position (location: {job_location}) and would like to invite you for a short interview conversation.

Please reply with your availability for a quick call.
Role details: {link}

Best regards,
{sender_name}
{company_name}""",
    },
    {
        "_id": "Sector Project Outreach", "order": 2,
        "subject": "Your {sector} project experience – {role_title} at {company_name}",
        "body": """Hi {first_name},

We came across your work on {sector} projects, in particular:
{project_list}

This is closely aligned with what our team is building for {sector} clients at {company_name}, and we think you'd be a strong fit for our {role_title} role (location: {job_location}).

Would you be open to a quick call this week? Just reply to this email.
Role details: {link}

Best regards,
{sender_name}
{company_name}""",
    },
    {
        "_id": "Experience-based Opening", "order": 3,
        "subject": "{category} opening at {company_name} – matching your profile",
        "body": """Hi {first_name},

We have an opening for a {role_title} in our {category} team, and your {experience} matches what we're looking for.

Key skills we noticed on your resume: {top_skills}.

If you're interested, please reply with your current notice period and a good time to talk.
Role details: {link}

Thanks,
{sender_name}
{company_name}""",
    },
    {
        "_id": "Talent Pool – Keep in Touch", "order": 4,
        "subject": "Staying in touch – {company_name}",
        "body": """Hi {first_name},

Thank you for sharing your resume with {company_name}. We don't have a matching opening right now, but we've added your {category} profile to our talent pool and will reach out as soon as a suitable role opens up.

Best wishes,
{sender_name}
{company_name}""",
    },
]

DEFAULT_OPENINGS = [
    {"title": "Data Analyst", "category": "Data Analytics", "location": "Chennai", "mode": "Hybrid",
     "link": "", "description": "SQL, Power BI/Tableau, Excel, dashboards, stakeholder reporting.", "active": True, "order": 1},
    {"title": "Data Engineer", "category": "Data Engineering", "location": "Chennai", "mode": "Hybrid",
     "link": "", "description": "Python, SQL, Spark/PySpark, Airflow, cloud data pipelines (Azure/AWS).", "active": True, "order": 2},
    {"title": "Business Development Executive", "category": "Business Development Executive", "location": "Chennai",
     "mode": "On-site", "link": "", "description": "Lead generation, client acquisition, CRM, B2B sales for analytics services.",
     "active": True, "order": 3},
]


def now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ connection
def db():
    global _client, _db
    if _db is not None:
        return _db
    with _lock:
        if _db is not None:
            return _db
        if Config.MONGODB_URI:
            from pymongo import MongoClient
            _client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=8000, tz_aware=True)
        else:
            import mongomock
            log.warning("MONGODB_URI not set — using in-memory database (data NOT persisted).")
            _client = mongomock.MongoClient(tz_aware=True)
        _db = _client[Config.MONGODB_DB]
        _ensure_indexes(_db)
        _bootstrap(_db)
        return _db


def persistent() -> bool:
    return bool(Config.MONGODB_URI)


def _ensure_indexes(d):
    try:
        d.candidates.create_index("category")
        d.candidates.create_index("email")
        d.email_log.create_index([("candidate_id", 1), ("at", -1)])
        d.usage.create_index("at")
    except Exception as e:
        log.warning("index creation skipped: %s", e)


def _bootstrap(d):
    if d.settings.count_documents({"_id": "app"}) == 0:
        d.settings.insert_one({"_id": "app", **Config.DEFAULT_SETTINGS, "data_version": 1})
    if d.templates.count_documents({}) == 0:
        for t in DEFAULT_TEMPLATES:
            d.templates.insert_one({**t, "updated_at": now()})
    if d.openings.count_documents({}) == 0:
        for i, o in enumerate(DEFAULT_OPENINGS):
            d.openings.insert_one({"_id": f"op{i + 1}", **o, "updated_at": now()})
    for em in Config.ADMIN_EMAILS:
        d.users.update_one({"_id": em}, {"$set": {"role": "admin"}, "$setOnInsert": {"added_at": now(), "added_by": "secrets"}},
                           upsert=True)
    if d.candidates.count_documents({}) == 0:
        _seed(d)


def _seed(d):
    """First run: load bundled, already-extracted candidates + their resume files."""
    path = Config.SEED_DIR / "candidates.json.gz"
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as f:
        recs = json.load(f)
    for r in recs:
        r["created_at"] = r["updated_at"] = now()
        d.candidates.replace_one({"_id": r["_id"]}, r, upsert=True)
        fp = Config.SEED_RESUMES / r["filename"]
        if fp.exists() and d.resume_files.count_documents({"_id": r["_id"]}) == 0:
            d.resume_files.insert_one({"_id": r["_id"], "filename": r["filename"], "data": fp.read_bytes(),
                                       "size": fp.stat().st_size})
    log.info("Seeded %d candidates", len(recs))
    dedupe()


def bump_version():
    db().settings.update_one({"_id": "app"}, {"$inc": {"data_version": 1}})


# ------------------------------------------------------------------ settings
def get_settings() -> dict:
    s = db().settings.find_one({"_id": "app"}) or {}
    return {**Config.DEFAULT_SETTINGS, **s}


def save_settings(values: dict):
    db().settings.update_one({"_id": "app"}, {"$set": values}, upsert=True)


# ------------------------------------------------------------------ candidates
_LIST_FIELDS = ["_id", "filename", "name", "email", "phone", "location", "category", "total_experience",
                "internship_experience", "current_role", "current_company", "skills", "summary",
                "project_sectors", "work_sectors", "projects", "work", "model", "source",
                "drive_file_id", "duplicate_of", "updated_at"]


def candidates_df(include_duplicates=False) -> pd.DataFrame:
    """All candidates (no full text). Cached until data_version changes."""
    ver = get_settings().get("data_version")
    if _cache["version"] != ver or _cache["df"] is None:
        rows = list(db().candidates.find({}, {f: 1 for f in _LIST_FIELDS}))
        df = pd.DataFrame(rows, columns=_LIST_FIELDS)
        for col in ("skills", "project_sectors", "work_sectors", "projects", "work"):
            df[col] = df[col].apply(lambda v: v if isinstance(v, list) else [])
        for col in ("name", "email", "phone", "location", "category", "current_role", "current_company",
                    "summary", "filename", "model", "source", "drive_file_id"):
            df[col] = df[col].fillna("").astype(str)
        for col in ("total_experience", "internship_experience"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df = df.rename(columns={"_id": "id"})
        _cache.update(version=ver, df=df)
    df = _cache["df"]
    if not include_duplicates:
        df = df[df["duplicate_of"].isna() | (df["duplicate_of"] == "")]
    return df.copy()


def get_candidate(cid: str, full_text=False) -> dict:
    proj = None if full_text else {"full_text": 0}
    return db().candidates.find_one({"_id": cid}, proj) or {}


def candidate_exists(cid: str) -> dict:
    return db().candidates.find_one({"_id": cid}, {"_id": 1, "prompt_version": 1, "filename": 1}) or {}


def upsert_candidate(rec: dict):
    old = db().candidates.find_one({"_id": rec["_id"]}, {"created_at": 1}) or {}
    rec["created_at"] = old.get("created_at", now())
    rec["updated_at"] = now()
    rec["project_sectors"] = sorted({p["sector"] for p in rec.get("projects", [])
                                     if p.get("sector") not in (None, "", "General", "Technology")})
    rec["work_sectors"] = sorted({w["industry"] for w in rec.get("work", [])
                                  if w.get("industry") not in (None, "", "General")})
    db().candidates.replace_one({"_id": rec["_id"]}, rec, upsert=True)
    bump_version()


def update_candidate(cid: str, fields: dict):
    db().candidates.update_one({"_id": cid}, {"$set": {**fields, "updated_at": now()}})
    bump_version()


def delete_candidate(cid: str):
    db().candidates.delete_one({"_id": cid})
    db().resume_files.delete_one({"_id": cid})
    bump_version()


def dedupe() -> int:
    """Same email (or same name+phone) => keep the richest record, flag the rest."""
    d = db()
    rows = list(d.candidates.find({}, {"email": 1, "phone": 1, "name": 1, "projects": 1, "work": 1, "updated_at": 1}))
    d.candidates.update_many({}, {"$set": {"duplicate_of": None}})
    groups = {}
    for r in rows:
        em = (r.get("email") or "").strip().lower()
        key = em or (f"{(r.get('name') or '').lower()}|{r.get('phone')}" if r.get("phone") else None)
        if key:
            groups.setdefault(key, []).append(r)
    flagged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (len(r.get("projects") or []) + len(r.get("work") or []),
                                    str(r.get("updated_at") or "")), reverse=True)
        for m in members[1:]:
            d.candidates.update_one({"_id": m["_id"]}, {"$set": {"duplicate_of": members[0]["_id"]}})
            flagged += 1
    bump_version()
    return flagged


# ------------------------------------------------------------------ resume files
def put_file(cid: str, filename: str, data: bytes):
    db().resume_files.replace_one({"_id": cid}, {"_id": cid, "filename": filename, "data": data, "size": len(data)},
                                  upsert=True)


def get_file(cid: str):
    f = db().resume_files.find_one({"_id": cid})
    if not f:
        return None, None
    return f["filename"], bytes(f["data"])


def file_status(filename, cid, status, error=""):
    db().file_status.replace_one({"_id": filename}, {"_id": filename, "cid": cid, "status": status,
                                                     "error": (error or "")[:400], "at": now()}, upsert=True)


def failed_files() -> pd.DataFrame:
    rows = list(db().file_status.find({"status": {"$nin": ["ok"]}}).sort("at", -1))
    return pd.DataFrame(rows, columns=["_id", "status", "error", "at"]).rename(columns={"_id": "file"})


# ------------------------------------------------------------------ templates
def templates() -> list:
    return list(db().templates.find().sort("order", 1))


def save_template(name, subject, body, order=None):
    upd = {"subject": subject, "body": body, "updated_at": now()}
    if order is not None:
        upd["order"] = order
    db().templates.update_one({"_id": name.strip()}, {"$set": upd, "$setOnInsert": {"order": 99}}
                              if order is None else {"$set": upd}, upsert=True)


def delete_template(name):
    db().templates.delete_one({"_id": name})


# ------------------------------------------------------------------ job openings
def openings(active_only=False) -> list:
    q = {"active": True} if active_only else {}
    return list(db().openings.find(q).sort("order", 1))


def save_opening(op: dict):
    oid = op.get("_id") or re.sub(r"[^a-z0-9]+", "-", (op["title"] + "-" + op.get("location", "")).lower()).strip("-")
    op = {**op, "_id": oid, "updated_at": now()}
    db().openings.replace_one({"_id": oid}, op, upsert=True)
    return oid


def delete_opening(oid):
    db().openings.delete_one({"_id": oid})


# ------------------------------------------------------------------ users / access
def user_role(email: str) -> str:
    """'admin' | 'hr' | '' (no access)."""
    em = (email or "").strip().lower()
    if not em:
        return ""
    if em in Config.ADMIN_EMAILS:
        return "admin"
    u = db().users.find_one({"_id": em})
    return (u or {}).get("role", "")


def users() -> list:
    return list(db().users.find().sort("_id", 1))


def save_user(email, role, name="", added_by=""):
    em = email.strip().lower()
    db().users.update_one({"_id": em}, {"$set": {"role": role, "name": name},
                                        "$setOnInsert": {"added_at": now(), "added_by": added_by}}, upsert=True)


def delete_user(email):
    em = email.strip().lower()
    if em in Config.ADMIN_EMAILS:
        raise ValueError("This admin is defined in secrets (ADMIN_EMAILS) and can't be removed here.")
    db().users.delete_one({"_id": em})


# ------------------------------------------------------------------ email log
def log_email(candidate_id, to_email, name, subject, body, template, opening, sent_by, status, error=""):
    db().email_log.insert_one({
        "candidate_id": candidate_id, "to": to_email, "name": name, "subject": subject, "body": body,
        "template": template, "opening": opening, "sent_by": sent_by, "status": status,
        "error": (error or "")[:400], "at": now(),
    })


def last_contacted() -> dict:
    out = {}
    for r in db().email_log.find({"status": "sent"}, {"candidate_id": 1, "at": 1}):
        cid, at = r["candidate_id"], r["at"]
        if cid not in out or at > out[cid]:
            out[cid] = at
    return out


def recently_contacted(cid: str, days: int):
    since = now() - timedelta(days=days)
    return db().email_log.find_one({"candidate_id": cid, "status": "sent", "at": {"$gte": since}})


def email_log_df() -> pd.DataFrame:
    rows = list(db().email_log.find({}, {"body": 0}).sort("at", -1).limit(2000))
    cols = ["at", "name", "to", "subject", "template", "opening", "sent_by", "status", "error"]
    return pd.DataFrame(rows, columns=["_id"] + cols)[cols]


# ------------------------------------------------------------------ usage / cost
def log_usage(user, kind, model, tokens, cost):
    db().usage.insert_one({"user": user, "kind": kind, "model": model, "tokens": int(tokens or 0),
                           "cost": float(cost or 0), "at": now()})


def cost_today() -> float:
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(r.get("cost", 0) for r in db().usage.find({"at": {"$gte": start}}, {"cost": 1}))


def usage_df(days=30) -> pd.DataFrame:
    since = now() - timedelta(days=days)
    rows = list(db().usage.find({"at": {"$gte": since}}))
    return pd.DataFrame(rows, columns=["user", "kind", "model", "tokens", "cost", "at"])


def log_question(user, question, intent, used_llm):
    db().questions.insert_one({"user": user, "q": question[:500], "intent": intent, "llm": bool(used_llm), "at": now()})


def questions_df(days=30) -> pd.DataFrame:
    since = now() - timedelta(days=days)
    rows = list(db().questions.find({"at": {"$gte": since}}).sort("at", -1))
    return pd.DataFrame(rows, columns=["at", "user", "q", "intent", "llm"])
