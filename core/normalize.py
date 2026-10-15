"""
Pure-Python clean-up (no LLM): dates, experience, phone, email, names.
"""
import re
from datetime import date

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
PRESENT_WORDS = ("present", "current", "now", "till date", "to date", "ongoing", "today", "till now")
INTERN_PAT = re.compile(r"\b(intern|internship|apprentice|trainee intern|summer trainee)\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{8,16}\d)")


def parse_month(value, is_end=False):
    """Return (year, month) or None. Handles YYYY-MM, YYYY, MM/YYYY, 'Jan 2022', 'Present'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("unknown", "n/a", "na", "none", "-"):
        return None
    if any(w in s for w in PRESENT_WORDS):
        t = date.today()
        return (t.year, t.month)
    m = re.search(r"(\d{4})[-/.](\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{1,2})[-/.](\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return (int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b([a-z]{3,9})[\s.,'\-/]*(\d{4}|\d{2})\b", s)
    if m and m.group(1)[:3] in MONTHS:
        mon = MONTHS[m.group(1)[:3]]
        yr = int(m.group(2))
        if yr < 100:
            yr += 2000
        return (yr, mon)
    m = re.search(r"\b(19[7-9]\d|20[0-4]\d)\b", s)
    if m:
        return (int(m.group(1)), 12 if is_end else 1)
    return None


def _to_index(ym):
    return ym[0] * 12 + (ym[1] - 1)


def is_internship(role: str, flag=None) -> bool:
    if isinstance(flag, bool):
        return flag or bool(INTERN_PAT.search(role or ""))
    if isinstance(flag, str) and flag.lower() in ("true", "yes", "1"):
        return True
    return bool(INTERN_PAT.search(role or ""))


def job_months(start, end) -> int:
    s = parse_month(start)
    e = parse_month(end, is_end=True)
    if not s:
        return 0
    if not e:
        return 0
    today = date.today()
    cap = today.year * 12 + today.month - 1
    si, ei = _to_index(s), min(_to_index(e), cap)
    if ei < si:
        return 0
    return ei - si + 1


def merged_years(jobs: list) -> float:
    """
    jobs: list of (start, end). Overlapping periods are merged so parallel
    jobs are not double-counted. Returns years (1 decimal).
    """
    today = date.today()
    cap = today.year * 12 + today.month - 1
    intervals = []
    for start, end in jobs:
        s = parse_month(start)
        e = parse_month(end, is_end=True)
        if not s or not e:
            continue
        si, ei = _to_index(s), min(_to_index(e), cap)
        if si > cap or ei < si:
            continue
        if (ei - si) > 12 * 45:
            continue
        intervals.append((si, ei))
    if not intervals:
        return 0.0
    intervals.sort()
    total = 0
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
    total += cur_e - cur_s + 1
    return round(total / 12.0, 1)


def clean_email(value, fallback_text: str = "") -> str:
    for src in (value, fallback_text):
        if not src:
            continue
        m = EMAIL_RE.search(str(src))
        if m:
            return m.group(0).strip(".").lower()
    return ""


def clean_phone(value, fallback_text: str = "") -> str:
    for src in (value, fallback_text[:3000] if fallback_text else ""):
        if not src:
            continue
        for m in PHONE_RE.finditer(str(src)):
            raw = m.group(0)
            digits = re.sub(r"\D", "", raw)
            if len(digits) == 10 and digits[0] in "6789":
                return f"+91 {digits[:5]} {digits[5:]}"
            if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
                return f"+91 {digits[2:7]} {digits[7:]}"
            if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
                return f"+91 {digits[1:6]} {digits[6:]}"
            if 10 <= len(digits) <= 13 and raw.strip().startswith("+"):
                return "+" + digits
    return ""


def name_from_filename(filename: str) -> str:
    """Google Form uploads are named '<original> - <Respondent Name>.pdf'."""
    stem = re.sub(r"\.(pdf|docx?|txt)$", "", filename or "", flags=re.I)
    if " - " in stem:
        cand = stem.split(" - ")[-1].strip()
        if re.search(r"[A-Za-z]{2,}", cand) and "#U" not in cand:
            return cand
    return ""


def clean_name(value, filename: str = "") -> str:
    name = (value or "").strip()
    name = re.sub(r"\s+", " ", name)
    if not name or len(name) > 60 or "@" in name or name.lower() in ("string", "name", "unknown"):
        name = name_from_filename(filename)
    if name.isupper() or name.islower():
        name = name.title()
    return name or "Unknown"


_SPACED_LINE = re.compile(r"^(?:\S ){2,}\S\s*$", re.M)


def clean_text(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    # "S K I L L S" -> "SKILLS"
    text = _SPACED_LINE.sub(lambda m: m.group(0).replace(" ", ""), text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def split_skills(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;|\n]", value)
    else:
        items = []
        for v in value:
            items.extend(re.split(r"[,;|]", str(v)))
    out, seen = [], set()
    for it in items:
        it = it.strip(" .-•*\t")
        if 1 < len(it) <= 40 and it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out[:40]
