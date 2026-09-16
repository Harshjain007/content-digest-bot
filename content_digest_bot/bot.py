"""Telegram knowledge-keeper bot (long-polling — runs locally).

When you share a link/topic, the bot classifies it and:
  • TOOL  (article that links to GitHub, or a GitHub URL directly)
        -> summarize article + fetch GitHub README -> structured JSON:
           {title, description, problem, how this works, links}
  • LEARNING (upskilling / productivity / self-improvement article)
        -> structured JSON: {description, links, takeAways}
  • CONCEPT (a topic or other AI content)
        -> short summary, then "explain more?" -> full deep-dive (chat only)

All TOOL/LEARNING entries are de-duplicated against existing JSON and saved
to data/ (resources.json / learnings.json), mirrored into data.json for the
HTML viewer in site/.
"""
import asyncio
import logging
import secrets
import os
import re

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from .config import (ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN,
                     ANTHROPIC_MODEL)
from .extractors import (extract, classify, extract_document,
                          DOC_SUFFIXES, URL_RE)
from .github_api import is_github_url, fetch_repo
from .synthesize import synthesize, synthesize_json
from .format_telegram import md_to_telegram_html, split_html, format_card
from .moderate import is_allowed, REJECT_MSG
from .errors import user_message
from .synthesize import used_fallback
from .store import add_resource, add_learning
from .prompts import build_tool_json_prompt, build_learning_json_prompt

# Private bot: only these Telegram chat ids may use it. Set ALLOWED_CHAT_IDS
# in .env — an empty set refuses everyone, which is the safe way to fail.

# Live knowledge register (GitHub Pages) — shown after every save so you can
# open it from anywhere.
PAGES_URL = "https://harshjain007.github.io/content-digest-bot/site/index.html"


NOT_AUTHORIZED = ("🔒 This bot is private. You are not authorized to use it.")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs every getUpdates poll (with the bot token in the URL) at INFO,
# which grows the log by megabytes a day and leaks the token to disk.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

HELP = (
    "I'm your AI knowledge-keeper. Share:\n"
    "• an article that links to a GitHub repo (or a GitHub URL) → I save a "
    "structured tool card (what it is, problem, how to use, links)\n"
    "• an upskilling / productivity article → I save a learning card "
    "(what it solves, takeaways)\n"
    "• an AI concept / topic → I give a short summary; reply 'yes' to go deeper\n\n"
    "Everything is de-duplicated and saved locally as JSON for a web viewer."
)

EXPAND_WORDS = ("yes", "y", "more", "explain", "explain more", "details",
                "detail", "elaborate", "tell me more", "full", "deep", "go on")


def _is_expand(text):
    return any(w in text.strip().lower() for w in EXPAND_WORDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    await update.message.reply_text("👋 " + HELP)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    await update.message.reply_text(HELP)


async def _send_html(update, text, prefix=""):
    for i, chunk in enumerate(split_html(md_to_telegram_html(text))):
        p = (f"<b>{prefix}</b>\n\n" if prefix and i == 0 else "")
        try:
            await update.message.reply_text(p + chunk, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            logger.warning("HTML send failed: %s", e)
            await update.message.reply_text(p + re.sub(r"<[^>]+>", "", chunk))


RETRY_PREFIX = "retry:"


class _ReplayUpdate:
    """Just enough of an Update for the existing handlers to reply into a chat.

    A callback query has no `.message` of its own in the sense the handlers
    expect, and rewriting every helper to take a chat id instead of an update
    would be a far bigger change than wrapping it once here.
    """

    def __init__(self, message):
        self.message = message
        self.effective_chat = message.chat


def _remember(context, payload):
    """Record what this request was, so a failure can offer to replay it."""
    context.user_data["retry_payload"] = payload


def _retry_markup(context):
    """A Retry button bound to the last input, or nothing to attach."""
    payload = context.user_data.get("retry_payload")
    if not payload:
        return None
    token = RETRY_PREFIX + secrets.token_urlsafe(8)
    context.user_data[token] = payload
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Retry", callback_data=token)]])


async def _fail(context, text, status=None, message=None):
    """Report a failure with a Retry button attached."""
    markup = _retry_markup(context)
    try:
        if status is not None:
            await status.edit_text(text, reply_markup=markup)
        else:
            await message.reply_text(text, reply_markup=markup)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not send failure notice: %s", e)


async def retry_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Replay the request behind a Retry button."""
    query = update.callback_query
    await query.answer()
    if query.message.chat.id not in ALLOWED_CHAT_IDS:
        return
    payload = context.user_data.pop(query.data, None)
    if not payload:
        await query.edit_message_text(
            "⌛ That retry has expired — send it again and I'll pick it up.")
        return
    await query.edit_message_text("🔄 Retrying…")
    replay = _ReplayUpdate(query.message)
    try:
        if payload["kind"] == "text":
            await _process_text(replay, context, payload["text"])
        else:
            await _process_document(replay, context, payload["file_id"],
                                    payload["file_name"])
    except Exception as e:  # noqa: BLE001
        logger.exception("retry failed")
        await _fail(context, f"❌ {user_message(e)}", message=query.message)


async def _model_call(fn, *args, **kwargs):
    """Run a blocking model call without freezing the bot.

    Generation takes minutes on a local model, and it ran on the event loop:
    every other chat, /help included, was stuck behind it, and the status
    message couldn't even be updated.

    to_thread runs the call in a *copy* of the context, so used_fallback set
    inside the thread never reaches this one — read it there and carry it back.
    """
    def run():
        return fn(*args, **kwargs), used_fallback.get()

    result, fell_back = await asyncio.to_thread(run)
    used_fallback.set(fell_back)
    return result


async def _send_card(update, entry, kind, added, reason):
    """Send one formatted card for a filed entry.

    This used to be three messages — a one-line ack, a raw JSON dump of the
    entry, and a bare register URL.
    """
    html = format_card(entry, kind, saved=added, reason=reason,
                       register_url=PAGES_URL, note=_provider_note())
    for chunk in split_html(html):
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("card send failed: %s", e)
            await update.message.reply_text(re.sub(r"<[^>]+>", "", chunk))


# ----------------------------------------------------------------- handlers
def _normalize(text):
    """Strip the @url: wrapper, backticks and markdown fences users paste."""
    t = text.strip()
    # remove leading @url: / url: markers (case-insensitive)
    m = re.match(r"@?url:\s*", t, re.IGNORECASE)
    if m:
        t = t[m.end():]
    # strip code fences / backticks
    t = t.replace("```", "").replace("`", "")
    return t.strip()


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text
    text = _normalize(raw_text)
    chat_id = update.effective_chat.id
    logger.info("Incoming from chat_id=%s: %s", chat_id, raw_text[:80])

    # Private bot guard.
    if chat_id not in ALLOWED_CHAT_IDS:
        logger.warning("Unauthorized chat %s blocked", chat_id)
        await update.message.reply_text(NOT_AUTHORIZED)
        return

    _remember(context, {"kind": "text", "text": text})
    await _process_text(update, context, text)


async def _process_text(update, context, text):
    """The pipeline for a text message, replayable by the Retry button."""
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, "typing")

    # Explain-more follow-up for a pending CONCEPT.
    pending = context.user_data.get("pending")
    if pending and _is_expand(text):
        try:
            full = await _model_call(synthesize, pending,
                                     user_note=pending.get("user_note"),
                                     mode="full")
            if full is None:
                await update.message.reply_text("⚠️ Nothing to explain.")
                return
            await _send_html(update, full)
            context.user_data.pop("pending", None)
        except Exception as e:  # noqa: BLE001
            logger.exception("expand error")
            await _fail(context, f"❌ {user_message(e)}",
                        message=update.message)
        return

    # Topic gate only applies to bare topics (no link). Any shared URL —
    # GitHub repo, article, PDF, YouTube — is an explicit "file this" intent
    # and always passes through.
    has_url = bool(URL_RE.search(text))
    if not has_url and not is_allowed(text):
        logger.info("Rejected off-topic: %s", text[:60])
        await update.message.reply_text(REJECT_MSG, parse_mode=ParseMode.HTML)
        return

    status = await update.message.reply_text("🔍 Working…")
    try:
        kind, url, rest = classify(text)

        # ---- TOOL flow: GitHub URL, or article -> GitHub ----
        if is_github_url(url):
            await _handle_tool(update, context, status, github_url=url,
                               article_url=None, article_text=None)
            return
        if kind == "article":
            data = extract(text)
            if data.get("failed"):
                # Some failures carry their own explanation (legacy .doc).
                await status.edit_text(
                    f"⚠️ {data['note']}" if data.get("note") else
                    "⚠️ Couldn't read that article. "
                    "Paste the text and I'll digest it.")
                return
            # PDFs / Word docs: full descriptive summary, saved as a resource card.
            if data.get("is_pdf") or data.get("is_doc"):
                await _handle_pdf(update, context, status, data)
                return
            gh = _find_github_link(data.get("text") or "")
            if gh:
                await _handle_tool(update, context, status, github_url=gh,
                                   article_url=url,
                                   article_text=data.get("text"))
            else:
                # No GitHub link -> is it a learning article?
                if _looks_like_learning(data.get("text") or ""):
                    await _handle_learning(update, context, status,
                                           article_text=data.get("text"),
                                           article_url=url)
                else:
                    # Generic AI article: just summarize in chat.
                    await status.delete()
                    summary = await _model_call(synthesize, data, mode="summary")
                    await _send_html(update, summary,
                                     prefix="Want me to explain more? Reply 'yes'.")
                    context.user_data["pending"] = data
            return

        # ---- CONCEPT / topic / youtube / instagram ----
        data = extract(text)
        if data.get("needs_caption"):
            await status.edit_text("⚠️ Couldn't read this Instagram reel. "
                                   "Paste the caption and I'll digest it.")
            return
        if data.get("failed"):
            await status.edit_text("⚠️ Couldn't read that. Paste the text.")
            return
        context.user_data["pending"] = data
        await status.delete()
        summary = await _model_call(synthesize, data, mode="summary")
        await _send_html(update, summary,
                         prefix="Want me to explain more? Reply 'yes'.")
    except Exception as e:  # noqa: BLE001
        logger.exception("handle error")
        await _fail(context, f"❌ {user_message(e)}", status=status)


def _provider_note():
    """Say so when a request had to use the paid provider, not the local one."""
    return ("\n\n⚡ Ollama was unreachable, so this one ran on Anthropic."
            if used_fallback.get() else "")


async def _handle_tool(update, context, status, github_url, article_url,
                       article_text):
    repo = fetch_repo(github_url)
    gh_text = repo.get("readme") if repo else ""
    website = repo.get("homepage") if repo else ""
    if repo:
        github_url = repo.get("html_url") or github_url
    prompt = build_tool_json_prompt(article_text, gh_text, article_url,
                                    github_url, website)
    try:
        entry = await _model_call(synthesize_json, prompt, num_predict=1200)
    except Exception as e:  # noqa: BLE001
        logger.exception("tool json failed")
        await _fail(context,
                    f"❌ Couldn't build the tool card. {user_message(e)}",
                    status=status)
        return
    # fill links that the model may have missed
    entry.setdefault("links", {})
    entry["links"]["github"] = entry["links"].get("github") or (github_url or "")
    entry["links"]["article"] = entry["links"].get("article") or (article_url or "")
    entry["links"]["website"] = entry["links"].get("website") or (website or "")

    added, reason = add_resource(entry)
    await status.delete()
    await _send_card(update, entry, "tool", added, reason)


async def _handle_pdf(update, context, status, data):
    """PDF / Word doc: full descriptive summary, saved as a resource card.

    The user wants a complete, readable explanation they can revisit on the
    web page — so we generate the full deep-dive (not the short summary) and
    persist it as a resource.
    """
    kind = {".pdf": "PDF", ".docx": "Word document", ".pptx": "PowerPoint deck",
            ".xlsx": "spreadsheet", ".csv": "spreadsheet",
            ".html": "page", ".htm": "page"}.get(data.get("suffix"), "document")
    if data.get("note"):  # e.g. legacy .doc not supported
        await status.edit_text(f"⚠️ {data['note']}")
        return
    await status.edit_text(f"📄 Reading the {kind} and writing a full explanation…")
    try:
        summary = await _model_call(synthesize, data, mode="full",
                                    num_predict=4096)
    except Exception as e:  # noqa: BLE001
        logger.exception("doc summary failed")
        await _fail(context,
                    f"❌ Couldn't summarize the {kind}. {user_message(e)}",
                    status=status)
        return
    if not summary:
        await status.edit_text(f"⚠️ Couldn't read that {kind}. Paste the text.")
        return

    entry = {
        "title": data.get("title") or (data.get("url") or kind),
        "description": summary,
        "type": "doc" if data.get("is_doc") else "paper",
        "links": {"github": "", "website": "",
                  "article": data.get("url") or ""},
    }
    added, reason = add_resource(entry)
    await status.delete()
    await _send_card(update, entry, entry.get("type") or "doc", added, reason)
    await _send_html(update, summary)


async def _handle_learning(update, context, status, article_text, article_url):
    prompt = build_learning_json_prompt(article_text, article_url)
    try:
        entry = await _model_call(synthesize_json, prompt, num_predict=800)
    except Exception as e:  # noqa: BLE001
        logger.exception("learning json failed")
        await _fail(context,
                    f"❌ Couldn't build the learning card. {user_message(e)}",
                    status=status)
        return
    entry["links"] = entry.get("links") or article_url
    added, reason = add_learning(entry)
    await status.delete()
    await _send_card(update, entry, "learn", added, reason)


# ----------------------------------------------------------------- helpers
def _find_github_link(text):
    m = re.search(r"https?://github\.com/[\w.-]+/[\w.-]+", text or "")
    return m.group(0) if m else None


def _looks_like_learning(text):
    """Heuristic: upskilling / productivity / self-improvement article."""
    keys = ("productiv", "upskill", "improve", "habit", "career", "learn",
            "routine", "focus", "suggest", "tip", "advice", "growth",
            "well-being", "wellbeing", "mental", "burnout")
    t = (text or "").lower()
    return sum(k in t for k in keys) >= 2


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a document sent as a Telegram attachment.

    Goes through the same MarkItDown converter as a document URL, so an
    attachment and a link to the same file produce identical Markdown.
    """
    if update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text(NOT_AUTHORIZED)
        return

    doc = update.message.document
    fname = (doc.file_name or "")
    suffix = os.path.splitext(fname)[1].lower()
    if suffix not in DOC_SUFFIXES:
        await update.message.reply_text(
            "⚠️ I can read " + ", ".join(DOC_SUFFIXES) +
            ". Re-send as one of those.")
        return

    # file_id stays valid across retries, so a replay needs no re-upload.
    _remember(context, {"kind": "doc", "file_id": doc.file_id,
                        "file_name": fname})
    await _process_document(update, context, doc.file_id, fname)


async def _process_document(update, context, file_id, fname):
    """Convert and file a document, replayable by the Retry button."""
    suffix = os.path.splitext(fname)[1].lower()
    status = await update.message.reply_text("📎 Reading file…")
    try:
        tf = await context.bot.get_file(file_id)
        raw = bytes(await tf.download_as_bytearray())
        # Conversion is synchronous and pdfminer is slow on big PDFs; run it
        # off the event loop so polling and other chats keep being served.
        data = await asyncio.to_thread(extract_document, raw, suffix)
    except Exception as e:  # noqa: BLE001
        logger.exception("document handling failed")
        await _fail(context, f"❌ {user_message(e, 'Couldn\'t read that file.')}",
                    status=status)
        return

    if data.get("failed"):
        await _fail(context, "⚠️ Couldn't read that file. Paste the text, "
                             "or retry.", status=status)
        return
    data["title"] = data.get("title") or fname or "Uploaded file"
    await _handle_pdf(update, context, status, data)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: set TELEGRAM_BOT_TOKEN in your .env first.")
        return
    if not ALLOWED_CHAT_IDS:
        print("ERROR: set ALLOWED_CHAT_IDS in your .env (your Telegram chat id),"
              " otherwise the bot refuses everyone.")
        return
    # Ensure the site data files reflect current storage on startup.
    try:
        from content_digest_bot.store import _regen
        _regen()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not regenerate site data: %s", e)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(retry_cb, pattern=f"^{RETRY_PREFIX}"))
    print(f"Bot running (model={ANTHROPIC_MODEL})…  Ctrl+C to stop.")
    # launchd restarts the process on crash; a manual retry loop here can spawn
    # overlapping pollers (double getUpdates → 409), so just run once.
    app.run_polling()


if __name__ == "__main__":
    main()
