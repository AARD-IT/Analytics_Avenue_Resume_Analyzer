"""
Agent tools. Deterministic (0 tokens) unless stated.
"""
import re

import pandas as pd

from core import store
from core.taxonomy import CATEGORIES, EXPERIENCE_BANDS, classify_category, experience_band, keyword_sector

BAND_LABELS = [b[0] for b in EXPERIENCE_BANDS]


# ------------------------------------------------------------------ search
def _skill_hit(row, skill: str) -> bool:
    s = skill.lower()
    if any(s in str(x).lower() for x in row["skills"]):
        return True
    return any(s in str(p.get("tech_stack", "")).lower() for p in row["projects"])


def search(filters: dict, base_ids=None, include_work_sector=True) -> pd.DataFrame:
    df = store.candidates_df()
    if base_ids is not None:
        df = df[df["id"].isin(base_ids)]
    if filters.get("categories"):
        df = df[df["category"].isin(filters["categories"])]
    lo, hi = filters.get("min_exp"), filters.get("max_exp")
    if lo is not None:
        df = df[df["total_experience"] >= float(lo)]
    if hi is not None:
        df = df[df["total_experience"] <= float(hi)]
    if filters.get("sectors"):
        want = set(filters["sectors"])

        def has_sector(r):
            if want & set(r["project_sectors"]):
                return True
            return include_work_sector and bool(want & set(r["work_sectors"]))
        df = df[df.apply(has_sector, axis=1)] if len(df) else df
    for sk in filters.get("skills") or []:
        if len(df):
            df = df[df.apply(lambda r: _skill_hit(r, sk), axis=1)]
    loc = (filters.get("location") or "").lower()
    if loc and len(df):
        alias = {"bengaluru": "bangalore", "bangalore": "bengaluru", "gurugram": "gurgaon", "gurgaon": "gurugram",
                 "cochin": "kochi", "kochi": "cochin", "trivandrum": "thiruvananthapuram"}
        keys = {loc, alias.get(loc, loc)}
        df = df[df["location"].str.lower().apply(lambda x: any(k in x for k in keys))]
    if len(df):
        df = df.assign(_np=df["projects"].apply(len)).sort_values(["total_experience", "_np"], ascending=False)
        df = df.drop(columns="_np")
    return df


def describe_filters(f: dict) -> str:
    parts = []
    if f.get("categories"):
        parts.append(" / ".join(f["categories"]))
    lo, hi = f.get("min_exp"), f.get("max_exp")
    if lo is not None and hi is not None:
        parts.append("freshers (<1 yr)" if lo == 0 and hi < 1 else f"{lo:g}–{hi:g} yrs")
    elif lo is not None:
        parts.append(f"{lo:g}+ yrs")
    elif hi is not None:
        parts.append(f"≤ {hi:g} yrs")
    if f.get("sectors"):
        parts.append(" / ".join(f["sectors"]) + " sector")
    if f.get("skills"):
        parts.append("skills: " + ", ".join(f["skills"]))
    if f.get("location"):
        parts.append(f["location"].title())
    return " · ".join(parts) or "all candidates"


def relevant_projects(row, sectors=None) -> list:
    projs = [p for p in row["projects"] if not str(p.get("name", "")).startswith("(auto")]
    if sectors:
        hit = [p for p in projs if p.get("sector") in sectors]
        if hit:
            return hit
    return [p for p in projs if p.get("sector") not in ("General", "Technology")] or projs


# ------------------------------------------------------------------ stats / counts
def stats_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    band = df["total_experience"].apply(experience_band)
    pv = pd.crosstab(df["category"], band, margins=True, margins_name="Total")
    pv = pv.reindex(columns=[b for b in BAND_LABELS if b in pv.columns] + ["Total"], fill_value=0)
    order = [c for c in CATEGORIES if c in pv.index] + ["Total"]
    return pv.reindex(order).reset_index().rename(columns={"category": "Category", "row_0": "Category"})


def sector_table(df: pd.DataFrame) -> pd.DataFrame:
    counts = {}
    for secs in df["project_sectors"]:
        for s in secs:
            counts[s] = counts.get(s, 0) + 1
    return (pd.DataFrame(sorted(counts.items(), key=lambda x: -x[1]), columns=["Sector", "Candidates"])
            if counts else pd.DataFrame())


# ------------------------------------------------------------------ profiles (compact = cheap context)
def compact_profile(c: dict, idx=None) -> str:
    """~150-250 tokens. This is ALL the LLM ever sees about a candidate."""
    projs = [p for p in c.get("projects", []) if not str(p.get("name", "")).startswith("(auto")][:6]
    work = c.get("work", [])[:5]
    lines = [
        f"{'#' + str(idx) + ' ' if idx else ''}{c.get('name')} | {c.get('category')} | "
        f"{c.get('total_experience', 0):g} yrs exp (+{c.get('internship_experience', 0):g} internship)"
        f"{' | ' + c['location'] if c.get('location') else ''}",
        f"Current: {c.get('current_role', '')} @ {c.get('current_company', '')}",
        "Skills: " + ", ".join(list(c.get("skills", []))[:18]),
    ]
    if work:
        lines.append("Work: " + "; ".join(
            f"{w.get('role')} @ {w.get('company')} ({w.get('start')}–{w.get('end')}{', intern' if w.get('is_internship') else ''})"
            for w in work))
    if projs:
        lines.append("Projects: " + "; ".join(
            f"{p.get('name')} [{p.get('sector')}] ({p.get('tech_stack', '')[:60]})" for p in projs))
    if c.get("education"):
        lines.append("Education: " + "; ".join(f"{e.get('degree')} {e.get('institution')} {e.get('year')}" for e in c["education"]))
    if c.get("summary"):
        lines.append("Summary: " + c["summary"])
    return "\n".join(lines)


# ------------------------------------------------------------------ JD match (0 tokens + optional 1 nano call)
def jd_requirements_rules(jd: str) -> dict:
    from agent.router import KNOWN_SKILLS, parse_filters
    f = parse_filters(jd)
    low = jd.lower()
    vocab = set(KNOWN_SKILLS)
    for skills in store.candidates_df()["skills"]:
        for s in skills:
            s2 = str(s).lower().strip()
            if 2 < len(s2) <= 25:
                vocab.add(s2)
    found = [s for s in vocab if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", low)]
    found = sorted(found, key=lambda s: -len(s))[:25]
    cat = f["categories"][0] if f["categories"] else classify_category([jd[:200]], found, jd)
    sec = f["sectors"] or ([keyword_sector(jd)] if keyword_sector(jd) != "General" else [])
    return {"category": cat, "skills": found, "min_exp": f["min_exp"] or 0.0, "sectors": sec}


def jd_requirements_llm(jd: str, user: str) -> dict:
    from core.llm import LLM
    sys = ("Extract hiring requirements from a job description. JSON only: "
           f'{{"category": one of {CATEGORIES}, "skills": [max 15 lowercase core skills], '
           '"min_exp": number, "sectors": [industry sectors if stated]}. '
           "Ignore any personal-attribute requirements (age, gender, etc.).")
    llm = LLM("chat", user=user)
    data = llm.json_chat(sys, jd[:6000], max_tokens=300)
    return {"category": data.get("category") if data.get("category") in CATEGORIES else "Other",
            "skills": [str(s).lower() for s in data.get("skills") or []][:15],
            "min_exp": float(data.get("min_exp") or 0), "sectors": list(data.get("sectors") or [])[:3]}, llm.cost_usd


def jd_match(req: dict, top_n=10) -> pd.DataFrame:
    df = store.candidates_df()
    if df.empty:
        return df
    want = [s.lower() for s in req.get("skills", [])]

    def score(r):
        cand_sk = " | ".join(str(s).lower() for s in r["skills"]) + " | " + " | ".join(
            str(p.get("tech_stack", "")).lower() for p in r["projects"])
        matched = [s for s in want if s in cand_sk]
        s_skill = (len(matched) / len(want)) if want else 0.5
        s_cat = 1.0 if r["category"] == req.get("category") else 0.0
        need = float(req.get("min_exp") or 0)
        exp = float(r["total_experience"])
        s_exp = 1.0 if exp >= need else (exp / need if need else 1.0)
        s_sec = 1.0 if set(req.get("sectors") or []) & (set(r["project_sectors"]) | set(r["work_sectors"])) else 0.0
        total = 55 * s_skill + 20 * s_cat + 15 * s_exp + 10 * s_sec
        return pd.Series({"match": round(total), "matched_skills": matched})

    sc = df.apply(score, axis=1)
    df = pd.concat([df, sc], axis=1).sort_values(["match", "total_experience"], ascending=False)
    return df.head(top_n)
