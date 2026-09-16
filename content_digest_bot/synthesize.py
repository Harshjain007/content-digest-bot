"""Synthesis layer — turns extracted content into a structured deep-dive.

Supports two backends (selected via LLM_PROVIDER in .env):
  - "anthropic": calls the Anthropic API (needs ANTHROPIC_API_KEY)
  - "ollama":    calls a local Ollama server (free, no key, needs Ollama running)

Prompt formatting uses LangChain's FewShotPromptTemplate (see prompts.py) so
the model output stays consistent and well-structured.
"""
import contextvars
import logging
import time

from .errors import BotError, TransientError
from .config import (ANTHROPIC_API_KEY, ANTHROPIC_MODEL, LLM_PROVIDER,
                     OLLAMA_BASE_URL, OLLAMA_MODEL)
from .prompts import build_prompt_text

logger = logging.getLogger(__name__)


def _strip_think(text):
    """Remove Qwen3-style <think>...</think> reasoning blocks if present."""
    import re
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def _parse_json(text):
    """Extract a JSON object from model output (handles ```json fences)."""
    import json
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise BotError("The model didn't return a usable card. Try resending.")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise BotError("The model's reply wasn't valid JSON. Try resending.")


# --------------------------------------------------------------- Anthropic
def _synthesize_anthropic(prompt, num_predict=2048):
    if not ANTHROPIC_API_KEY:
        raise BotError(
            "ANTHROPIC_API_KEY isn't set in .env (needed for LLM_PROVIDER=anthropic).")
    import anthropic
    from anthropic import (APIConnectionError, APIStatusError,
                           AuthenticationError, BadRequestError,
                           NotFoundError, RateLimitError)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=min(4096, num_predict),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except AuthenticationError:
        raise BotError(
            "Anthropic rejected the API key — it may be invalid or revoked.")
    except BadRequestError as e:
        msg = str(e)
        if "credit balance" in msg or "purchase credits" in msg:
            raise BotError(
                "Anthropic credit balance is too low to run this.")
        if "model" in msg and ("not exist" in msg or "access" in msg):
            raise BotError(
                f"Model '{ANTHROPIC_MODEL}' isn't available on this plan.")
        raise BotError(f"Anthropic rejected the request: {msg[:160]}")
    except APIConnectionError:
        raise TransientError("Couldn't reach Anthropic — network looks down.")
    except APIStatusError as e:
        if e.status_code in (500, 502, 503, 529):
            raise TransientError("Anthropic is overloaded — try again shortly.")
        raise BotError(f"Anthropic returned {e.status_code}.")
    except NotFoundError:
        # Almost always a stale ANTHROPIC_MODEL in .env rather than an outage.
        raise BotError(
            f"The model '{ANTHROPIC_MODEL}' doesn't exist on this account. "
            "Update ANTHROPIC_MODEL in .env.")
    except RateLimitError:
        raise TransientError("Anthropic is rate-limiting — try again shortly.")


# ----------------------------------------------------------------- Ollama
def _synthesize_ollama(prompt, num_predict=2048):
    import requests
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": num_predict},
        "think": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=300)
        r.raise_for_status()
        return _strip_think(r.json()["message"]["content"])
    except requests.exceptions.ConnectionError:
        raise TransientError(f"Ollama isn't reachable at {OLLAMA_BASE_URL}.")
    except requests.exceptions.Timeout:
        raise TransientError(f"Ollama timed out generating with '{OLLAMA_MODEL}'.")
    except KeyError:
        raise BotError(
            f"Ollama returned an unexpected response — is '{OLLAMA_MODEL}' pulled?")
    except Exception as e:  # noqa: BLE001
        raise BotError(f"Ollama error: {e}")


# Set when a call had to fall back to the paid provider, so the bot can say
# so in its reply. A ContextVar rather than a module global: handlers
# interleave at every await, and a global would misattribute the note to
# whichever chat happened to read it first.
used_fallback = contextvars.ContextVar("used_fallback", default=False)


def _retrying(fn, *args, attempts=3, **kwargs):
    """Run fn, retrying only the failures that a retry can actually fix.

    Backs off 1s then 2s. A permanent BotError propagates on the first try —
    a bad key or a missing model fails identically however often it is asked.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except TransientError as e:
            if attempt == attempts:
                raise
            delay = 2 ** (attempt - 1)
            logger.warning("%s — retrying in %ss (attempt %d/%d)",
                           e, delay, attempt + 1, attempts)
            time.sleep(delay)


def _call(prompt, num_predict=2048):
    """Run the prompt on the configured provider.

    When the provider is Ollama and the local server is asleep, an unreachable
    daemon used to cost the user their submission outright. If an Anthropic key
    is configured we retry there instead and flag it, because silently
    spending money is its own surprise — the bot says which one it used.
    """
    used_fallback.set(False)
    if LLM_PROVIDER == "ollama":
        logger.info("Synthesizing via Ollama (%s)", OLLAMA_MODEL)
        try:
            return _retrying(_synthesize_ollama, prompt, num_predict, attempts=2)
        except BotError as e:
            if not ANTHROPIC_API_KEY:
                raise
            logger.warning("Ollama unavailable (%s) — falling back to Anthropic", e)
            used_fallback.set(True)
            return _retrying(_synthesize_anthropic, prompt, num_predict)
    logger.info("Synthesizing via Anthropic (%s)", ANTHROPIC_MODEL)
    return _retrying(_synthesize_anthropic, prompt, num_predict)


def synthesize(data, user_note=None, mode="full", num_predict=2048):
    """Dispatch to the configured LLM backend and return text.

    mode="summary" -> short bullet summary (first message)
    mode="full"    -> full structured deep-dive (on request)
    num_predict    -> token cap for the model output
    Returns None when there is nothing to digest.
    """
    if data.get("text") is None and data.get("needs_caption"):
        return None
    prompt = build_prompt_text(data, user_note, mode=mode)
    return _call(prompt, num_predict=num_predict)


def synthesize_json(prompt, num_predict=2048):
    """Return parsed JSON from the model given a JSON-request prompt."""
    raw = _call(prompt, num_predict=num_predict)
    return _parse_json(raw)
