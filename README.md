# Content Digest Bot

A private Telegram bot that turns links and topics into a searchable knowledge
base. Send it something; it works out what kind of thing it is, extracts the
real content, and files a structured card. The cards are published as a static
site — **The Register** in `site/`, live at
https://harshjain007.github.io/content-digest-bot/site/index.html

## What it files

| Kind | Trigger | Saved as |
|---|---|---|
| **Tool** | a GitHub URL, or an article that links to one | `data/resources.json` — title, description, problem it solves, how it works, links |
| **Learning** | an upskilling / productivity / career article | `data/learnings.json` — description, takeaways, link |
| **Concept** | a topic, YouTube video, or other AI content | not saved — replies with a summary in chat, then a full deep-dive if you reply "yes" |

### Documents

PDFs, Word, PowerPoint, Excel and CSV — sent as a link or as a Telegram
attachment, both routed through the same code — are converted to **Markdown** by
[MarkItDown](https://github.com/microsoft/markitdown) before the model sees
them, so heading levels, lists and tables survive as structure instead of being
flattened into anonymous paragraphs. One converter (`extractors.to_markdown`)
handles every document, whether it arrived as a URL or an upload, so both
routes produce identical input, and a link is fetched once rather than
downloaded again per format.

A document with no title of its own is named from its first Markdown heading
(MarkItDown exposes no metadata), falling back to the URL only when there is
no heading at all.

Note that Markdown conversion is about **fidelity, not token count** — for a
PDF it yields about the same volume of text as raw extraction. `MAX_INPUT_CHARS`
is what actually bounds what you spend per document.

### De-duplication

Nothing is filed twice. Before any save, a candidate entry is checked against
**both** stores together, cheapest test first:

1. **Shared link** — every URL on the entry is canonicalized (scheme, `www.`,
   a trailing `/` and a trailing `.git` are ignored, case folded), so
   `http://GitHub.com/a/b.git` and `https://github.com/a/b/` are one link.
   Checked across both stores, so a repo filed as a tool can't come back as a
   learning card.
2. **Identical title.**
3. **Keyword overlap** on the substantive fields, at 55% Jaccard similarity.

The bot replies with which of the three caught it. To clean duplicates that
predate these rules:

```bash
python -m content_digest_bot.store --dedupe
```

Saved entries are mirrored into `data/data.json` for the site.

## Layout

```
content_digest_bot/     the package
  bot.py                Telegram handlers and routing
  extractors.py         YouTube / article / Instagram / topic → text
  github_api.py         repo metadata + README via the GitHub REST API
  moderate.py           topic gate (is this on-topic?)
  prompts.py            LangChain prompt templates
  synthesize.py         LLM calls — Anthropic or local Ollama
  store.py              JSON store, de-duplication, site data
  format_telegram.py    Markdown → Telegram-safe HTML, message splitting
  config.py             environment / .env
  demo.py               run the pipeline without Telegram
data/                   the knowledge base (JSON)
notes/                  Markdown notes written by demo.py
site/index.html         The Register — the static viewer
tests/                  offline checks + a live extraction smoke test
```

## Setup

### 1. Create the bot
Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the
**HTTP API token**.

### 2. Install
```bash
cd content-digest-bot
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 3. Configure `.env`
| Variable | Notes |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `LLM_PROVIDER` | `anthropic` (default) or `ollama` for free local inference |
| `ANTHROPIC_API_KEY` | needed when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | defaults to `claude-sonnet-5`. Check `client.models.list()` if a model 404s — old ids like `claude-3-5-sonnet-latest` are no longer available. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | used when `LLM_PROVIDER=ollama` |
| `INSTAGRAM_USERNAME` / `PASSWORD` | optional; without them the bot asks you to paste a reel's caption |
| `MAX_INPUT_CHARS` | hard cap on the Markdown sent to the model, in characters (default 30000). This is the token dial — roughly 4 chars per token. |

`.env` holds live secrets and is git-ignored. Never commit it — `.env.example`
is the template to share.

### 4. Authorize yourself
The bot is private. `ALLOWED_CHAT_IDS` in `content_digest_bot/bot.py` lists the
Telegram chat IDs allowed to use it; everyone else is refused. Add your own ID
there before running.

### 5. Run

Use the launcher — it activates the venv, clears `PYTHONPATH`, kills any
already-running instance (so you never get a duplicate-process `409 Conflict`),
and starts exactly one bot:

```bash
./run.sh            # foreground — Ctrl+C to stop
./run.sh -d         # background (detached); logs to bot.log, tail -f bot.log
```

Manual equivalent (only if you know what you're doing):

```bash
source .venv/bin/activate
env -u PYTHONPATH python -m content_digest_bot.bot
```

It uses long-polling, so it works from anywhere — no public server or port
forwarding. **Never start two copies** — Telegram allows only one `getUpdates`
per bot, and a second instance will Conflict and kill both.

### 6. Stop

```bash
pkill -9 -f content_digest_bot.bot
```

### 7. Keep it running (macOS launchd)

A LaunchAgent starts the bot at login and restarts it if it dies, so the
register keeps filling without a terminal open:
`~/Library/LaunchAgents/com.harshjain.contentdigestbot.plist`

```bash
launchctl kickstart -k gui/$(id -u)/com.harshjain.contentdigestbot   # restart
launchctl bootout   gui/$(id -u)/com.harshjain.contentdigestbot      # stop
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.harshjain.contentdigestbot.plist
launchctl list | grep contentdigest                                  # pid / last exit
tail -f ~/Library/Logs/content-digest-bot/bot.err.log                # logs
```

Two things that are easy to get wrong:

- **Logs live outside the project** (`~/Library/Logs/content-digest-bot/`).
  The repo sits under `~/Documents`, and macOS privacy protection (TCC) blocks
  *launchd itself* from creating files there, so a `StandardOutPath` inside the
  project makes the job fail at load with exit code 78 and no output. The bot
  process can read and write the project fine — only launchd's own log
  creation is blocked. `./run.sh -d` still writes `bot.log` in the project for
  manual runs.
- **The agent runs `.venv/bin/python3` directly**, not `run.sh`. KeepAlive
  already guarantees a single instance, so run.sh's kill-and-relaunch is
  redundant there. The venv is built with `--copies` so that interpreter is a
  real binary rather than a symlink into the system framework.

If you move the project, three things must be redone: the paths in the plist,
the venv (`rm -rf .venv && python3 -m venv --copies .venv && .venv/bin/python
-m pip install -r requirements.txt` — a venv bakes in absolute paths), and a
reload of the agent.

## The Register (the site)

Open `site/index.html` directly, or serve the repo root:

```bash
python3 -m http.server        # then visit localhost:8000/site/index.html
```

Both work. Opening the file straight off disk can't use `fetch` (browsers block
it on `file://`), so `store.py` embeds the data directly into `site/index.html`
when it renders it. When served over HTTP the page reads `data/data.json`
instead, so it always shows the latest entries.

To rebuild the site data by hand after editing the JSON:
```bash
python -m content_digest_bot.store
```

`store.py` renders `site/index.html` from `site/template.html`, inlining
`site/mdfmt.js` and embedding the data. Edit the **template**, never
`index.html` — it is generated and overwritten on every save. Set
`CDB_NO_PUBLISH=1` to regenerate without pushing to `gh-pages`.

## GitHub Pages (live from anywhere)

The register is published automatically to GitHub Pages on **every new
addition**. When the bot files a card it commits `site/` + `data/` and pushes
them to the `gh-pages` branch with a descriptive message
(`add: resource: <title>` / `add: learning: <title>`). GitHub Pages then
redeploys within ~1 minute.

- **Live URL:** https://harshjain007.github.io/content-digest-bot/site/index.html
- Source branch: `gh-pages` (root). Managed by `store._publish_to_pages()`.
- The Mac must be on (and the bot running) for a new save to publish — Pages
  only updates when the bot pushes.
- The repo is **public** so the URL works without login. Make it private from
  the GitHub settings if you'd rather keep the register to yourself (note:
  private-repo Pages needs a Pro/Team plan).

To preview locally without waiting for the push:

```bash
python3 -m http.server        # then visit localhost:8000/site/index.html
```

## Running without Telegram

```bash
python -m content_digest_bot.demo "explain vector databases"
python -m content_digest_bot.demo "https://www.youtube.com/watch?v=aircAruvnKk"
```
Prints the brief and saves it to `notes/`.

## Tests

```bash
pip install -r requirements-dev.txt   # test-only deps (python-docx fixtures)
python -m tests.test_units           # offline: classify, dedup, docs, formatting
python -m tests.test_site_security   # offline: the site's XSS defences
node tests/test_mdfmt.cjs            # offline: the markdown renderer's escaping
python -m tests.test_extractors      # hits the network (YouTube + Wikipedia)
```

## Limits

- YouTube auto-captions are imperfect; briefs note when content was thin.
- Some sites block scrapers; the bot will ask you to paste the text instead.
- Instagram without login credentials usually needs a pasted caption.
- The topic gate always calls Ollama, even when `LLM_PROVIDER=anthropic`. If
  Ollama isn't running the gate fails open and lets everything through — by
  design, so a stopped Ollama can't block the bot.
- With `LLM_PROVIDER=ollama`, a call that can't reach the local server falls
  back to Anthropic when `ANTHROPIC_API_KEY` is set, so a sleeping daemon
  doesn't cost you the submission. The reply says when that happened, because
  the fallback is the paid one. With no key set, the Ollama error surfaces
  as-is instead.
- Failures retry. A transient cause — a timeout, a rate limit, an unreachable
  host — is retried automatically (backing off 1s then 2s) before you see
  anything. A permanent one is not: a revoked key, an exhausted balance or a
  model that doesn't exist fails identically the second time, so it surfaces
  straight away. Whatever survives that comes with a **🔄 Retry** button that
  replays the original link or file, so a failed digest never means re-sending
  it. Uploads replay by `file_id`, so nothing is uploaded twice.
- Errors shown in chat are only ever messages the bot wrote itself
  (`errors.BotError`); anything else is replaced with a generic line and the
  real exception goes to the log, so paths and request URLs stay out of
  Telegram.
- Entries are written by a model, so the site treats every field and URL as
  untrusted: text is escaped and only `http(s)` links are rendered.
