"""Content extraction layer.

Each extractor returns a dict:
    {
        "source": "YouTube" | "Article" | "Instagram" | "Topic",
        "title":   str,
        "url":     str | None,
        "text":    str | None,   # the raw material Claude will digest
        # optional flags:
        "needs_caption": True,   # Instagram: ask user to paste caption
        "failed":        True,   # Article: extraction failed
        "user_note":     str,    # extra text the user typed alongside the link
    }
"""
import logging
import re

from .config import MAX_INPUT_CHARS

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def classify(text):
    """Return (kind, url_or_None, leftover_text)."""
    urls = URL_RE.findall(text.strip())
    if not urls:
        return ("topic", None, text.strip())
    url = urls[0]
    rest = text.replace(url, "").strip()
    if "youtu.be" in url or "youtube.com" in url:
        return ("youtube", url, rest)
    if "instagram.com" in url or "instagr.am" in url:
        return ("instagram", url, rest)
    return ("article", url, rest)


# ----------------------------------------------------------------- YouTube
def _youtube_id(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})", url)
    if m:
        return m.group(1)
    m = re.search(r"([\w-]{11})", url)
    return m.group(1) if m else None


def _yt_metadata(url):
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True,
                               "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title"), info.get("description")
    except Exception as e:  # noqa: BLE001
        logger.warning("yt-dlp metadata failed: %s", e)
        return None, None


def _yt_transcript(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(vid, languages=["en", "en-US", "en-GB"])
        except TypeError:
            fetched = api.fetch(vid)
        parts = [s.text for s in fetched]
        return " ".join(parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("Transcript failed for %s: %s", vid, e)
        return None


def extract_youtube(url, max_chars=MAX_INPUT_CHARS):
    vid = _youtube_id(url)
    title, description = _yt_metadata(url)
    transcript = _yt_transcript(vid) if vid else None
    text = (description or "") + "\n\n" + (transcript or "")
    text = text[:max_chars]
    return {
        "source": "YouTube",
        "title": title or (f"Video {vid}" if vid else url),
        "url": url,
        "text": text,
        "had_transcript": bool(transcript),
    }


# ----------------------------------------------------------------- Article
# Everything MarkItDown turns into Markdown for us. Kept in one place so the
# link route and the upload route accept exactly the same set.
DOC_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm")

# Content types worth trusting when a URL has no useful extension.
_CTYPE_SUFFIX = (
    ("application/pdf", ".pdf"),
    ("wordprocessingml", ".docx"),
    ("presentationml", ".pptx"),
    ("spreadsheetml", ".xlsx"),
    ("text/csv", ".csv"),
)


def _fetch_bytes(url, timeout=30):
    """Return (content_bytes, content_type) or (None, None)."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (compatible; content-digest-bot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout,
                         allow_redirects=True)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("Fetch failed for %s: %s", url, e)
        return None, None


def doc_suffix(url, content_type=None):
    """Which document type a URL points at, or None if it isn't one."""
    u = (url or "").lower().split("?")[0]
    for suf in DOC_SUFFIXES:
        if u.endswith(suf):
            return suf
    ct = (content_type or "").lower()
    for needle, suf in _CTYPE_SUFFIX:
        if needle in ct:
            return suf
    return None


def _pdf_title(raw):
    """A PDF's own /Title, which MarkItDown discards.

    pdfminer.six is already installed as MarkItDown's PDF backend, so reading
    the metadata costs no new dependency. Titles are often UTF-16, which is
    what decode_text handles.
    """
    import io
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
        from pdfminer.utils import decode_text
        for info in PDFDocument(PDFParser(io.BytesIO(raw))).info or []:
            title = info.get("Title")
            if isinstance(title, bytes):
                title = decode_text(title)
            title = (title or "").strip()
            if len(title) > 3:
                return title[:160]
    except Exception as e:  # noqa: BLE001
        logger.debug("No PDF title: %s", e)
    return None


def _md_title(markdown):
    """The document's own first heading — MarkItDown carries no title.

    pdfminer returns no metadata at all and the docx path goes through
    mammoth, which drops core properties, so the heading is the only title
    the document still has. Without this every card is titled with its URL,
    which also collapses title-based de-duplication.
    """
    for line in (markdown or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                return title[:160]
        elif line:
            break          # body text before any heading: there is no title
    return None


def to_markdown(raw, suffix, max_chars=MAX_INPUT_CHARS):
    """Convert a document's bytes to Markdown for the model.

    One converter for every document the bot sees, whether it arrived as a URL
    or as a Telegram attachment. MarkItDown keeps the structure the model
    actually needs — heading levels, real tables, lists — where the previous
    per-format parsing flattened all of it into anonymous paragraphs.

    Returns (markdown, title); title is None when the document has no heading.
    Raises on failure so callers can fall back to their own error message.
    """
    import io
    from markitdown import MarkItDown

    res = MarkItDown().convert_stream(io.BytesIO(raw), file_extension=suffix)
    text = (res.text_content or "").strip()
    if len(text) < 50:
        raise RuntimeError(f"too little text extracted from {suffix}")
    title = (getattr(res, "title", None) or "").strip()
    if not title and suffix == ".pdf":
        title = _pdf_title(raw)      # pdfminer drops this on the floor
    return text[:max_chars], title or _md_title(text)


def extract_document(raw, suffix, url=None, max_chars=MAX_INPUT_CHARS):
    """Build the extractor dict for an already-fetched document."""
    try:
        text, title = to_markdown(raw, suffix, max_chars)
    except Exception as e:  # noqa: BLE001
        logger.warning("Document extraction failed for %s (%s): %s",
                       url or "upload", suffix, e)
        return {"source": "Article", "title": "Document", "url": url,
                "text": None, "failed": True}
    return {"source": "Article", "title": title or url or "Document",
            "url": url, "text": text, "suffix": suffix,
            "is_pdf": suffix == ".pdf", "is_doc": suffix != ".pdf"}


def extract_article(url, max_chars=MAX_INPUT_CHARS):
    # Legacy .doc is a binary format nothing here reads.
    if url and url.lower().split("?")[0].endswith(".doc"):
        return {"source": "Article", "title": "Word doc (.doc)", "url": url,
                "text": None, "failed": True,
                "note": "Legacy .doc files aren't supported — please "
                        "re-save as .docx and reshare."}
    # One fetch: documents are converted from these bytes rather than
    # downloaded a second time by a per-format extractor.
    raw, ctype = _fetch_bytes(url)
    suffix = doc_suffix(url, ctype) if raw is not None else None
    if suffix and suffix not in (".html", ".htm"):
        return extract_document(raw, suffix, url, max_chars)
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise RuntimeError("Could not fetch URL")
        text = trafilatura.extract(downloaded, include_comments=False,
                                   include_tables=True)
        meta = trafilatura.extract_metadata(downloaded)
        title = meta.title if meta else None
        if not text or len(text) < 200:
            raise RuntimeError("No main text extracted")
        return {
            "source": "Article",
            "title": title or url,
            "url": url,
            "text": text[:max_chars],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Article extraction failed for %s: %s", url, e)
        return {"source": "Article", "title": "Article link", "url": url,
                "text": None, "failed": True}


# --------------------------------------------------------------- Instagram
def _ig_shortcode(url):
    m = re.search(r"instagram\.com/(?:reel|p|tv)/([\w-]+)", url)
    return m.group(1) if m else None


def extract_instagram(url, username=None, password=None, max_chars=MAX_INPUT_CHARS):
    try:
        import instaloader
        shortcode = _ig_shortcode(url)
        if not shortcode:
            raise RuntimeError("Could not parse Instagram shortcode")
        L = instaloader.Instaloader()
        if username and password:
            try:
                L.login(username, password)
            except Exception as e:  # noqa: BLE001
                logger.warning("IG login failed: %s", e)
        post = instaloader.Post.from_shortcode(L, shortcode)
        caption = post.caption or ""
        text = (f"Caption:\n{caption}\n\n"
                f"(Instagram post by @{post.owner_username or 'unknown'}; "
                f"likes ~{post.likes_count})")
        return {
            "source": "Instagram",
            "title": (caption.split("\n")[0][:80] if caption
                      else "Instagram reel/post"),
            "url": url,
            "text": text[:max_chars],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Instagram extraction failed for %s: %s", url, e)
        return {"source": "Instagram", "title": "Instagram link", "url": url,
                "text": None, "needs_caption": True}


# -------------------------------------------------------------------- Topic
def extract_topic(topic, max_chars=2000):
    return {"source": "Topic", "title": topic[:80], "url": None,
            "text": topic[:max_chars]}


# ----------------------------------------------------------------- dispatch
def extract(text):
    """Classify a raw message and return the appropriate content dict."""
    from .config import INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD  # local import
    kind, url, rest = classify(text)

    if kind == "topic":
        data = extract_topic(text)
    elif kind == "youtube":
        data = extract_youtube(url)
    elif kind == "instagram":
        data = extract_instagram(url, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    else:  # article
        data = extract_article(url)

    if rest:
        data["user_note"] = rest
    return data
