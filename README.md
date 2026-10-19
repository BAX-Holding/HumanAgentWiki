# HumanAgentWiki

> A local, private **"second brain" your AI agents can actually read** — semantic search + [MCP](https://modelcontextprotocol.io) over your own Markdown notes.

**Status: early WIP** — being extracted and generalized from a working private system. See [ROADMAP.md](ROADMAP.md).

---

## What it is

HumanAgentWiki turns *your own notes* (plain Markdown) into a shared, searchable knowledge base that any **MCP-compatible AI agent** can plug into. Storage, indexing and retrieval run **locally on your machine**. A connected agent may send retrieved passages to its model provider; configure its permissions and data policy accordingly.

## Why

- 🧠 **LLMs don't know your context.** HumanAgentWiki gives them retrieval over what *you* actually wrote and think (RAG), so answers are grounded in your knowledge — not generic web fluff, and far fewer hallucinations.
- 🔒 **Local & private.** PostgreSQL + [pgvector](https://github.com/pgvector/pgvector) on your own machine. No cloud upload.
- 🌍 **Multilingual.** Uses [BGE-M3](https://huggingface.co/BAAI/bge-m3) embeddings — excellent for Czech and 100+ languages, not just English.
- 🔌 **Agent-native.** Exposes an MCP server, so Claude and other MCP clients connect with zero glue code. Multiple agents share one brain.

## Install

One line:

```bash
curl -fsSL https://raw.githubusercontent.com/petrludwig-collab/HumanAgentWiki/main/install.sh | bash
```

Or clone and run the installer:

```bash
git clone https://github.com/petrludwig-collab/HumanAgentWiki.git
cd HumanAgentWiki && ./install.sh
```

It checks prerequisites, creates a virtualenv, installs dependencies, brings up PostgreSQL (Docker if available, otherwise a local instance), writes `.env`, and creates the schema. Then use the `./haw` launcher:

For updates, pull the reviewed version and re-run `./install.sh`. An existing
`.env` preserves your database, model, notes and web authentication. The installer
then stops before changing agents or services: restart **only this wiki** through
your supervisor when idle. Back up the database and configuration before upgrades.
A mismatched embedding dimension is a migration requiring explicit operator action;
the installer never deletes index tables to resolve it. `.env` values are literal
`KEY=value` data (no shell expansion or surrounding shell quotes).

```bash
./haw index     # index your notes (Markdown under ./notes)
./haw web       # web UI at http://127.0.0.1:8808
./haw serve     # MCP server for your agents
```

> Try it instantly with the bundled demo: `NOTES_DIR=./sample_notes ./haw index && ./haw web`

### Headless macOS (Homebrew, no GUI login)

Install `postgresql@17`, `pgvector` and a supported Python with Homebrew. Start your
user-owned database cluster with `pg_ctl` (see `brew info postgresql@17` for its
paths), add its `bin` directory to PATH, then run from this checkout:

```bash
HAW_DB=mywiki HAW_NOTES_DIR="$HOME/work/wiki" HAW_SEED_NOTES=N HAW_PREPARE_ONLY=1 ./install.sh
```

Preparation uses the normal installer but stops before configuring or restarting
any agent/service. It never requests `sudo -u postgres` on macOS. Index your notes
with `./haw index`. Prefetch the model with the read-only query `./haw search "warmup"`
(also works when the wiki is empty), then set `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` in `.env`. Keep `.env` mode 0600. Set `MCP_HOST=127.0.0.1`,
`WEB_HOST` to the intended private interface (or explicitly `0.0.0.0` for LAN + VPN),
and a strong `WEB_AUTH=user:password`.
The built-in web authentication is HTTP Basic; use an encrypted private network
or TLS reverse proxy. Do not expose unauthenticated Postgres on the network.

For headless boot persistence use both `@reboot` and a once-per-minute user cron
entry calling the absolute venv Python path, absolute `headless.py` path, and
`--pg-bin /absolute/postgres/bin --pg-data /absolute/cluster`. The keeper starts
PostgreSQL first, then MCP/web; it takes a process lock, checks PID identities,
and leaves running processes alone. Logs and PID files are in ignored `state/`.
It does not replace a health/selftest check or continuously index edited files:
after external Markdown edits run `./haw index`. Web edits index immediately.
Configure MCP in the intended agent only; restart that agent separately when safe.
On macOS with Application Firewall, the actual Python.app must be allowed for
inbound connections; testing loopback alone does not prove LAN/VPN access. If
changing Python versions, create a matching virtualenv (never reuse another
Python minor version's compiled dependencies).
For a deliberately empty wiki use `./haw selftest --allow-empty`; this only allows
zero chunks if there are also zero Markdown files, and still checks registry sync.

## How it works (plain English)

1. Your notes are **Markdown files**, linked like a wiki with `[[links]]`.
2. They get chunked and **embedded** — each idea gets a "meaning fingerprint" stored in pgvector.
3. Search finds things **by meaning, not keywords** — search *"how not to get dumber from AI"* and it finds your note about *"cognitive laziness"* even without those exact words.
4. **Incremental indexing** — only changed files get re-embedded (seconds, not minutes).
5. An **MCP server** (`brain_search` / `brain_get` / `brain_neighbors` / `brain_write`) lets your agents query and safely add notes live.

## Categories - no config files

You define your top-level **categories right in the app** (add/remove with +/–) — e.g. *Books, Podcast, Interviews, Notes* — and start saving into them. No YAML, no config editing. On first run you get **sample notes + a built-in guide** so you instantly see how it works.

## Quick start

```bash
# 1. Start PostgreSQL + pgvector
docker compose up -d

# 2. Create the virtualenv used by the ./haw launcher
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 3. Configure (copy and edit if needed)
cp .env.example .env

# 4. Create the schema
./haw init-db

# 5. Index your notes  (put Markdown in ./notes — folders are categories;
#    or try the bundled demo:)
NOTES_DIR=./sample_notes ./haw index

# 6. Search from the terminal…
NOTES_DIR=./sample_notes ./haw search "how do small habits compound"

# 7. …or run the MCP server for your agents
./haw serve

# 8. …or open the web UI (manage categories, write/link notes, search)
./haw web      # then open http://127.0.0.1:8808
```

Then point any MCP client at `http://127.0.0.1:8802/mcp`. For Claude Code:

```bash
claude mcp add --transport http humanagentwiki http://127.0.0.1:8802/mcp
```

Tools exposed: `brain_search`, `brain_get`, `brain_neighbors`.

## Web UI

`python cli.py web` opens a wiki-style interface (no build step, vanilla JS):
- **3D graph** — your notes as an interactive 3D graph (rotate / zoom / pan), nodes coloured by category, edges from `[[links]]`. Click a node to open it. This is the centerpiece.
- **Categories** — add/remove your top-level categories with +/- (left panel).
- **Note editor** — slides in on the right: pick a category, write, and link other notes with `[[...]]` (autocomplete). Saving writes a Markdown file, **commits it to git** (full history), and re-indexes it.
- **Search** — semantic search across everything.

## Development

```bash
pip install -r requirements-dev.txt
pytest        # unit tests for the pure helpers (no DB needed)
```

## On the roadmap

See [ROADMAP.md](ROADMAP.md).

## Agentsmon monitoring

An optional read-only service-card plugin checks authenticated web access, MCP,
PostgreSQL, indexed-note counts and the existing recovery keeper. It reuses
Agentsmon's uptime and 90-day history without changing its core. See
[monitoring/README.md](monitoring/README.md) for setup and evidence limits.

## License

[MIT](LICENSE)
