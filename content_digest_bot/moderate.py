"""Topic gate: only allow AI / generative-AI related input.

Uses a fast local LLM call (Ollama) to decide if the user's message is about
artificial intelligence or generative AI (tools, models, techniques, use cases,
news, concepts). Anything else is rejected with a short, friendly message.

Kept separate from synthesis so we don't waste a full deep-dive on off-topic text.
"""
import logging

import requests

from .config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

GATE_PROMPT = """You are a topic moderator for a personal knowledge-keeper bot.
ALLOW a message if it is about EITHER of these:
  (a) artificial intelligence or generative AI — LLMs, diffusion models,
      LangChain, RAG, agents, prompts, AI tools, AI news, ML, DL, CV, NLP; or
  (b) upskilling, productivity, career growth, learning technique, focus,
      habits, or developer self-improvement.

Both are subjects this bot files, so (b) is on-topic even with no AI angle.

Reply with exactly one word: ALLOW or REJECT.
If unsure but it plausibly fits (a) or (b), ALLOW. REJECT only what is clearly
neither — sports, cooking, politics, celebrity gossip, personal chit-chat.

USER MESSAGE:
\"\"\"{msg}\"\"\"

Answer (ALLOW or REJECT):"""

REJECT_MSG = (
    "🚫 I only file two kinds of thing: <b>AI / generative AI</b> "
    "(LLMs, RAG, agents, prompts, AI tools) and <b>upskilling / productivity</b>.\n\n"
    "Send a link or a topic in either lane and I'll digest it. "
    "Try <code>what is agentic RAG</code>, or paste an article on deep work."
)


def is_allowed(msg, model=None):
    """Return True if the message is on-topic (AI / generative AI)."""
    model = model or OLLAMA_MODEL
    payload = {
        "model": model,
        "messages": [{"role": "user",
                      "content": GATE_PROMPT.format(msg=msg)}],
        "stream": False,
        "options": {"num_predict": 8, "temperature": 0},
        "think": False,
    }
    try:
        r = requests.post(f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                          json=payload, timeout=60)
        r.raise_for_status()
        ans = r.json()["message"]["content"].strip().upper()
        return "ALLOW" in ans
    except Exception as e:  # noqa: BLE001
        logger.warning("Gate call failed, defaulting to ALLOW: %s", e)
        return True  # fail open so a broken gate doesn't block real use
