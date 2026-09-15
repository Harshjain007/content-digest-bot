"""Convert the bot's Markdown brief into Telegram-renderable HTML.

Telegram's MarkdownV2 is extremely fiddly (it requires escaping most
punctuation). HTML mode is far more forgiving and supports <b>, <i>, <code>,
<pre>, <a>, <blockquote>. So we keep the model emitting clean Markdown (nice
for the .md notes) and convert it here for chat rendering.

Supported Markdown subset (keep the model constrained to this):
  # / ## / ### header      -> <b>header</b>
  **bold**                  -> <b>bold</b>
  *italic*                  -> <i>italic</i>
  `code`                   -> <code>code</code>
  ```...``` fenced block   -> <pre><code>...</code></pre>
  - item / * item          -> • item   (bullets)
  [text](url)              -> <a href="url">text</a>
"""
import re

_INLINE_TAGS = ("b", "i", "code")  # these may span a chunk boundary


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(md_line):
    """Format inline Markdown on a single line (after code blocks are split out)."""
    s = _escape(md_line)
    # inline code first so its contents aren't further formatted
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", s)
    # links [text](url)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    # bold then italic
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    return s


def md_to_telegram_html(md):
    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # fenced code block
        if line.strip().startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            out.append("<pre><code>" + _escape("\n".join(buf)) + "</code></pre>")
            continue
        # headers
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            out.append("<b>" + _inline(m.group(2)) + "</b>")
            i += 1
            continue
        # bullet
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            out.append("• " + _inline(m.group(1)))
            i += 1
            continue
        # blank
        if not line.strip():
            out.append("")
            i += 1
            continue
        # normal line
        out.append(_inline(line))
        i += 1
    return "\n".join(out)


def split_html(html, limit=3800):
    """Split HTML into balanced chunks that Telegram will accept.

    We split on line breaks but re-close any inline tag that straddles a
    boundary and reopen it at the start of the next chunk.
    """
    if len(html) <= limit:
        return [html]
    lines = html.split("\n")
    chunks, cur, open_stack = [], "", []
    for line in lines:
        # detect opening/closing inline tags to track balance
        opens = re.findall(r"<(b|i|code)>", line)
        closes = re.findall(r"</(b|i|code)>", line)
        # naive: assume balanced within a line; track net across lines
        for t in opens:
            open_stack.append(t)
        for t in closes:
            if open_stack and open_stack[-1] == t:
                open_stack.pop()

        projected = (cur + "\n" + line) if cur else line
        if len(projected) > limit and cur:
            # close any open tags, store as a chunk
            closing = "".join(f"</{t}>" for t in reversed(open_stack))
            chunks.append(cur + closing)
            # next chunk reopens the still-open tags
            cur = "".join(f"<{t}>" for t in open_stack) + ("\n" + line if open_stack else line)
        else:
            cur = projected
    if cur:
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------- entry cards
def _safe_link(url):
    """Only real http(s) URLs become links; model output is not trusted."""
    u = str(url or "").strip()
    return u if u.startswith(("http://", "https://")) else ""


def _clip(text, limit):
    """Trim to a whole word, marking that it was cut."""
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + " …"


def _block(label, body, limit=600):
    """A labelled section, or nothing when there is no body worth showing."""
    body = _clip(body, limit)
    if len(body) < 2:
        return ""
    return f"\n\n<b>{_escape(label)}</b>\n{_escape(body)}"


def _steps(body, limit=700):
    """Render a numbered run as real lines instead of one paragraph.

    The model writes "1. Install … 2. Configure …" on a single line, which in
    chat becomes an unreadable wall.
    """
    text = " ".join(str(body or "").split())
    if len(text) < 2:
        return ""
    text = _clip(text, limit)
    parts = re.split(r"\s*(?=\b\d{1,2}[.)]\s)", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return "\n\n<b>How it works</b>\n" + "\n".join(
            f"　{_escape(p)}" for p in parts)
    return _block("How it works", text, limit)


def _links_line(links, register_url=None):
    """One row of links rather than a bare URL per line."""
    out = []
    for label, key in (("GitHub", "github"), ("Website", "website"),
                       ("Source", "article")):
        u = _safe_link((links or {}).get(key))
        if u:
            out.append(f'<a href="{u}">{label}</a>')
    if register_url:
        out.append(f'<a href="{register_url}">Register</a>')
    return "\n\n🔗 " + " · ".join(out) if out else ""


def format_card(entry, kind, saved=True, reason="", register_url=None,
                note=""):
    """One tidy Telegram message for a filed entry.

    Replaces the old output of three separate messages, one of which was a raw
    JSON dump of the entry.
    """
    heading = {"tool": "Tool", "learn": "Learning", "paper": "Paper",
               "doc": "Document"}.get(kind, "Note")
    head = (f"✅ <b>Filed · {heading}</b>" if saved
            else f"⚠️ <b>Already filed · {heading}</b>")
    if not saved and reason:
        head += f"\n<i>{_escape(reason)}</i>"

    title = _clip(entry.get("title"), 120)
    body = f"\n\n<b>{_escape(title)}</b>" if title else ""

    desc = _clip(entry.get("description"), 500)
    if desc:
        body += f"\n{_escape(desc)}"

    if kind == "tool":
        body += _block("Problem it solves", entry.get("problem"))
        body += _steps(entry.get("how this works"))
    elif kind == "learn":
        takeaways = entry.get("takeAways") or []
        if isinstance(takeaways, str):
            takeaways = [t for t in takeaways.split("\n") if t.strip()]
        items = [_clip(t, 220) for t in takeaways[:8] if str(t).strip()]
        if items:
            body += "\n\n<b>Takeaways</b>\n" + "\n".join(
                f"• {_escape(t)}" for t in items)

    links = entry.get("links")
    if isinstance(links, str):
        links = {"article": links}
    return head + body + _links_line(links, register_url) + (note or "")
