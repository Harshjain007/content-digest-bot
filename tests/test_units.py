"""Offline sanity checks for the pure logic — no network, no API keys.

Run:  python -m tests.test_units
"""
import json
import os
import tempfile

from content_digest_bot import store
from content_digest_bot.extractors import classify
from content_digest_bot.format_telegram import md_to_telegram_html, split_html
from content_digest_bot.github_api import is_github_url


def test_classify():
    assert classify("https://youtu.be/aircAruvnKk")[0] == "youtube"
    assert classify("https://www.youtube.com/watch?v=x")[0] == "youtube"
    assert classify("https://instagram.com/reel/ABC")[0] == "instagram"
    assert classify("https://example.com/post")[0] == "article"
    assert classify("what is RAG")[0] == "topic"
    # leftover text is preserved alongside the URL
    kind, url, rest = classify("https://example.com/p why does this matter")
    assert kind == "article" and rest == "why does this matter"


def test_is_github_url():
    assert is_github_url("https://github.com/psf/requests")
    assert not is_github_url("https://example.com")
    assert not is_github_url(None)


def test_markdown_to_html():
    html = md_to_telegram_html("## Title\n- **bold** item\n`code`")
    assert "<b>Title</b>" in html
    assert "• <b>bold</b> item" in html
    assert "<code>code</code>" in html
    # HTML in the source is escaped, not passed through to Telegram
    assert "&lt;script&gt;" in md_to_telegram_html("<script>alert(1)</script>")
    # links become anchors
    assert '<a href="https://x.com">t</a>' in md_to_telegram_html("[t](https://x.com)")


def test_split_html_balances_tags():
    md = "\n".join(f"<b>line {i} with some padding text</b>" for i in range(400))
    chunks = split_html(md, limit=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 700  # limit + reopened/closed tags
        assert c.count("<b>") == c.count("</b>")


def test_dedup_and_store(tmpdir):
    store.DATA_DIR = tmpdir
    store.RESOURCES = os.path.join(tmpdir, "resources.json")
    store.LEARNINGS = os.path.join(tmpdir, "learnings.json")
    store.COMBINED = os.path.join(tmpdir, "data.json")

    entry = {"title": "LangChain", "description": "framework for LLM apps",
             "links": {"github": "https://github.com/langchain-ai/langchain"}}
    added, _ = store.add_resource(dict(entry))
    assert added

    # same title -> rejected
    added, reason = store.add_resource(dict(entry))
    assert not added and "name" in reason

    # same repo under a different title -> still rejected
    added, _ = store.add_resource({"title": "LangChain Core",
                                   "description": "totally unrelated words here",
                                   "links": entry["links"]})
    assert not added

    # genuinely different entry -> accepted
    added, _ = store.add_resource({
        "title": "Whisper",
        "description": "speech recognition model transcribing audio offline",
        "links": {"github": "https://github.com/openai/whisper"}})
    assert added

    # combined data.json is regenerated for the site
    with open(store.COMBINED) as f:
        assert len(json.load(f)) == 2


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            fn(tmp) if fn.__code__.co_argcount else fn()
            print(f"  ok  {name}")
    print("\nRESULT: PASS")


if __name__ == "__main__":
    main()
