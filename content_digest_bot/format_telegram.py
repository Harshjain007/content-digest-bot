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


def _strip_tags(html):
    return re.sub(r"<[^>]+>", "", html)


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
