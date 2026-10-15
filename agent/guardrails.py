"""
Guardrails (pure Python, 0 tokens).

Input:
  * blocks searches on protected attributes (fair-hiring compliance)
  * blocks prompt-injection / system-prompt extraction attempts
  * caps message length
Output:
  * removes any email / phone in AI-written text that doesn't belong to a
    candidate in the current context (no leaking, no hallucinated contacts)
  * caps answer length
"""
import re

MAX_MSG = 1200
MAX_JD = 8000

_PROTECTED = re.compile(
    r"\b(gender|male|female|females|males|women|woman|girls?|boys?|ladies|gents|"
    r"religio\w*|hindu|muslim|christian|sikh|jain|caste|dalit|brahmin|"
    r"married|unmarried|marital|pregnan\w*|maternity|kids|children|"
    r"years old|aged?\s*(below|under|above|over|between)?\s*\d+|age (below|under|above|over|limit|between)|"
    r"young(er)?|nationality|ethnic\w*|skin colou?r|race|disabilit\w*|handicap\w*|"
    r"political|community)\b",
    re.I,
)
# phrases where those words are harmless
_PROTECTED_OK = re.compile(r"\b(women in tech program|race condition|community detection|young professional program)\b", re.I)

_INJECTION = re.compile(
    r"(ignore (all |the )?(previous|above|prior|earlier) (instructions|rules|prompts?)|"
    r"(reveal|show|print|repeat|leak) (me )?(your|the) (system )?(prompt|instructions|rules)|"
    r"system prompt|you are now|act as (an? )?(unrestricted|jailbroken)|jailbreak|developer mode|"
    r"disregard (your|the) (rules|guidelines)|pretend (you are|to be) not)",
    re.I,
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s\-()]{8,15}\d")


def check_input(text: str, is_jd: bool = False):
    """Return (ok, message_if_blocked, reason)."""
    t = (text or "").strip()
    if not t:
        return False, "Please type a question.", "empty"
    if len(t) > (MAX_JD if is_jd else MAX_MSG):
        return False, (f"That message is too long ({len(t)} characters). Please keep questions under {MAX_MSG} "
                       f"characters, or paste a job description starting with **JD:** (max {MAX_JD})."), "too_long"
    if _INJECTION.search(t):
        return False, ("I can only help with searching and contacting candidates. "
                       "I can't change my instructions or share them."), "injection"
    if _PROTECTED.search(t) and not _PROTECTED_OK.search(t) and not is_jd:
        return False, ("I can't filter or rank candidates by age, gender, religion, caste, marital status, "
                       "nationality, disability or similar personal attributes — that would be discriminatory. "
                       "I can search by **skills, experience, job category, industry sector, projects and location**."), "protected"
    return True, "", ""


def protected_in_jd(text: str) -> bool:
    return bool(_PROTECTED.search(text or "")) and not _PROTECTED_OK.search(text or "")


def clean_output(text: str, allowed_emails=(), allowed_phones=(), max_chars=2500) -> str:
    """Strip contacts that aren't from the candidates we gave the model."""
    allowed_e = {e.lower() for e in allowed_emails if e}
    allowed_p = {re.sub(r"\D", "", p)[-10:] for p in allowed_phones if p}

    def _e(m):
        return m.group(0) if m.group(0).lower() in allowed_e else "[email hidden]"

    def _p(m):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 10:
            return m.group(0)
        return m.group(0) if digits[-10:] in allowed_p else "[phone hidden]"

    out = _EMAIL.sub(_e, text or "")
    out = _PHONE.sub(_p, out)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + " …"
    return out.strip()
