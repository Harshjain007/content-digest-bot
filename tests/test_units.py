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
    # Keep the run entirely inside tmpdir: without these the test would
    # rewrite the real site and push it to gh-pages.
    store.COMBINED_JS = os.path.join(tmpdir, "data.js")
    store.SITE_HTML = os.path.join(tmpdir, "index.html")
    store.SITE_TEMPLATE = os.path.join(tmpdir, "missing-template.html")
    store._publish_to_pages = lambda *a, **k: None

    entry = {"title": "LangChain", "description": "framework for LLM apps",
             "links": {"github": "https://github.com/langchain-ai/langchain"}}
    added, _ = store.add_resource(dict(entry))
    assert added

    # exactly the same card again -> rejected on the link
    added, reason = store.add_resource(dict(entry))
    assert not added and "link" in reason

    # same title, no link at all -> rejected on the title
    added, reason = store.add_resource(
        {"title": "LangChain", "description": "unrelated wording entirely"})
    assert not added and "title" in reason

    # same repo under a different title -> still rejected
    added, _ = store.add_resource({"title": "LangChain Core",
                                   "description": "totally unrelated words here",
                                   "links": entry["links"]})
    assert not added

    # the same repo dressed up differently: other scheme, www, .git, trailing
    # slash, different casing. All one link, so all rejected.
    for variant in ("http://github.com/langchain-ai/langchain",
                    "https://www.github.com/langchain-ai/langchain/",
                    "https://github.com/langchain-ai/langchain.git",
                    "https://GitHub.com/langchain-ai/LangChain"):
        added, reason = store.add_resource({
            "title": f"variant {variant}",
            "description": "words that share nothing with the first entry",
            "links": {"github": variant}})
        assert not added, f"{variant} should have been caught as a duplicate"
        assert "link" in reason

    # a learning card pointing at an already-filed link is a duplicate too,
    # even though it lands in the other store
    added, _ = store.add_learning({
        "description": "a write-up about the same repository",
        "links": "https://github.com/langchain-ai/langchain",
        "takeAways": ["it chains things"]})
    assert not added

    # genuinely different entry -> accepted
    added, _ = store.add_resource({
        "title": "Whisper",
        "description": "speech recognition model transcribing audio offline",
        "links": {"github": "https://github.com/openai/whisper"}})
    assert added

    # a learning card with a bare-string link is normalized to a dict on save
    added, _ = store.add_learning({
        "description": "how to run deliberate practice sessions each week",
        "links": "https://example.com/deep-work",
        "takeAways": ["block the calendar"]})
    assert added
    with open(store.LEARNINGS) as f:
        assert json.load(f)[0]["links"] == {"article": "https://example.com/deep-work"}

    # combined data.json is regenerated for the site
    with open(store.COMBINED) as f:
        assert len(json.load(f)) == 3


def test_dedupe_cleans_existing(tmpdir):
    """dedupe() collapses duplicates that predate the checks."""
    store.RESOURCES = os.path.join(tmpdir, "r2.json")
    store.LEARNINGS = os.path.join(tmpdir, "l2.json")
    store._save(store.RESOURCES, [
        {"title": "Flask", "description": "a micro web framework",
         "links": {"github": "https://github.com/pallets/flask"}},
        {"title": "Flask (again)", "description": "different words entirely",
         "links": {"github": "https://github.com/pallets/flask/"}},
        {"title": "Django", "description": "batteries included web framework",
         "links": {"github": "https://github.com/django/django"}},
    ])
    store._save(store.LEARNINGS, [])
    dropped = store.dedupe()
    assert len(dropped) == 1
    with open(store.RESOURCES) as f:
        kept = json.load(f)
    assert [e["title"] for e in kept] == ["Flask", "Django"]


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
