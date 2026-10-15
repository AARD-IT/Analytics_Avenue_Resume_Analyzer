"""
Fixed vocabularies + zero-cost (no LLM) keyword classifiers.

The LLM is forced to pick from these lists, and everything it returns is
normalised back onto them, so filters always work on clean values.
"""
import re
from collections import Counter

# ======================= JOB CATEGORIES =======================
CATEGORIES = [
    "Data Analytics",
    "Data Science",
    "Data Engineering",
    "AI / ML Engineering",
    "Business Analyst",
    "Business Development Executive",
    "Software Engineering",
    "Other",
]

CATEGORY_HINTS = {
    "Data Analytics": "BI, dashboards, SQL, Excel, Power BI/Tableau, reporting, MIS",
    "Data Science": "statistics, ML modelling, predictive analytics, experimentation",
    "Data Engineering": "ETL/ELT, pipelines, Spark, Airflow, Kafka, data warehouses, cloud data platforms",
    "AI / ML Engineering": "deep learning, NLP, computer vision, GenAI/LLM/RAG, MLOps, model deployment",
    "Business Analyst": "requirements, BRD/FRD, stakeholders, process improvement, UAT",
    "Business Development Executive": "sales, lead generation, client acquisition, partnerships, CRM, revenue targets",
    "Software Engineering": "web/mobile/backend development, APIs, full-stack",
    "Other": "none of the above",
}

_CATEGORY_SYNONYMS = {
    "data analyst": "Data Analytics",
    "data analytics": "Data Analytics",
    "business intelligence": "Data Analytics",
    "bi developer": "Data Analytics",
    "data scientist": "Data Science",
    "data science": "Data Science",
    "data engineer": "Data Engineering",
    "data engineering": "Data Engineering",
    "ml engineer": "AI / ML Engineering",
    "machine learning engineer": "AI / ML Engineering",
    "ai engineer": "AI / ML Engineering",
    "ai/ml": "AI / ML Engineering",
    "ai / ml": "AI / ML Engineering",
    "genai": "AI / ML Engineering",
    "business analyst": "Business Analyst",
    "business development": "Business Development Executive",
    "bde": "Business Development Executive",
    "sales": "Business Development Executive",
    "software engineer": "Software Engineering",
    "software developer": "Software Engineering",
    "developer": "Software Engineering",
}

# (pattern, weight) — titles are weighted x3 by the caller
_CATEGORY_KEYWORDS = {
    "Data Analytics": [
        r"data analyst", r"business intelligence", r"\bbi\b", r"power ?bi", r"tableau",
        r"\bexcel\b", r"dashboard", r"reporting", r"\bmis\b", r"looker", r"data analytics",
    ],
    "Data Science": [
        r"data scientist", r"data science", r"machine learning", r"statistic",
        r"predictive", r"scikit", r"regression", r"classification", r"xgboost", r"hypothesis",
    ],
    "Data Engineering": [
        r"data engineer", r"\betl\b", r"\belt\b", r"pyspark", r"\bspark\b", r"airflow", r"kafka",
        r"databricks", r"snowflake", r"hadoop", r"data pipeline", r"data factory", r"\badf\b",
        r"\bdbt\b", r"redshift", r"bigquery", r"data warehouse", r"\bhive\b",
    ],
    "AI / ML Engineering": [
        r"ml engineer", r"ai engineer", r"machine learning engineer", r"deep learning", r"\bnlp\b",
        r"computer vision", r"\bllm", r"gen ?ai", r"generative ai", r"langchain", r"\brag\b",
        r"tensorflow", r"pytorch", r"mlops", r"transformer", r"\bcnn\b", r"opencv", r"yolo",
    ],
    "Business Analyst": [
        r"business analyst", r"requirement", r"\bbrd\b", r"\bfrd\b", r"stakeholder",
        r"\bjira\b", r"process improvement", r"\buat\b", r"user stor",
    ],
    "Business Development Executive": [
        r"business development", r"\bbde\b", r"\bsales\b", r"lead generation", r"client acquisition",
        r"cold call", r"\bcrm\b", r"partnership", r"revenue target", r"account manager", r"inside sales",
    ],
    "Software Engineering": [
        r"software engineer", r"software developer", r"full ?stack", r"front ?end", r"back ?end",
        r"\breact\b", r"\bangular\b", r"spring boot", r"node\.?js", r"\bdjango\b", r"\.net\b",
        r"web developer", r"android", r"\bjava\b",
    ],
}


def normalize_category(raw) -> str:
    if not raw:
        return "Other"
    s = str(raw).strip()
    for c in CATEGORIES:
        if s.lower() == c.lower():
            return c
    low = s.lower()
    for key, cat in _CATEGORY_SYNONYMS.items():
        if key in low:
            return cat
    return "Other"


def classify_category(titles: list, skills: list, text: str = "") -> str:
    """Keyword scoring. Job titles count 3x, skills 1x, free text 0.5x."""
    scores = Counter()
    title_blob = " | ".join(t for t in titles if t).lower()
    skill_blob = " | ".join(s for s in skills if s).lower()
    text_blob = (text or "")[:4000].lower()
    for cat, pats in _CATEGORY_KEYWORDS.items():
        for p in pats:
            if re.search(p, title_blob):
                scores[cat] += 3
            if re.search(p, skill_blob):
                scores[cat] += 1
            if text_blob and re.search(p, text_blob):
                scores[cat] += 0.5
    if not scores:
        return "Other"
    return scores.most_common(1)[0][0]


# ======================= INDUSTRY SECTORS =======================
SECTORS = [
    "Manufacturing",
    "Healthcare",
    "Retail & E-commerce",
    "Banking & Finance",
    "Insurance",
    "Supply Chain & Logistics",
    "Automobile",
    "Telecom",
    "Education",
    "Energy & Utilities",
    "Agriculture",
    "Marketing & Media",
    "Real Estate",
    "Travel & Hospitality",
    "Government & Public Sector",
    "Technology",
    "General",
]

_SECTOR_SYNONYMS = {
    "manufactur": "Manufacturing",
    "industrial": "Manufacturing",
    "health": "Healthcare",
    "pharma": "Healthcare",
    "medical": "Healthcare",
    "hospital": "Healthcare",
    "life science": "Healthcare",
    "retail": "Retail & E-commerce",
    "commerce": "Retail & E-commerce",
    "consumer goods": "Retail & E-commerce",
    "fmcg": "Retail & E-commerce",
    "cpg": "Retail & E-commerce",
    "bank": "Banking & Finance",
    "financ": "Banking & Finance",
    "fintech": "Banking & Finance",
    "bfsi": "Banking & Finance",
    "capital market": "Banking & Finance",
    "insur": "Insurance",
    "supply": "Supply Chain & Logistics",
    "logistic": "Supply Chain & Logistics",
    "transport": "Supply Chain & Logistics",
    "shipping": "Supply Chain & Logistics",
    "automo": "Automobile",
    "automobile": "Automobile",
    "telecom": "Telecom",
    "telco": "Telecom",
    "education": "Education",
    "edtech": "Education",
    "energy": "Energy & Utilities",
    "utilit": "Energy & Utilities",
    "oil": "Energy & Utilities",
    "mining": "Energy & Utilities",
    "power": "Energy & Utilities",
    "agri": "Agriculture",
    "marketing": "Marketing & Media",
    "media": "Marketing & Media",
    "entertainment": "Marketing & Media",
    "advertis": "Marketing & Media",
    "real estate": "Real Estate",
    "property": "Real Estate",
    "travel": "Travel & Hospitality",
    "hospitality": "Travel & Hospitality",
    "aviation": "Travel & Hospitality",
    "food": "Travel & Hospitality",
    "government": "Government & Public Sector",
    "public sector": "Government & Public Sector",
    "technolog": "Technology",
    "software": "Technology",
    "it services": "Technology",
    "saas": "Technology",
}

_SECTOR_KEYWORDS = {
    "Manufacturing": [
        r"manufactur", r"factory", r"production line", r"assembly line", r"predictive maintenance",
        r"machine failure", r"defect", r"quality control", r"\boee\b", r"\bcnc\b", r"steel",
        r"semiconductor", r"industrial", r"equipment failure", r"\bplant\b(?! disease| leaf)",
    ],
    "Healthcare": [
        r"health", r"hospital", r"patient", r"clinical", r"medical", r"disease", r"diabet",
        r"cancer", r"tumou?r", r"heart", r"cardio", r"pharma", r"\bdrug", r"diagnos", r"covid",
        r"x-?ray", r"\bmri\b", r"parkinson", r"alzheimer", r"pneumonia", r"stroke", r"kidney",
        r"liver", r"blood", r"\behr\b", r"doctor", r"skin lesion", r"brain",
    ],
    "Retail & E-commerce": [
        r"retail", r"e-?commerce", r"superstore", r"\bstore\b", r"shopping", r"basket",
        r"recommendation system", r"product recommend", r"customer segment", r"\bsales\b",
        r"walmart", r"amazon", r"flipkart", r"grocery", r"fashion", r"adventure ?works",
        r"blinkit", r"zepto", r"\bfmcg\b", r"pizza sales", r"coffee sales",
    ],
    "Banking & Finance": [
        r"\bbank", r"\bloan", r"credit", r"fraud", r"\bstock", r"trading", r"financ", r"fintech",
        r"payment", r"transaction", r"portfolio", r"investment", r"\bupi\b", r"\bemi\b",
        r"default", r"crypto", r"bitcoin", r"\batm\b", r"money laundering", r"\bkyc\b",
    ],
    "Insurance": [r"insur", r"\bclaims?\b", r"policyholder", r"premium", r"underwrit"],
    "Supply Chain & Logistics": [
        r"supply chain", r"logistic", r"shipment", r"delivery", r"warehouse", r"inventory",
        r"procurement", r"freight", r"fleet", r"route optim", r"demand forecast", r"vendor",
        r"parcel", r"courier", r"last.mile", r"distribution cent",
    ],
    "Automobile": [
        r"\bcar\b", r"\bcars\b", r"vehicle", r"automobile", r"automotive", r"\bev\b",
        r"electric vehicle", r"used car", r"\btesla\b", r"driver drowsiness", r"number plate",
        r"license plate",
    ],
    "Telecom": [r"telecom", r"\btelco\b", r"subscriber", r"call drop", r"\b5g\b", r"mobile operator", r"network traffic"],
    "Education": [
        r"\bstudent", r"education", r"school", r"universit", r"\bcourse", r"e-?learning",
        r"edtech", r"\bexam", r"attendance", r"learning management",
    ],
    "Energy & Utilities": [
        r"\benergy", r"electricity", r"solar", r"wind turbine", r"\boil\b", r"\bgas\b",
        r"utility", r"power grid", r"power consumption", r"mining",
    ],
    "Agriculture": [r"\bcrop", r"agricult", r"\bfarm", r"\bsoil", r"plant disease", r"plant leaf", r"yield prediction", r"fertili"],
    "Marketing & Media": [
        r"marketing", r"campaign", r"advertis", r"social media", r"sentiment", r"\bmovie",
        r"\bmusic", r"spotify", r"netflix", r"youtube", r"twitter", r"instagram", r"\bnews\b",
        r"customer review",
    ],
    "Real Estate": [r"house price", r"real estate", r"property", r"housing", r"\brent(al)?\b"],
    "Travel & Hospitality": [
        r"\bhotel", r"airline", r"flight", r"travel", r"touris", r"booking", r"restaurant",
        r"zomato", r"swiggy", r"food delivery", r"\buber\b", r"\bola\b", r"airbnb",
    ],
    "Government & Public Sector": [r"government", r"public sector", r"\bcrime", r"police", r"census", r"election", r"smart city", r"homicide"],
}

_COMPILED_SECTOR_KW = {s: [re.compile(p, re.I) for p in pats] for s, pats in _SECTOR_KEYWORDS.items()}


def normalize_sector(raw) -> str:
    if not raw:
        return "General"
    s = str(raw).strip()
    for sec in SECTORS:
        if s.lower() == sec.lower():
            return sec
    low = s.lower()
    for key, sec in _SECTOR_SYNONYMS.items():
        if key in low:
            return sec
    return "General"


def keyword_sector(text: str) -> str:
    """Return best keyword-matched sector, or 'General' if nothing matches."""
    if not text:
        return "General"
    scores = Counter()
    for sec, pats in _COMPILED_SECTOR_KW.items():
        for p in pats:
            hits = len(p.findall(text))
            if hits:
                scores[sec] += hits
    if not scores:
        return "General"
    return scores.most_common(1)[0][0]


def resolve_project_sector(llm_sector, name: str, description: str) -> str:
    """
    LLM sector first. If the LLM said General/Technology/unknown, try keywords
    on the project text (free). Keeps tagging good even with a small model.
    """
    sec = normalize_sector(llm_sector)
    if sec in ("General", "Technology"):
        kw = keyword_sector(f"{name or ''}. {description or ''}")
        if kw != "General":
            return kw
    return sec


# ======================= EXPERIENCE BANDS =======================
EXPERIENCE_BANDS = [
    ("Fresher (0–1 yr)", 0.0, 1.0),
    ("1–3 yrs", 1.0, 3.0),
    ("3–5 yrs", 3.0, 5.0),
    ("5–8 yrs", 5.0, 8.0),
    ("8+ yrs", 8.0, 99.0),
]


def experience_band(years) -> str:
    try:
        y = float(years or 0)
    except (TypeError, ValueError):
        y = 0.0
    for label, lo, hi in EXPERIENCE_BANDS:
        if lo <= y < hi:
            return label
    return EXPERIENCE_BANDS[-1][0]
