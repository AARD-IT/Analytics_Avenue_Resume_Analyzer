"""
Router: understands the HR message.

1. Free rule parser (regex + vocab) — handles most messages with 0 tokens.
2. Only if rules can't tell what's wanted -> ONE small LLM call (gpt-4.1-nano)
   that returns a JSON intent + filters. It never sees resumes or writes SQL.
Results of LLM routing are cached per normalised message.
"""
import difflib
import re
from functools import lru_cache

from core.taxonomy import CATEGORIES, SECTORS, _CATEGORY_SYNONYMS, _SECTOR_SYNONYMS

INTENTS = ["greeting", "help", "stats", "count", "search", "refine", "profile", "question", "compare",
           "match_jd", "show_resume", "email", "off_topic"]

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
        "eighth": 8, "ninth": 9, "tenth": 10, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
        "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10, "last": -1}
CITIES = ["chennai", "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "delhi", "new delhi", "noida",
          "gurgaon", "gurugram", "kolkata", "coimbatore", "kochi", "cochin", "madurai", "trichy",
          "tiruchirappalli", "salem", "vellore", "ahmedabad", "jaipur", "indore", "nagpur", "mysore",
          "mysuru", "visakhapatnam", "vizag", "vijayawada", "thiruvananthapuram", "trivandrum", "remote"]
KNOWN_SKILLS = [
    "python", "sql", "power bi", "tableau", "excel", "pyspark", "spark", "airflow", "aws", "azure", "gcp",
    "databricks", "snowflake", "tensorflow", "pytorch", "langchain", "langgraph", "llm", "nlp", "rag",
    "computer vision", "opencv", "docker", "kubernetes", "react", "java", "sas", "dax", "salesforce", "crm",
    "kafka", "hadoop", "mongodb", "fastapi", "flask", "django", "machine learning", "deep learning",
    "statistics", "etl", "looker", "alteryx", "ssis", "ssrs", "bigquery", "redshift", "dbt", "scikit-learn",
    "pandas", "numpy", "generative ai", "genai", "mlops", "git", "linux", "javascript", "node", "r",
]


def _empty_filters():
    return {"categories": [], "sectors": [], "min_exp": None, "max_exp": None, "skills": [], "location": ""}


def _norm(q: str) -> str:
    low = " " + q.lower().replace("–", "-") + " "
    for w, n in _WORDNUM.items():
        if w in ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"):
            continue
        low = re.sub(rf"\b{w}\b", str(n), low)
    return low


def parse_filters(q: str) -> dict:
    f = _empty_filters()
    low = _norm(q)
    low_sec = re.sub(r"power ?(bi|query|apps|automate)", " ", low)
    for c in CATEGORIES:
        if c != "Other" and c.lower() in low:
            f["categories"].append(c)
    for key, c in _CATEGORY_SYNONYMS.items():
        if key in ("sales", "developer", "genai"):
            continue
        if re.search(rf"\b{re.escape(key)}s?\b", low) and c not in f["categories"]:
            f["categories"].append(c)
    if re.search(r"\b(software|web|full ?stack|backend|frontend) (developer|engineer)s?\b", low) and "Software Engineering" not in f["categories"]:
        f["categories"].append("Software Engineering")
    if re.search(r"\b(bdes?|business development|sales executives?)\b", low) and "Business Development Executive" not in f["categories"]:
        f["categories"].append("Business Development Executive")
    # "data analyst" should not also pull "Business Analyst" etc. — keep as is (explicit words only)
    for s in SECTORS:
        if s not in ("General", "Technology") and s.lower() in low_sec and s not in f["sectors"]:
            f["sectors"].append(s)
    for key, s in _SECTOR_SYNONYMS.items():
        if s in ("Technology",) or s in f["sectors"]:
            continue
        if re.search(rf"\b{re.escape(key)}", low_sec):
            # avoid "sales" style collisions: only sector words, not category words
            f["sectors"].append(s)

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:to|-|and)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|yoe)", low)
    if m:
        f["min_exp"], f["max_exp"] = float(m.group(1)), float(m.group(2))
    else:
        m = (re.search(r"(?:more than|over|above|at ?least|minimum|min\.?|>=?)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yoe)", low)
             or re.search(r"(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?|yoe)?", low))
        if m:
            f["min_exp"] = float(m.group(1))
        m2 = re.search(r"(?:less than|under|below|upto|up to|max(?:imum)?|within|<=?)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yoe)", low)
        if m2:
            f["max_exp"] = float(m2.group(1))
        if not m and not m2:
            m3 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yoe)\b", low)
            if m3:
                y = float(m3.group(1))
                f["min_exp"], f["max_exp"] = y, y + 0.99
    if re.search(r"\bfreshers?\b|\bentry[ -]level\b|\bno experience\b|\bgraduates?\b", low):
        f["min_exp"], f["max_exp"] = 0.0, 0.99
    if re.search(r"\b(senior|experienced|seniors)\b", low) and f["min_exp"] is None:
        f["min_exp"] = 5.0

    for sk in KNOWN_SKILLS:
        if len(sk) == 1:
            if re.search(r"(?<![\w.])r(?![\w.])\s*(language|programming)|\bin r\b|\bknows? r\b", low):
                f["skills"].append("r")
            continue
        if re.search(rf"(?<![a-z]){re.escape(sk)}(?![a-z])", low):
            if sk == "spark" and "pyspark" in f["skills"]:
                continue
            f["skills"].append(sk)
    for city in CITIES:
        if re.search(rf"\b{city}\b", low):
            f["location"] = city
            break
    return f


def has_filters(f: dict) -> bool:
    return bool(f["categories"] or f["sectors"] or f["skills"] or f["location"]
                or f["min_exp"] is not None or f["max_exp"] is not None)


def parse_refs(q: str, last_n: int) -> list:
    """'#2', 'no 2', 'number 2', '2nd', 'second one', 'top 3', 'first 5', 'them/all' -> 1-based positions."""
    low = q.lower()
    refs = []
    m = re.search(r"\b(?:top|first)\s+(\d+)\b", _norm(q))
    if m:
        return list(range(1, min(int(m.group(1)), last_n) + 1))
    for m in re.finditer(r"(?:#|\bno\.?\s*|\bnumber\s+|\bcandidate\s+)(\d{1,2})\b", low):
        refs.append(int(m.group(1)))
    for w, n in _ORD.items():
        if re.search(rf"\b{w}\b(?!\s+(?:\d|time))", low) and not re.search(rf"\b{w}\s+\d", low):
            refs.append(last_n if n == -1 else n)
    m = re.search(r"\b(\d{1,2})\s*(?:,|and|&)\s*(\d{1,2})\b", low)
    if m and re.search(r"compare|vs|#|and", low) and not re.search(r"years?|yrs", low):
        refs += [int(m.group(1)), int(m.group(2))]
    if not refs and re.search(r"\b(all of them|them all|all of these|these candidates|those candidates|"
                              r"these people|them|they|those|these|everyone|all)\b", low):
        refs = list(range(1, last_n + 1))
    seen, out = set(), []
    for r in refs:
        if 1 <= r <= last_n and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def match_names(q: str, names: dict) -> list:
    """names: {id: name}. Returns ids whose name is mentioned (fuzzy on tokens >= 4 chars)."""
    low = re.sub(r"[^a-z ]", " ", q.lower())
    words = [w for w in low.split() if len(w) >= 4]
    stop = {"show", "resume", "about", "tell", "profile", "details", "compare", "email", "send", "with", "their",
            "candidate", "candidates", "data", "analyst", "engineer", "years", "more", "what", "which", "does",
            "have", "experience", "projects", "project", "skills", "open", "view", "give", "please", "interview",
            "invite", "mail", "this", "that", "from", "role", "team", "business", "development", "executive",
            "science", "scientist", "engineering", "analytics", "healthcare", "manufacturing", "retail", "banking",
            "chennai", "bangalore", "fresher", "freshers", "senior", "python", "power", "tableau", "excel", "only",
            "also", "them", "they", "those", "these", "between", "versus", "know", "knows", "worked", "work"}
    words = [w for w in words if w not in stop]
    if not words:
        return []
    hits = []
    for cid, name in names.items():
        toks = [t for t in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(t) >= 3]
        score = 0
        for w in words:
            if w in toks or difflib.get_close_matches(w, toks, n=1, cutoff=0.88):
                score += 1
        if score:
            hits.append((score, cid))
    if not hits:
        return []
    best = max(s for s, _ in hits)
    return [cid for s, cid in hits if s == best][:5]


def rule_route(q: str, has_last: bool) -> tuple:
    """Return (intent, confident: bool)."""
    low = q.lower().strip()
    if re.fullmatch(r"(hi|hello|hey|hii+|good (morning|afternoon|evening)|thanks?|thank you|ok|okay|cool|great)[!. ]*", low):
        return "greeting", True
    if re.search(r"\b(help|what can you do|how to use|commands)\b", low) and len(low) < 60:
        return "help", True
    if low.startswith(("jd:", "job description:")) or (len(q) > 450 and re.search(r"responsibilit|requirement|qualification|we are looking", low)):
        return "match_jd", True
    if re.search(r"\b(match|best fit|suitable|shortlist)\b.*\b(jd|job description|opening|role)\b", low):
        return "match_jd", True
    if re.search(r"\b(send|shoot|draft|write)\b.*\b(e?-?mails?|invites?|invitations?)\b|\b(e?-?mail|invite|reach out to|contact)\b\s+"
                 r"(them|these|those|all|top|the|#|\d|him|her|candidate)", low) or re.match(r"^(e?-?mail|invite)\b", low):
        return "email", True
    if re.search(r"\b(resume|cv|pdf)\b", low) and re.search(r"\b(show|open|view|see|send me|give|get|download|display)\b", low):
        return "show_resume", True
    if re.search(r"\b(compare|comparison|versus|vs\.?|difference between|better between)\b", low):
        return "compare", True
    if re.search(r"\b(overview|statistics|stats|breakdown|dashboard|summary of (all|the) (candidates|database|resumes))\b", low):
        return "stats", True
    if re.search(r"\b(tell me (more )?about|more about|profile of|details? (of|about|for)|who is|summar(y|ise|ize) (of )?#?)\b", low):
        return "profile", True
    if re.search(r"\b(does|do|did|has|have|can|is|was|why|which of)\b", low) and re.search(r"#\d|\b(he|she|him|her|this candidate|them|they)\b", low):
        return "question", True
    if re.search(r"\b(how many|count|number of|total)\b", low):
        return "count", True
    if has_last and re.search(r"\b(among (them|these)|from (them|these|those|the list)|of (them|these|those)|"
                              r"only (those|the ones)|filter (them|these)|out of these|in them)\b", low):
        return "refine", True
    if re.search(r"\b(find|show|list|search|get|give|need|want|looking for|who (has|have|knows?|worked)|candidates?|profiles?|people)\b", low):
        return "search", True
    return "", False


_FILLER = set("""a an the i we you me my our us is are am be with for of in on at to from and or by who which that
this these those them they their any some all only also need needs want wants looking look find show list search get give
please can could would should candidate candidates profile profiles people person someone somebody resume resumes cv
year years yr yrs experience exp role roles job jobs position opening good best strong top more than less over under
above below between least most minimum maximum plus have has having had worked working work knows know know how many
count number total do does did tell me about details hire hiring fresher freshers senior junior team using used good"""
              .split())


def unknown_words(q: str, f: dict) -> list:
    """Meaningful words the rule parser did NOT understand (signals: ask the LLM)."""
    low = re.sub(r"power ?bi", "powerbi", q.lower())
    known = set(_FILLER)
    for v in f.get("categories", []) + f.get("sectors", []) + f.get("skills", []) + [f.get("location") or ""]:
        known |= set(re.findall(r"[a-z]+", v.lower()))
    known |= {"powerbi", "analyst", "analysts", "analytics", "engineer", "engineers", "scientist", "scientists",
              "bde", "bdes", "developer", "developers", "data", "projects", "project", "sector", "industry", "domain"}
    for k in list(_CATEGORY_SYNONYMS) + list(_SECTOR_SYNONYMS) + CITIES:
        known |= set(re.findall(r"[a-z]+", k))
    words = re.findall(r"[a-z]{3,}", low)
    return [w for w in words if w not in known and not any(w.startswith(k) for k in ("manufactur", "health", "financ", "bank", "logist", "insur", "retail"))]


LLM_ROUTER_SYSTEM = (
    "You route messages for an HR resume-search assistant. Reply with JSON only:\n"
    '{"intent": one of ' + str(INTENTS) + ",\n"
    '"filters": {"categories": subset of ' + str([c for c in CATEGORIES if c != "Other"]) + ", "
    '"sectors": subset of ' + str([s for s in SECTORS if s != "General"]) + ", "
    '"min_exp": number|null, "max_exp": number|null, "skills": [lowercase strings], "location": city or ""},\n'
    '"refs": [1-based numbers of candidates from the previous result list the user refers to],\n'
    '"names": [candidate names mentioned], "role_title": string or "", "limit": number|null}\n'
    "Use 'off_topic' for anything unrelated to candidates, resumes, hiring or emailing candidates. "
    "Never filter by age, gender, religion, caste, marital status or nationality."
)


@lru_cache(maxsize=512)
def _llm_route_cached(q_norm: str, last_names: str, user: str):
    from core.llm import LLM
    llm = LLM("chat", user=user)
    ctx = f"Previous result list: {last_names}\n" if last_names else ""
    data = llm.json_chat(LLM_ROUTER_SYSTEM, f"{ctx}Message: {q_norm}", max_tokens=250)
    return data, llm.cost_usd


def llm_route(q: str, last_names: list, user: str) -> dict:
    names = "; ".join(f"#{i + 1} {n}" for i, n in enumerate(last_names[:10]))
    hits_before = _llm_route_cached.cache_info().hits
    data, cost = _llm_route_cached(q.strip().lower(), names, user)
    if _llm_route_cached.cache_info().hits > hits_before:
        cost = 0.0  # served from cache
    data = dict(data or {})
    f = _empty_filters()
    fl = data.get("filters") or {}
    f["categories"] = [c for c in fl.get("categories") or [] if c in CATEGORIES]
    f["sectors"] = [s for s in fl.get("sectors") or [] if s in SECTORS]
    f["skills"] = [str(s).lower()[:40] for s in fl.get("skills") or []][:6]
    f["location"] = str(fl.get("location") or "").lower()[:40]
    for k in ("min_exp", "max_exp"):
        try:
            f[k] = float(fl.get(k)) if fl.get(k) is not None else None
        except (TypeError, ValueError):
            f[k] = None
    intent = data.get("intent") if data.get("intent") in INTENTS else "search"
    refs = [int(r) for r in data.get("refs") or [] if str(r).isdigit()]
    return {"intent": intent, "filters": f, "refs": refs, "names": data.get("names") or [],
            "role_title": str(data.get("role_title") or ""), "limit": data.get("limit"), "cost": cost}
