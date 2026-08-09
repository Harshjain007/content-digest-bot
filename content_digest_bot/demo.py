"""Run the full pipeline locally without Telegram.

Usage:
  source .venv/bin/activate
  python -m content_digest_bot.demo "explain vector databases"
  python -m content_digest_bot.demo "https://www.youtube.com/watch?v=aircAruvnKk"

Needs ANTHROPIC_API_KEY in .env. Prints the brief and saves it to notes/.
"""
import os
import re
import sys
from datetime import datetime, timezone

from .config import ANTHROPIC_API_KEY
from .extractors import extract
from .synthesize import synthesize


def main():
    if len(sys.argv) < 2:
        print('Usage: python demo.py "<topic or URL>"')
        return
    query = " ".join(sys.argv[1:])
    if not ANTHROPIC_API_KEY:
        print("ERROR: set ANTHROPIC_API_KEY in .env first.")
        return

    print("🔍 Extracting…")
    data = extract(query)
    if data.get("needs_caption"):
        print("⚠️ Instagram blocked scraping — paste the caption text and re-run.")
        return
    if data.get("failed"):
        print("⚠️ Extraction failed. Paste the text directly or try another link.")
        return

    print(f"   source={data['source']}  title={data.get('title','')[:50]!r}"
          f"  text_len={len(data.get('text') or '')}")
    print("🧠 Synthesizing with Claude…\n")
    try:
        brief = synthesize(data, user_note=data.get("user_note"))
    except RuntimeError as e:
        print(str(e))
        return

    print("=" * 70)
    print(brief)
    print("=" * 70)

    safe = re.sub(r"\W+", "_", (data.get("title") or "note"))[:50]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    notes = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notes")
    os.makedirs(notes, exist_ok=True)
    path = os.path.join(notes, f"{ts}_{safe}.md")
    with open(path, "w") as f:
        f.write(f"# {data.get('title')}\n\n"
                f"Source: {data.get('source')} — {data.get('url') or 'topic'}\n"
                f"Generated: {ts}\n\n---\n\n{brief}\n")
    print(f"\n📝 saved to {path}")


if __name__ == "__main__":
    main()
