"""Smoke-test the extraction layer against REAL public content.

Run:  python -m tests.test_extractors
Requires deps installed. No API keys needed for YouTube/Article extraction.
"""
import logging
import sys

from content_digest_bot.extractors import (classify, extract_article,
                                           extract_youtube)

logging.basicConfig(level=logging.INFO)

# A well-known, stable YouTube video (Big Buck Bunny is a safe public test).
YT_URL = "https://www.youtube.com/watch?v=aircAruvnKk"
# A stable Wikipedia article (great for trafilatura text-stripping).
ART_URL = "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)"


def main():
    print("=== classify ===")
    for s in [YT_URL, ART_URL, "https://instagram.com/reel/ABC123",
              "explain vector databases"]:
        print(classify(s))

    print("\n=== YouTube extraction ===")
    d = extract_youtube(YT_URL)
    print("source:", d["source"], "| title:", d["title"][:60])
    print("had_transcript:", d.get("had_transcript"))
    print("text length:", len(d["text"] or ""))
    print("text sample:", (d["text"] or "")[:200].replace("\n", " "))

    print("\n=== Article extraction ===")
    d2 = extract_article(ART_URL)
    print("source:", d2["source"], "| title:", (d2["title"] or "")[:60])
    print("failed:", d2.get("failed"))
    print("text length:", len(d2["text"] or ""))
    print("text sample:", (d2["text"] or "")[:200].replace("\n", " "))

    ok = (d.get("had_transcript") or len(d["text"] or "") > 100) and \
         not d2.get("failed") and len(d2["text"] or "") > 500
    print("\nRESULT:", "PASS" if ok else "CHECK ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
