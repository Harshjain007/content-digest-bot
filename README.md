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

Tool and Learning entries are de-duplicated before saving, then mirrored into
`data/data.json` (and `data/data.js`) for the site.

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
| `ANTHROPIC_MODEL` | defaults to `claude-3-5-sonnet-latest` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | used when `LLM_PROVIDER=ollama` |
| `INSTAGRAM_USERNAME` / `PASSWORD` | optional; without them the bot asks you to paste a reel's caption |
| `MAX_INPUT_CHARS` | cap on extracted text sent to the model (default 30000) |

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

## The Register (the site)

Open `site/index.html` directly, or serve the repo root:

```bash
python3 -m http.server        # then visit localhost:8000/site/index.html
```

Both work. Opening the file straight off disk can't use `fetch` (browsers block
it on `file://`), so `store.py` also writes `data/data.js`, which the page falls
back to. When served over HTTP the page reads `data/data.json` instead, so it
always shows the latest entries.

To rebuild the site data by hand after editing the JSON:
```bash
python -m content_digest_bot.store
```

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
python -m tests.test_units           # offline: classify, dedup, formatting
python -m tests.test_site_security   # offline: the site's XSS defences
python -m tests.test_extractors      # hits the network (YouTube + Wikipedia)
```

## Limits

- YouTube auto-captions are imperfect; briefs note when content was thin.
- Some sites block scrapers; the bot will ask you to paste the text instead.
- Instagram without login credentials usually needs a pasted caption.
- The topic gate always calls Ollama, even when `LLM_PROVIDER=anthropic`. If
  Ollama isn't running the gate fails open and lets everything through — by
  design, so a stopped Ollama can't block the bot.
- Entries are written by a model, so the site treats every field and URL as
  untrusted: text is escaped and only `http(s)` links are rendered.
