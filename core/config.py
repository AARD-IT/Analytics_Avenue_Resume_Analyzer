"""
Static config from secrets / env. Runtime-editable settings (models, budget,
company name…) live in MongoDB and are edited from the Admin page — see store.get_settings().
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
_SECRETS_FILE = BASE_DIR / ".streamlit" / "secrets.toml"
_file_cache = None


def _file_secrets() -> dict:
    global _file_cache
    if _file_cache is None:
        _file_cache = {}
        if _SECRETS_FILE.exists():
            try:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib
                with open(_SECRETS_FILE, "rb") as f:
                    _file_cache = tomllib.load(f)
            except Exception:
                _file_cache = {}
    return _file_cache


def get_secret(key: str, default=None):
    v = os.getenv(key)
    if v:
        return v
    v = _file_secrets().get(key)
    if v:
        return v
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def _list(value) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return [v.strip().lower() for v in str(value).split(",") if v.strip()]


class Config:
    BASE_DIR = BASE_DIR
    ASSETS = BASE_DIR / "assets"
    SEED_DIR = BASE_DIR / "data" / "seed"
    SEED_RESUMES = BASE_DIR / "data" / "resumes"

    APP_NAME = "Resume Analyzer"
    BRAND = "Analytics Avenue"

    # ---------------- storage ----------------
    MONGODB_URI = get_secret("MONGODB_URI", "")
    MONGODB_DB = get_secret("MONGODB_DB", "aa_resume_analyzer")

    # ---------------- access ----------------
    # Legacy: admin emails (not used — Admin is protected by ADMIN_PASSWORD).
    ADMIN_EMAILS = _list(get_secret("ADMIN_EMAILS", ""))
    # Fallback login when Google sign-in ([auth] secrets) is not configured
    ACCESS_CODE = get_secret("ACCESS_CODE", "")

    # ---------------- LLM ----------------
    OPENAI_API_KEY = get_secret("OPENAI_API_KEY", "")
    GROQ_API_KEY = get_secret("GROQ_API_KEY", "")
    LLM_PROVIDER = (get_secret("LLM_PROVIDER", "") or "").lower().strip()
    PROMPT_VERSION = "v3.0"
    MAX_RESUME_CHARS = 9000

    # USD per 1M tokens (input, output) – for cost display + budget
    PRICES = {
        "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1": (2.00, 8.00),
        "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
        "gpt-5-mini": (0.25, 2.00), "gpt-5-nano": (0.05, 0.40), "gpt-5": (1.25, 10.00),
        "llama-3.3-70b-versatile": (0.59, 0.79), "llama-3.1-8b-instant": (0.05, 0.08),
    }

    # ---------------- email (credentials stay in secrets) ----------------
    SMTP_HOST = get_secret("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(get_secret("SMTP_PORT", 587))
    SMTP_USER = get_secret("SMTP_USER", "")
    SMTP_PASSWORD = get_secret("SMTP_PASSWORD", "")
    FROM_EMAIL = get_secret("FROM_EMAIL", "") or SMTP_USER

    # ---------------- Google Drive ----------------
    DEFAULT_DRIVE_FOLDER = get_secret(
        "DRIVE_FOLDER_ID", "1bSH_SI2-PSCpWKqDOO87LHIaK5PeMqMf4deRig7N-0NfoydGgsWfrwJojX-LTsQzKjRDkieP")

    # ---------------- defaults for runtime settings ----------------
    DEFAULT_SETTINGS = {
        "company_name": "Analytics Avenue",
        "sender_name": "Analytics Avenue Talent Team",
        "reply_to": "",
        "extract_model": "gpt-4.1-mini",
        "fallback_model": "gpt-4o-mini",
        "chat_model": "gpt-4.1-nano",
        "groq_extract_model": "llama-3.3-70b-versatile",
        "groq_chat_model": "llama-3.1-8b-instant",
        "daily_budget_usd": 2.0,
        "max_email_batch": 25,
        "email_cooldown_days": 30,
        "email_delay_seconds": 1.5,
        "drive_folders": [],
        "default_template": "Interview Invitation",
        "results_per_answer": 8,
    }

    @staticmethod
    def llm_provider() -> str:
        p = Config.LLM_PROVIDER
        if p == "openai" and Config.OPENAI_API_KEY:
            return "openai"
        if p == "groq" and Config.GROQ_API_KEY:
            return "groq"
        if Config.OPENAI_API_KEY:
            return "openai"
        if Config.GROQ_API_KEY:
            return "groq"
        return ""

    @staticmethod
    def smtp_ready() -> bool:
        return bool(Config.SMTP_USER and Config.SMTP_PASSWORD and Config.FROM_EMAIL)

    @staticmethod
    def price(model, pin_tokens, pout_tokens) -> float:
        a, b = Config.PRICES.get(model, (0.0, 0.0))
        return (pin_tokens * a + pout_tokens * b) / 1_000_000
