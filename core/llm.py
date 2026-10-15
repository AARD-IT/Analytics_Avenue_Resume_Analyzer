"""
One small wrapper for both providers (OpenAI + Groq).
Every call is JSON-mode, so we never pay for retries caused by broken JSON.

Provider choice (LLM_PROVIDER secret):
  "openai" -> uses OPENAI_API_KEY   (default when an OpenAI key exists)
  "groq"   -> uses GROQ_API_KEY
"""
import json
import logging
import re
import time

from core.config import Config

log = logging.getLogger("llm")


class LLMUnavailable(Exception):
    """Quota / daily limit reached — stop the batch, resume later."""


def _is_reasoning(model: str) -> bool:
    # gpt-5*, gpt-6*, o1/o3/o4 families don't accept temperature
    return bool(re.match(r"^(gpt-5|gpt-6|o\d)", model or ""))


def _retry_after(err) -> float:
    try:
        h = err.response.headers
        if h.get("retry-after-ms"):
            return float(h["retry-after-ms"]) / 1000 + 1
        if h.get("retry-after"):
            return float(h["retry-after"]) + 1
    except Exception:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(err))
    if m:
        secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return int(m.group(1) or 0) * 60 + secs + 1
    return 20.0


class BudgetExceeded(LLMUnavailable):
    pass


class LLM:
    def __init__(self, purpose: str = "chat", user: str = "system", enforce_budget: bool = True):
        """purpose: 'extract' (resume parsing) or 'chat' (router / answers / polish)."""
        from core import store
        self.store = store
        self.settings = store.get_settings()
        self.provider = Config.llm_provider()
        self.user = user
        self.purpose = purpose
        self.enforce_budget = enforce_budget
        self.client = None
        self.calls = 0
        self.tokens = 0
        self.cost_usd = 0.0
        self._no_temp = False
        self.reasoning_effort = "minimal"
        s = self.settings
        if self.provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=Config.OPENAI_API_KEY, max_retries=0, timeout=120)
            self.model = s["extract_model"] if purpose == "extract" else s["chat_model"]
            self.fallback = s["fallback_model"]
        elif self.provider == "groq":
            from groq import Groq
            self.client = Groq(api_key=Config.GROQ_API_KEY, max_retries=0, timeout=90)
            self.model = s["groq_extract_model"] if purpose == "extract" else s["groq_chat_model"]
            self.fallback = "llama-3.1-8b-instant"

    @property
    def ready(self) -> bool:
        return self.client is not None

    def _errors(self):
        if self.provider == "openai":
            import openai as m
        else:
            import groq as m
        return m

    def json_chat(self, system: str, user: str, max_tokens: int = 2500, temperature: float = 0) -> dict:
        if not self.client:
            raise LLMUnavailable("No LLM API key configured.")
        if self.enforce_budget:
            budget = float(self.settings.get("daily_budget_usd") or 0)
            if budget > 0 and self.store.cost_today() >= budget:
                raise BudgetExceeded(f"Daily AI budget of ${budget:.2f} reached.")
        E = self._errors()
        attempts = 0
        while True:
            attempts += 1
            kwargs = dict(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            if self.provider == "openai":
                reasoning = _is_reasoning(self.model)
                kwargs["max_completion_tokens"] = max_tokens + (3000 if reasoning else 0)
                if reasoning and self.reasoning_effort:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                if not reasoning and not self._no_temp:
                    kwargs["temperature"] = temperature
            else:
                kwargs["max_tokens"] = max_tokens
                kwargs["temperature"] = temperature
            try:
                resp = self.client.chat.completions.create(**kwargs)
                self.calls += 1
                u = getattr(resp, "usage", None)
                if u:
                    self.tokens += u.total_tokens or 0
                    cost = Config.price(self.model, u.prompt_tokens or 0, u.completion_tokens or 0)
                    self.cost_usd += cost
                    try:
                        self.store.log_usage(self.user, self.purpose, self.model, u.total_tokens or 0, cost)
                    except Exception:
                        pass
                content = resp.choices[0].message.content or "{}"
                return json.loads(content)
            except E.RateLimitError as e:
                msg = str(e).lower()
                if "insufficient_quota" in msg or "billing" in msg:
                    raise LLMUnavailable(f"OpenAI quota/billing issue: {e}")
                wait = _retry_after(e)
                daily = "per day" in msg or "tpd" in msg or "rpd" in msg
                if (daily or wait > 90) and self.fallback and self.model != self.fallback:
                    log.warning("%s hit its limit -> switching to %s", self.model, self.fallback)
                    self.model = self.fallback
                    continue
                if daily or attempts > 6:
                    raise LLMUnavailable(f"Rate limit reached: {e}")
                log.info("Rate limited, waiting %.0fs", wait)
                time.sleep(wait)
            except E.BadRequestError as e:
                msg = str(e).lower()
                # older/newer models that reject a parameter -> drop it and retry once
                if "reasoning_effort" in msg and self.reasoning_effort:
                    self.reasoning_effort = None
                    if attempts < 3:
                        continue
                if "temperature" in msg and not self._no_temp and attempts < 3:
                    self._no_temp = True
                    continue
                if attempts >= 2:
                    raise
                log.warning("Bad request (%s), retrying once", str(e)[:150])
            except json.JSONDecodeError:
                if attempts >= 2:
                    raise
            except (E.APIConnectionError, E.APITimeoutError, E.InternalServerError):
                if attempts >= 3:
                    raise
                time.sleep(5 * attempts)
