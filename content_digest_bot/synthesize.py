"""Synthesis layer — turns extracted content into a structured deep-dive.

Supports two backends (selected via LLM_PROVIDER in .env):
  - "anthropic": calls the Anthropic API (needs ANTHROPIC_API_KEY)
  - "ollama":    calls a local Ollama server (free, no key, needs Ollama running)

Prompt formatting uses LangChain's FewShotPromptTemplate (see prompts.py) so
the model output stays consistent and well-structured.
"""
import logging

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
        raise ValueError("no JSON object found in model output")
    return json.loads(m.group(0))


# --------------------------------------------------------------- Anthropic
def _synthesize_anthropic(prompt, num_predict=2048):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "❌ ANTHROPIC_API_KEY is not set in .env (needed for LLM_PROVIDER=anthropic).")
    import anthropic
    from anthropic import (AuthenticationError, BadRequestError, RateLimitError)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=min(4096, num_predict),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except AuthenticationError:
        raise RuntimeError(
            "❌ Anthropic rejected your API key — it may be invalid or revoked. "
            "Check ANTHROPIC_API_KEY in .env.")
    except BadRequestError as e:
        msg = str(e)
        if "credit balance" in msg or "purchase credits" in msg:
            raise RuntimeError(
                "❌ Your Anthropic credit balance is too low. Add credits at "
                "https://console.anthropic.com/settings/billing  then retry.")
        if "model" in msg and ("not exist" in msg or "access" in msg):
            raise RuntimeError(
                f"❌ Model '{ANTHROPIC_MODEL}' isn't available on your plan. "
                "Check ANTHROPIC_MODEL in .env.")
        raise RuntimeError(f"❌ Anthropic request error: {msg}")
    except RateLimitError:
        raise RuntimeError(
            "❌ Anthropic rate-limited you. Wait a moment and retry; if it "
            "persists, your plan's throughput is low.")


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
        raise RuntimeError(
            f"❌ Couldn't reach Ollama at {OLLAMA_BASE_URL}. Is Ollama running? "
            "Start it with `ollama serve` (or just open the Ollama app) and make "
            f"sure you've pulled the model: `ollama pull {OLLAMA_MODEL}`.")
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"❌ Ollama timed out generating with '{OLLAMA_MODEL}'. The model may "
            "be too slow/large for your hardware. Try a smaller model "
            "(e.g. OLLAMA_MODEL=llama3.1:8b).")
    except KeyError:
        raise RuntimeError(
            "❌ Ollama returned an unexpected response — is the model name "
            f"'{OLLAMA_MODEL}' valid? Check `ollama list`.")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"❌ Ollama error: {e}")


def _call(prompt, num_predict=2048):
    if LLM_PROVIDER == "ollama":
        logger.info("Synthesizing via Ollama (%s)", OLLAMA_MODEL)
        return _synthesize_ollama(prompt, num_predict)
    logger.info("Synthesizing via Anthropic (%s)", ANTHROPIC_MODEL)
    return _synthesize_anthropic(prompt, num_predict)


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
