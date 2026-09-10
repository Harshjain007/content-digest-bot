"""Local JSON knowledge store with dedup + site data generation.

Data lives in data/:
  data/resources.json   - tool/repo entries
  data/learnings.json    - upskilling/article entries
  data/data.json         - combined, for the HTML viewer (site/index.html)

Dedup: a lightweight local check (no extra API). We compute a keyword/title
signature and skip adding an entry if a stored one is too similar.
"""
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
RESOURCES = os.path.join(DATA_DIR, "resources.json")
LEARNINGS = os.path.join(DATA_DIR, "learnings.json")
COMBINED = os.path.join(DATA_DIR, "data.json")
COMBINED_JS = os.path.join(DATA_DIR, "data.js")
SITE_DIR = os.path.join(REPO_ROOT, "site")
SITE_HTML = os.path.join(SITE_DIR, "index.html")
SITE_TEMPLATE = os.path.join(SITE_DIR, "template.html")

os.makedirs(DATA_DIR, exist_ok=True)


def _load(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def _save(path, items):
    with open(path, "w") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def _keywords(text):
    text = (text or "").lower()
    # strip punctuation, keep words >=4 chars, drop very common ones
    stop = {"this", "that", "with", "from", "your", "what", "when", "have",
            "will", "they", "their", "about", "which", "those", "these", "into",
            "than", "then", "them", "are", "was", "were", "been", "being"}
    words = re.findall(r"[a-z0-9_+#.-]{4,}", text)
    return {w for w in words if w not in stop}


def _similarity(a, b):
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def _is_duplicate(new_entry, existing, fields=("title", "description", "how this works", "takeAways")):
    """Return True if new_entry is too similar to an existing one."""
    for ex in existing:
        # `default` matters: two entries can share no comparable field at all
        # (a learning card against a paper card), and max() over an empty
        # generator raises instead of scoring zero.
        score = max((_similarity(new_entry.get(f, ""), ex.get(f, ""))
                     for f in fields if new_entry.get(f) and ex.get(f)),
                    default=0.0)
        if score >= 0.55:
            return True
    return False


def norm_links(entry):
    """An entry's links as a dict, whatever shape the model wrote.

    The learning prompt yields a bare URL string about half the time and a
    dict the rest, which left every reader downstream handling both. Normalize
    once, here, so nothing else has to.
    """
    links = entry.get("links")
    if isinstance(links, str):
        return {"article": links}
    return links if isinstance(links, dict) else {}


def _canon_url(url):
    """Canonical form of a URL for comparison — same page, same key.

    Ignores scheme, `www.`, a trailing slash and a trailing `.git`, so
    `http://GitHub.com/a/b.git` and `https://github.com/a/b/` collapse to one.
    """
    u = (url or "").strip().lower()
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    return re.sub(r"\.git$", "", u)


def _entry_urls(entry):
    """Every canonical URL an entry points at."""
    return {c for c in (_canon_url(v) for v in norm_links(entry).values()) if c}


def _all_entries():
    """Both stores together. An entry is a duplicate of anything already
    filed, not just of things filed into the same lane."""
    return _load(RESOURCES) + _load(LEARNINGS)


def duplicate_reason(entry, existing=None):
    """Why `entry` is already in the knowledge base, or None if it is new.

    Runs before every save, so the same link filed twice never creates a
    second card. Three passes, cheapest first:
      1. a shared link, checked across BOTH stores — a repo filed as a tool
         must not come back later as a learning
      2. an identical title
      3. keyword overlap across the substantive fields
    """
    if existing is None:
        existing = _all_entries()
    urls = _entry_urls(entry)
    title = (entry.get("title") or "").strip().lower()
    for ex in existing:
        if urls and urls & _entry_urls(ex):
            return "already filed under this link"
        if title and title == (ex.get("title") or "").strip().lower():
            return "already have an entry with this title"
    if _is_duplicate(entry, existing):
        return "too similar to an existing entry"
    return None


def add_resource(entry):
    """Add a tool/repo entry. Returns (added: bool, reason: str)."""
    entry["links"] = norm_links(entry)
    reason = duplicate_reason(entry)
    if reason:
        return False, reason
    entry["_added"] = datetime.now(timezone.utc).isoformat()
    items = _load(RESOURCES)
    items.append(entry)
    _save(RESOURCES, items)
    _regen(f"resource: {entry.get('title') or entry['links'].get('github') or 'entry'}")
    return True, "added"


def add_learning(entry):
    """Add a learning/article entry. Returns (added: bool, reason: str)."""
    # Strip any spurious "type" the LLM may have injected — learning
    # cards must be classified as "learn" by kindOf(), never "paper".
    entry.pop("type", None)
    entry["links"] = norm_links(entry)
    reason = duplicate_reason(entry)
    if reason:
        return False, reason
    entry["_added"] = datetime.now(timezone.utc).isoformat()
    items = _load(LEARNINGS)
    items.append(entry)
    _save(LEARNINGS, items)
    title = entry.get("title") or (entry.get("description") or "")
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)[:60]
    elif not isinstance(title, str):
        title = str(title)[:60]
    _regen(f"learning: {title}")
    return True, "added"


def _render_site(items):
    """Write site/index.html with the data embedded, so opening the file
    straight off disk always shows the latest entries (file:// blocks fetch).

    The template (site/template.html) carries a `window.REGISTER_DATA =
    __REGISTER_DATA__;` placeholder that we fill with the live JSON. We
    also inject the mdfmt() markdown formatter inline so the page can
    render bullet lists, bold, headings, and @url: links properly.
    """
    if not os.path.exists(SITE_TEMPLATE):
        return
    mdfmt_js = Path(__file__).resolve().parent.parent / "site" / "mdfmt.js"
    mdfmt_src = mdfmt_js.read_text(encoding="utf-8") if mdfmt_js.exists() else ""
    with open(SITE_TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps(items, ensure_ascii=False)
    html = tpl.replace("__REGISTER_DATA__", payload)
    html = html.replace("<!--MDFMT-->", f"<script>\n{mdfmt_src}\n</script>")
    with open(SITE_HTML, "w", encoding="utf-8") as f:
        f.write(html)


def _regen(commit_msg=None):
    """Write the combined data the site reads.

    Three artifacts, all from the same data:
      data.json - fetched by the page when served over HTTP (live updates)
      data.js   - same array as a global; legacy fallback for file://
      site/index.html - the page with data EMBEDDED, so opening it off disk
                  always shows the latest entries with no fetch/CORS issues

    commit_msg: used as the gh-pages commit message when provided.
    """
    items = _load(RESOURCES) + _load(LEARNINGS)
    _save(COMBINED, items)
    payload = json.dumps(items, indent=2, ensure_ascii=False)
    with open(COMBINED_JS, "w") as f:
        f.write("// Generated by store.py — do not edit by hand.\n"
                f"window.REGISTER_DATA = {payload};\n")
    _render_site(items)
    _publish_to_pages(commit_msg or "update knowledge register")


# Set by add_resource/add_learning so _regen can write a meaningful commit msg.
_last_added_title = None


def _run_git(args, cwd):
    """Run a git command, return (ok, output). Never raises."""
    try:
        res = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
        return res.returncode == 0, (res.stdout + res.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _publish_to_pages(commit_msg="update knowledge register"):
    """Commit site/ + data/ and push them to the gh-pages branch so the
    GitHub Pages site reflects the latest save. Best-effort: failures are
    logged but never break the bot's main flow.
    """
    if os.getenv("CDB_NO_PUBLISH"):
        return
    if not os.path.isdir(os.path.join(REPO_ROOT, ".git")):
        return
    ok, out = _run_git(["add", "site", "data"], REPO_ROOT)
    if not ok:
        logger.warning("Pages publish: git add failed: %s", out)
        return
    # Only commit if there is something staged.
    ok, _ = _run_git(["diff", "--cached", "--quiet"], REPO_ROOT)
    if ok:  # nothing staged -> nothing changed
        return
    msg = f"add: {commit_msg}" if commit_msg != "update knowledge register" \
        else "publish: update knowledge register"
    ok, out = _run_git(["commit", "-m", msg], REPO_ROOT)
    if not ok:
        logger.warning("Pages publish: commit failed: %s", out)
        return
    ok, out = _run_git(
        ["push", "origin", "HEAD:refs/heads/gh-pages"], REPO_ROOT)
    if not ok:
        logger.warning("Pages publish: push failed: %s", out)
        return
    logger.info("Published site to GitHub Pages (gh-pages).")


def dedupe(dry_run=False):
    """Rebuild both stores keeping only the first copy of each entry.

    The dedup checks run on save, so a store only carries duplicates that
    predate them. Run this once after changing the rules:
        python -m content_digest_bot.store --dedupe
    Returns the list of (store, title, reason) that were dropped.
    """
    dropped = []
    for path in (RESOURCES, LEARNINGS):
        kept = []
        for entry in _load(path):
            entry["links"] = norm_links(entry)
            reason = duplicate_reason(entry, kept)
            if reason:
                dropped.append((os.path.basename(path),
                                entry.get("title") or "(untitled)", reason))
            else:
                kept.append(entry)
        if not dry_run:
            _save(path, kept)
    return dropped


if __name__ == "__main__":
    import sys
    if "--dedupe" in sys.argv:
        for store, title, reason in dedupe():
            print(f"  dropped [{store}] {title[:60]} — {reason}")
    _regen()
    print(f"Wrote {COMBINED}, {COMBINED_JS} and {SITE_HTML}.")
