"""
LangGraph agent for the Resume Analyzer chatbot.

    START → guard_in ─┬─(blocked)──────────────────────────────► guard_out → END
                      └► route ─► one tool node ─────────────────► guard_out → END
                                   └ email_draft → approve (interrupt: HR clicks Send/Cancel) → guard_out

* route   : free rules first, 1 nano LLM call only when rules can't tell
* tools   : search · refine · count · stats · profile · question · compare ·
            show_resume · match_jd · email_draft · smalltalk
* approve : human-in-the-loop — nothing is emailed without an explicit click
* memory  : LangGraph checkpointer keeps last result list per HR user/session,
            so "#2", "top 3", "them", "among these" work across turns
"""
import logging
import re
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import guardrails as G
from agent import router as R
from agent import tools as T
from core import store
from core.config import Config
from core.llm import LLM, LLMUnavailable

log = logging.getLogger("agent")
MAX_LAST = 60


class State(TypedDict, total=False):
    user: str
    message: str
    intent: str
    filters: dict
    focus_ids: list
    last_ids: list
    last_filters: dict
    route_src: str           # rules | llm
    response: dict           # {text, cards, table, kind, ...}
    llm_text: bool           # response text written by an LLM → output guard
    cost: float
    route_cost: float


# ------------------------------------------------------------------ helpers
def _llm_ok() -> bool:
    if not Config.llm_provider():
        return False
    s = store.get_settings()
    b = float(s.get("daily_budget_usd") or 0)
    return not (b > 0 and store.cost_today() >= b)


def _names(ids):
    df = store.candidates_df()
    m = dict(zip(df["id"], df["name"]))
    return [m.get(i, "?") for i in ids]


def _resp(text, kind="text", **kw):
    return {"text": text, "kind": kind, **kw}


HELP = """**Here's what I can do** — just type naturally:

- **Search** — *data analysts with 3+ years in manufacturing* · *BDE freshers in Chennai* · *who knows PySpark and Airflow*
- **Count** — *how many data engineers 2 to 5 years?* · *overview*
- **Sector projects** — *candidates with healthcare projects* · *banking projects using python*
- **Profile** — *tell me about #2* · *does #3 know Power BI?*
- **Compare** — *compare #1 and #3*
- **Resume** — *show resume of #2* (or click *View resume* on any card)
- **Job match** — paste a job description starting with **JD:**
- **Email** — *send interview email to top 3 for Data Analyst* (or click *Email* on any card)
- **Refine** — *among them only Chennai* · *from these, with Tableau*"""


# ------------------------------------------------------------------ nodes
def guard_in(state: State):
    msg = state.get("message", "")
    is_jd = msg.lower().startswith(("jd:", "job description:")) or len(msg) > 450
    ok, why, reason = G.check_input(msg, is_jd=is_jd)
    base = {"response": {}, "intent": "", "filters": {}, "focus_ids": [], "llm_text": False, "cost": 0.0, "route_cost": 0.0,
            "route_src": "rules"}
    if not ok:
        base.update(intent="blocked", response=_resp(why, "guard", reason=reason))
    return base


def route(state: State):
    msg = state["message"]
    last = state.get("last_ids") or []
    intent, sure = R.rule_route(msg, bool(last))
    filters = R.parse_filters(msg) if intent != "match_jd" else {}
    refs = R.parse_refs(msg, len(last)) if last else []
    focus = [last[r - 1] for r in refs]
    src, rcost = "rules", 0.0

    if intent in ("profile", "question", "show_resume", "compare", "email") and not focus or (not sure and not R.has_filters(filters)):
        df = store.candidates_df()
        hits = R.match_names(msg, dict(zip(df["id"], df["name"])))
        if hits and not focus:
            focus = hits
            if not sure:
                intent, sure = "profile", True

    # rules matched a generic "search", but most of the message wasn't understood → let the LLM read it
    weak = not (filters.get("categories") or filters.get("skills") or filters.get("sectors"))
    if (intent in ("search", "count", "") and len(R.unknown_words(msg, filters)) >= (1 if weak else 2) and _llm_ok()
            and len(msg) < 400):
        sure = False
        filters_rules = filters
        try:
            r = R.llm_route(msg, _names(last), state.get("user", ""))
            src, rcost = "llm", r.get("cost", 0.0)
            if r["intent"] in ("search", "count", "refine", "match_jd", "off_topic", "help", "stats"):
                intent, sure = r["intent"], True
                merged = dict(filters_rules)
                for k, v in r["filters"].items():
                    if v not in (None, "", []):
                        merged[k] = v
                filters = merged
        except Exception as e:
            log.warning("LLM route failed: %s", e)
        if not sure and intent:
            sure = True

    if not sure:
        if R.has_filters(filters):
            intent = "refine" if (last and re.search(r"\b(only|among|from (them|these)|of them)\b", msg.lower())) else "search"
        elif focus:
            intent = "profile"
        elif _llm_ok():
            try:
                r = R.llm_route(msg, _names(last), state.get("user", ""))
                src, rcost = "llm", r.get("cost", 0.0)
                intent, filters = r["intent"], r["filters"]
                if r["refs"] and last:
                    focus = [last[i - 1] for i in r["refs"] if 1 <= i <= len(last)]
                if r["names"] and not focus:
                    df = store.candidates_df()
                    focus = R.match_names(" ".join(r["names"]), dict(zip(df["id"], df["name"])))
            except LLMUnavailable:
                intent = "help"
            except Exception as e:
                log.warning("LLM route failed: %s", e)
                intent = "help"
        else:
            intent = "help"

    if intent == "count" and not R.has_filters(filters):
        intent = "stats"
    if intent == "search" and not R.has_filters(filters) and last and refs:
        intent = "profile"
    try:
        store.log_question(state.get("user", ""), msg, intent, src == "llm")
    except Exception:
        pass
    return {"intent": intent, "filters": filters, "focus_ids": focus, "route_src": src, "route_cost": rcost}


def smalltalk(state: State):
    i = state["intent"]
    if i == "greeting":
        n = len(store.candidates_df())
        return {"response": _resp(f"Hello. I'm the **Analytics Avenue Resume Analyzer**. I have **{n} candidates** indexed. "
                                  "Ask me anything — e.g. *data analysts with 3+ years in manufacturing*.", "help")}
    if i == "off_topic":
        return {"response": _resp("I'm focused on hiring — searching candidates, their projects and contacting them. "
                                  "Try *show BDE freshers* or type **help**.", "guard", reason="off_topic")}
    return {"response": _resp(HELP, "help")}


def stats(state: State):
    df = store.candidates_df()
    return {"response": _resp(f"**{len(df)} candidates** in the database. Breakdown by category × experience:", "stats",
                              table=T.stats_table(df).to_dict("records"),
                              table2=T.sector_table(df).to_dict("records"))}


def _list_response(df, filters, verb="Found", extra=""):
    n_show = int(store.get_settings().get("results_per_answer", 8))
    desc = T.describe_filters(filters)
    if df.empty:
        # helpful relaxation hints (0 tokens)
        hints = []
        for key in ("location", "skills", "sectors", "min_exp"):
            if filters.get(key):
                relaxed = {**filters, key: [] if key in ("skills", "sectors") else (None if key == "min_exp" else "")}
                if key == "min_exp":
                    relaxed["max_exp"] = filters.get("max_exp")
                n = len(T.search(relaxed))
                if n:
                    hints.append(f"without the **{key.replace('_', ' ')}** filter → {n} candidates")
        tip = ("\n\nTry " + "; ".join(hints) + ".") if hints else ""
        return _resp(f"No candidates match **{desc}**.{tip}", "empty"), []
    ids = df["id"].tolist()
    text = f"{verb} **{len(ids)} candidate{'s' if len(ids) != 1 else ''}** · {desc}{extra}"
    if len(ids) > n_show:
        text += f"\n\nShowing top {n_show} by experience — say *show more* or refine (e.g. *among them only Chennai*)."
    cards = [{"id": i, "sectors": filters.get("sectors") or []} for i in ids[:n_show]]
    by_cat = df["category"].value_counts()
    breakdown = " · ".join(f"{c}: {n}" for c, n in by_cat.items()) if len(by_cat) > 1 else ""
    return _resp(text, "list", cards=cards, breakdown=breakdown, total=len(ids)), ids


def search(state: State):
    f = state.get("filters") or {}
    base = (state.get("last_ids") or []) if state["intent"] == "refine" else None
    if state["intent"] == "refine":
        merged = {**(state.get("last_filters") or {})}
        for k, v in f.items():
            if v not in (None, "", []):
                merged[k] = v
        f = merged
    df = T.search(f, base_ids=base)
    resp, ids = _list_response(df, f, "Narrowed to" if base is not None else "Found")
    out = {"response": resp}
    if ids:
        out.update(last_ids=ids[:MAX_LAST], last_filters=f)
    return out


def show_more(state: State):
    last = state.get("last_ids") or []
    n = int(store.get_settings().get("results_per_answer", 8))
    cards = [{"id": i, "sectors": (state.get("last_filters") or {}).get("sectors") or []} for i in last[n:n * 3]]
    if not cards:
        return {"response": _resp("That's everything from the last search.", "text")}
    return {"response": _resp(f"More results ({n + 1}–{n + len(cards)} of {len(last)}):", "list", cards=cards,
                              offset=n, total=len(last))}


def count(state: State):
    f = state.get("filters") or {}
    df = T.search(f)
    desc = T.describe_filters(f)
    text = f"**{len(df)}** candidate{'s' if len(df) != 1 else ''} · {desc}"
    table = T.stats_table(df).to_dict("records") if len(df) else []
    out = {"response": _resp(text + ("\n\nSay *show them* to see the cards." if len(df) else ""), "count",
                             table=table, total=len(df))}
    if len(df):
        out.update(last_ids=df["id"].tolist()[:MAX_LAST], last_filters=f)
    return out


def profile(state: State):
    ids = (state.get("focus_ids") or [])[:3]
    if not ids:
        return {"response": _resp("Which candidate? Say e.g. *tell me about #2* or use a name.", "text")}
    last = state.get("last_ids") or []
    return {"response": _resp("", "profile", cards=[{"id": i, "detail": True,
                                                    "pos": (last.index(i) + 1) if i in last else None} for i in ids])}


def question(state: State):
    ids = (state.get("focus_ids") or [])[:3]
    if not ids:
        return profile(state)
    if not _llm_ok():
        return profile(state)
    cands = [store.get_candidate(i) for i in ids]
    ctx = "\n\n".join(T.compact_profile(c, idx=n + 1) for n, c in enumerate(cands))
    sys = ("You are an HR assistant. Answer ONLY from the candidate profiles given. If the profile doesn't say, "
           "answer 'Not mentioned in the resume.' Be brief (max 80 words). Never comment on age, gender, religion, "
           'caste, marital status or nationality. Reply JSON {"answer": str}.')
    try:
        llm = LLM("chat", user=state.get("user", ""))
        data = llm.json_chat(sys, f"PROFILES:\n{ctx}\n\nQUESTION: {state['message']}", max_tokens=250)
        ans = str(data.get("answer") or "Not mentioned in the resume.")
        return {"response": _resp(ans, "answer", cards=[{"id": i} for i in ids], compact=True,
                                  allowed=[(c.get("email"), c.get("phone")) for c in cands]),
                "llm_text": True, "cost": llm.cost_usd}
    except LLMUnavailable:
        return profile(state)


def compare(state: State):
    ids = (state.get("focus_ids") or [])[:4]
    if len(ids) < 2:
        return {"response": _resp("Tell me which candidates to compare, e.g. *compare #1 and #3*.", "text")}
    cands = [store.get_candidate(i) for i in ids]
    rows = []
    for c in cands:
        rows.append({
            "Candidate": c.get("name"), "Category": c.get("category"),
            "Experience (yrs)": c.get("total_experience"), "Internship (yrs)": c.get("internship_experience"),
            "Current role": c.get("current_role"), "Top skills": ", ".join(list(c.get("skills", []))[:6]),
            "Project sectors": ", ".join(c.get("project_sectors", [])) or "—",
            "# Projects": len([p for p in c.get("projects", []) if not str(p.get("name", "")).startswith("(auto")]),
        })
    verdict, cost, used = "", 0.0, False
    if _llm_ok():
        try:
            llm = LLM("chat", user=state.get("user", ""))
            ctx = "\n\n".join(T.compact_profile(c, idx=n + 1) for n, c in enumerate(cands))
            data = llm.json_chat(
                "Compare candidates for a recruiter using ONLY the profiles. Neutral, factual, max 90 words. "
                "Mention strengths per candidate and who fits which kind of role. No personal attributes. "
                'JSON {"verdict": str}.', f"PROFILES:\n{ctx}\n\nRequest: {state['message']}", max_tokens=300)
            verdict, cost, used = str(data.get("verdict") or ""), llm.cost_usd, True
        except LLMUnavailable:
            pass
    return {"response": _resp(verdict or "Side-by-side comparison:", "compare", table=rows,
                              cards=[{"id": i} for i in ids], compact=True,
                              allowed=[(c.get("email"), c.get("phone")) for c in cands]),
            "llm_text": used, "cost": cost}


def show_resume(state: State):
    ids = (state.get("focus_ids") or [])[:3]
    if not ids:
        return {"response": _resp("Whose resume? Say e.g. *show resume of #2* or a name.", "text")}
    return {"response": _resp("", "resume", ids=ids)}


def match_jd(state: State):
    jd = re.sub(r"^(jd|job description)\s*:\s*", "", state["message"], flags=re.I).strip()
    note = ""
    if G.protected_in_jd(jd):
        note = "\n\nNote: personal-attribute requirements in the JD (age, gender, etc.) were **ignored**."
    req, cost = None, 0.0
    if _llm_ok() and len(jd) > 250:
        try:
            req, cost = T.jd_requirements_llm(jd, state.get("user", ""))
        except Exception:
            req = None
    if not req:
        req = T.jd_requirements_rules(jd)
    df = T.jd_match(req, top_n=int(store.get_settings().get("results_per_answer", 8)))
    df = df[df["match"] > 0] if len(df) else df
    if df.empty:
        return {"response": _resp("No suitable candidates found for this JD.", "empty")}
    text = (f"**Top {len(df)} matches** for the JD — looking for **{req['category']}**, "
            f"{req['min_exp']:g}+ yrs, skills: {', '.join(req['skills'][:10]) or '—'}"
            f"{' · sectors: ' + ', '.join(req['sectors']) if req['sectors'] else ''}{note}")
    cards = [{"id": r["id"], "match": int(r["match"]), "matched_skills": r["matched_skills"],
              "sectors": req.get("sectors") or []} for _, r in df.iterrows()]
    ids = df["id"].tolist()
    return {"response": _resp(text, "list", cards=cards, total=len(ids)), "last_ids": ids,
            "last_filters": {"categories": [req["category"]], "skills": req["skills"][:5]}, "cost": cost}


def _pick_template(msg: str) -> dict:
    tpls = store.templates()
    if not tpls:
        return {}
    low = msg.lower()
    for key, name in [("sector", "Sector Project Outreach"), ("project", "Sector Project Outreach"),
                      ("talent pool", "Talent Pool – Keep in Touch"), ("keep in touch", "Talent Pool – Keep in Touch"),
                      ("opening", "Experience-based Opening"), ("interview", "Interview Invitation")]:
        if key in low:
            t = next((t for t in tpls if t["_id"] == name), None)
            if t:
                return t
    for t in tpls:
        if t["_id"].lower() in low:
            return t
    default = store.get_settings().get("default_template")
    return next((t for t in tpls if t["_id"] == default), tpls[0])


def _pick_opening(msg: str) -> dict:
    low = msg.lower()
    ops = store.openings(active_only=True)
    best, best_score = None, 0
    for o in ops:
        words = [w for w in re.split(r"\W+", o["title"].lower()) if len(w) > 2]
        score = sum(1 for w in words if w in low) / max(len(words), 1)
        if o.get("location") and o["location"].lower() in low:
            score += 0.3
        if score > best_score:
            best, best_score = o, score
    return best if best_score >= 0.5 else {}


def email_draft(state: State):
    from services import mailer
    last = state.get("last_ids") or []
    ids = state.get("focus_ids") or []
    if not ids:
        m = re.search(r"\btop\s+(\d+)", R._norm(state["message"]))
        ids = last[: int(m.group(1))] if m else last[:5]
    if not ids:
        return {"response": _resp("Who should I email? Search first (e.g. *BDE freshers*), then say *email top 3*.", "text")}
    tpl = _pick_template(state["message"])
    opening = _pick_opening(state["message"])
    sector = ((state.get("last_filters") or {}).get("sectors") or [None])[0]
    drafts = [mailer.draft(store.get_candidate(i), tpl, opening, sector) for i in ids]
    return {"response": _resp("", "drafts", drafts=drafts, template=tpl.get("_id"), opening=opening.get("title", ""))}


def approve(state: State):
    """Human-in-the-loop: pauses the graph until HR clicks Send or Cancel in the UI."""
    from services import mailer
    resp = state["response"]
    decision = interrupt({"drafts": resp["drafts"], "template": resp.get("template"), "opening": resp.get("opening")})
    if not isinstance(decision, dict) or decision.get("action") != "send":
        return {"response": _resp("Cancelled. No emails were sent.", "text")}
    drafts = decision.get("drafts") or resp["drafts"]
    try:
        res = mailer.send(drafts, sent_by=state.get("user", ""))
    except Exception as e:
        return {"response": _resp(f"Could not send: {e}", "text")}
    text = f"Sent **{res['sent']}** email(s)"
    if res["failed"]:
        text += f" · {res['failed']} failed"
    if res["skipped"]:
        text += f" · {res['skipped']} skipped"
    if res["errors"]:
        text += "\n\n" + "\n".join(f"- {e}" for e in res["errors"][:8])
    return {"response": _resp(text, "sent", result=res)}


def guard_out(state: State):
    resp = dict(state.get("response") or {})
    if state.get("llm_text") and resp.get("text"):
        allowed = resp.get("allowed") or []
        resp["text"] = G.clean_output(resp["text"], [a[0] for a in allowed], [a[1] for a in allowed])
    resp["route"] = state.get("route_src", "rules")
    resp["intent"] = state.get("intent")
    resp["cost"] = round(float(state.get("cost") or 0) + float(state.get("route_cost") or 0), 6)
    return {"response": resp}


# ------------------------------------------------------------------ graph
def _after_guard(state: State):
    return "guard_out" if state.get("intent") == "blocked" else "route"


def _dispatch(state: State):
    i = state.get("intent")
    if i in ("search", "refine"):
        if re.search(r"\b(show|see|more|next)\b.*\bmore\b|\bshow more\b|\bnext\b", state["message"].lower()) and state.get("last_ids"):
            return "show_more"
        return "search"
    if i == "count" and re.search(r"\bshow (them|all)\b", state["message"].lower()):
        return "search"
    return {"greeting": "smalltalk", "help": "smalltalk", "off_topic": "smalltalk", "stats": "stats",
            "count": "count", "profile": "profile", "question": "question", "compare": "compare",
            "show_resume": "show_resume", "match_jd": "match_jd", "email": "email_draft"}.get(i, "smalltalk")


def _pre_route(state: State):
    low = state["message"].lower().strip()
    if state.get("last_ids") and re.fullmatch(r"(show|see|give|list)?\s*(me\s+)?(them|all of them|more|next|the rest)\W*", low):
        return "show_more" if re.search(r"more|next|rest", low) else "show_last"
    return _dispatch(state)


def show_last(state: State):
    last = state.get("last_ids") or []
    n = int(store.get_settings().get("results_per_answer", 8))
    f = state.get("last_filters") or {}
    return {"response": _resp(f"Here they are · **{len(last)}** candidates · {T.describe_filters(f)}", "list",
                              cards=[{"id": i, "sectors": f.get("sectors") or []} for i in last[:n]], total=len(last))}


def build_graph():
    g = StateGraph(State)
    for name, fn in [("guard_in", guard_in), ("route", route), ("smalltalk", smalltalk), ("stats", stats),
                     ("search", search), ("show_more", show_more), ("show_last", show_last), ("count", count),
                     ("profile", profile), ("question", question), ("compare", compare),
                     ("show_resume", show_resume), ("match_jd", match_jd), ("email_draft", email_draft),
                     ("approve", approve), ("guard_out", guard_out)]:
        g.add_node(name, fn)
    g.add_edge(START, "guard_in")
    g.add_conditional_edges("guard_in", _after_guard, {"route": "route", "guard_out": "guard_out"})
    targets = ["smalltalk", "stats", "search", "show_more", "show_last", "count", "profile", "question",
               "compare", "show_resume", "match_jd", "email_draft"]
    g.add_conditional_edges("route", _pre_route, {t: t for t in targets})
    for t in targets:
        if t != "email_draft":
            g.add_edge(t, "guard_out")
    g.add_conditional_edges("email_draft", lambda s: "approve" if s["response"].get("kind") == "drafts" else "guard_out",
                            {"approve": "approve", "guard_out": "guard_out"})
    g.add_edge("approve", "guard_out")
    g.add_edge("guard_out", END)
    return g.compile(checkpointer=MemorySaver())


def mermaid() -> str:
    try:
        return build_graph().get_graph().draw_mermaid()
    except Exception:
        return ""
