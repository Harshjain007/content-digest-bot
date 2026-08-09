"""Configuration loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

# LLM backend selection: "anthropic" (default) or "ollama" (free, local).
# When "ollama", the bot calls a local Ollama server instead of the Anthropic API.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Any model you've pulled via `ollama pull`, e.g. llama3.1, qwen2.5, mistral, gemma2, phi3
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# Instagram login (optional). Without it, Instagram reels often can't be
# scraped, and the bot will ask you to paste the caption instead.
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "30000"))
