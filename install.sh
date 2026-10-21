#!/usr/bin/env bash
#
# HumanAgentWiki installer.
#
#   curl -fsSL https://raw.githubusercontent.com/petrludwig-collab/HumanAgentWiki/main/install.sh | bash
#
# Overridable via env: HAW_DIR (install path), HAW_REPO (git url), HAW_DB (db name),
# HAW_NOTES_DIR, HAW_SEED_NOTES=N, HAW_PREPARE_ONLY=1 (no agent/service changes).
set -euo pipefail

REPO="${HAW_REPO:-https://github.com/petrludwig-collab/HumanAgentWiki.git}"
DIR="${HAW_DIR:-$HOME/humanagentwiki}"
DB="${HAW_DB:-humanagentwiki}"
# Some shells (cron, minimal CI, non-login) don't export USER; without this, `set -u` aborts
# with a cryptic "USER: unbound variable" right on the DB-role path before any helpful message.
USER="${USER:-$(id -un)}"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; X=$'\033[0m'
else B=; G=; Y=; R=; C=; X=; fi
ok()   { printf "  ${G}OK${X} %s\n" "$*"; }
warn() { printf "  ${Y}!${X}  %s\n" "$*"; }
die()  { printf "  ${R}x  %s${X}\n" "$*" >&2; exit 1; }
step() { printf "\n${B}${C}==>${X} ${B}%s${X}\n" "$*"; }
# Prompt the user even under `curl | bash` (stdin is the pipe) by reading /dev/tty.
# No tty (CI / headless) -> return the default, so non-interactive installs still work.
ask() {  # $1=prompt  $2=default  -> echoes the answer
  local a=""
  if ( : </dev/tty ) 2>/dev/null; then printf "%s" "$1" >/dev/tty; IFS= read -r a </dev/tty || a=""; fi
  printf "%s" "${a:-$2}"
}
lc() { printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }
# Poll the MCP endpoint with a real `initialize` until it answers (HTTP 200) or the timeout ends.
wait_for_mcp() {  # $1=url  $2=timeout seconds
  local end=$(( $(date +%s) + ${2:-300} )) code
  printf "  waiting for the wiki MCP to answer (first start loads the model)"
  while [ "$(date +%s)" -lt "$end" ]; do
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' -X POST "$1" \
      -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"haw-install","version":"1"}}}' 2>/dev/null || true)"
    if [ "$code" = "200" ]; then printf " - ready\n"; return 0; fi
    printf "."; sleep 3
  done
  printf "\n"; return 1
}
load_env() {
  # .env stores literal KEY=value data, never shell code (including passwords).
  [ -f .env ] || return 0
  local line key
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    key="${line%%=*}"
    [[ "$line" = *=* && "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
    export "$line"
  done < .env
}
# End environment loader
write_env() {
  # Atomic literal replacement: passwords/paths can contain sed metacharacters.
  local key="$1" value="$2" line tmp found=0
  tmp="$(mktemp .env.XXXXXX)"
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" = "$key="* ]]; then
      printf '%s=%s\n' "$key" "$value" >> "$tmp"; found=1
    else printf '%s\n' "$line" >> "$tmp"; fi
  done < .env
  [ "$found" = 1 ] || printf '%s=%s\n' "$key" "$value" >> "$tmp"
  chmod 600 "$tmp"; mv "$tmp" .env
}
# End configuration writer
# Read a secret from /dev/tty without echoing it; show one '*' per character (with backspace).
ask_secret() {  # $1=prompt  -> echoes the typed secret
  local prompt="$1" pw="" ch
  ( : </dev/tty ) 2>/dev/null || { printf ""; return; }
  printf "%s" "$prompt" >/dev/tty
  while IFS= read -rsn1 ch </dev/tty; do
    [ -z "$ch" ] && break                                   # Enter -> done
    if [ "$ch" = $'\177' ] || [ "$ch" = $'\b' ]; then       # backspace
      [ -n "$pw" ] && { pw="${pw%?}"; printf '\b \b' >/dev/tty; }
    else
      pw="$pw$ch"; printf '*' >/dev/tty
    fi
  done
  printf '\n' >/dev/tty
  printf '%s' "$pw"
}

printf "${B}${C}"
cat <<'BANNER'
  ============================================================
   HumanAgentWiki
   Your second brain - local, private, agent-ready (MCP + RAG)
  ============================================================
BANNER
printf "${X}"

# 1) prerequisites ----------------------------------------------------------
step "Checking prerequisites"
command -v git >/dev/null || die "git is required"
ok "git"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  command -v "$c" >/dev/null || continue
  v=$("$c" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null) || continue
  set -- $v
  if [ "$1" = "3" ] && [ "$2" -ge 10 ]; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.10+ is required"
ok "python ($("$PY" --version 2>&1))"

# 2) fetch the code ---------------------------------------------------------
step "Fetching HumanAgentWiki"
if [ -f "./cli.py" ] && [ -f "./requirements.txt" ]; then
  DIR="$(pwd)"; ok "using current checkout: $DIR"
elif [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only -q; ok "updated $DIR"
else
  git clone -q "$REPO" "$DIR"; ok "cloned into $DIR"
fi
cd "$DIR"
EXISTING_CONFIG=0
if [ -f .env ]; then
  EXISTING_CONFIG=1
  load_env || die "Invalid .env: use literal KEY=value lines"
fi

# 3) python environment -----------------------------------------------------
step "Installing Python dependencies (first run downloads PyTorch - be patient)"
# Debian/Ubuntu ship Python without venv support (no ensurepip). Detect and fix it.
if ! "$PY" -c "import ensurepip" >/dev/null 2>&1; then
  pyver="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  warn "Python venv support (ensurepip) is missing - installing python${pyver}-venv (needs sudo)"
  if command -v apt-get >/dev/null; then
    sudo apt-get update -qq >/dev/null 2>&1 || true
    sudo apt-get install -y "python${pyver}-venv" >/dev/null 2>&1 || sudo apt-get install -y python3-venv >/dev/null 2>&1 || true
  fi
  "$PY" -c "import ensurepip" >/dev/null 2>&1 \
    || die "Could not enable venv. Run:  sudo apt install python${pyver}-venv   then re-run this installer."
  ok "venv support installed"
fi
# (Re)create the venv if it's missing or incomplete. A first run that failed before
# ensurepip was available leaves a venv with python but NO pip — detect that by probing
# pip itself (not just the python binary) and rebuild.
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv; "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
# PyTorch + deps are a large download, and pip can sit SILENT for minutes while it resolves
# dependencies before any bytes move — that looks frozen. Run pip in the background and show a
# live heartbeat (elapsed time + .venv size + what pip is currently fetching) so the user always
# sees movement, even during the quiet resolver phase.
warn "downloading PyTorch + dependencies — the big step, several minutes (live progress below)"
PIPLOG="$(mktemp)"
# Disable errexit/pipefail for the heartbeat: the grep below finds nothing early on (empty log),
# which under `set -o pipefail` makes the whole pipeline non-zero and would otherwise abort the
# script SILENTLY mid-install. pip's real exit code is captured explicitly via `wait` instead.
# `</dev/null` also matters under `curl | bash`: a backgrounded job otherwise inherits the
# script-pipe as stdin and drains it, making bash hit EOF and stop.
set +e +o pipefail
.venv/bin/python -m pip install --progress-bar on -r requirements.txt >"$PIPLOG" 2>&1 </dev/null &
pip_pid=$!
secs=0
while kill -0 "$pip_pid" 2>/dev/null; do
  cur="$(grep -aoE '(Collecting|Downloading|Using cached|Building|Installing|Preparing|Successfully)[[:print:]]*' "$PIPLOG" 2>/dev/null | tail -1)"
  dl="$(du -sh .venv 2>/dev/null | cut -f1)"
  printf '\r   %s⏳%s %4ds  .venv:%-6s  %-46.46s' "$C" "$X" "$secs" "${dl:-?}" "${cur:-resolving dependencies…}"
  sleep 5; secs=$((secs + 5))
done
wait "$pip_pid"; piprc=$?
set -e -o pipefail
printf '\r%-90s\r' ' '
if [ "$piprc" -ne 0 ]; then printf '\n'; tail -25 "$PIPLOG"; rm -f "$PIPLOG"; die "pip install failed (log above)"; fi
rm -f "$PIPLOG"
ok "dependencies installed"

# 4) database (Postgres + pgvector) -----------------------------------------
step "Setting up PostgreSQL (pgvector)"
DB_URL="${DATABASE_URL:-dbname=$DB}"
pg_ready() { command -v pg_isready >/dev/null 2>&1 && pg_isready -q 2>/dev/null; }
if [ -n "${DATABASE_URL:-}" ]; then
  ok "preserving the configured database connection"
elif command -v docker >/dev/null && docker info >/dev/null 2>&1 && [ -f docker-compose.yml ]; then
  docker compose up -d >/dev/null 2>&1 || sudo docker compose up -d >/dev/null 2>&1 || true
  DB_URL="dbname=humanagentwiki user=humanagentwiki password=humanagentwiki host=localhost port=5432"
  for _ in $(seq 1 30); do
    .venv/bin/python -c "import psycopg; psycopg.connect('$DB_URL').close()" 2>/dev/null && break; sleep 1
  done
  ok "Postgres running via docker compose (pgvector image)"
elif pg_ready; then
  ok "using the PostgreSQL already running on this machine"
elif command -v apt-get >/dev/null; then
  warn "No PostgreSQL found - installing PostgreSQL + pgvector (needs sudo)"
  sudo apt-get update -qq >/dev/null 2>&1 || true
  sudo apt-get install -y postgresql postgresql-contrib >/dev/null 2>&1 || warn "postgres install hit an issue"
  PGMAJ="$(ls /usr/lib/postgresql/ 2>/dev/null | sort -n | tail -1)"
  :   # pgvector is handled below, for every native install - not only fresh ones
  sudo systemctl enable --now postgresql >/dev/null 2>&1 || true
  ok "PostgreSQL installed"
else
  warn "No PostgreSQL, Docker, or apt - install PostgreSQL+pgvector manually, then re-run."
fi
# pgvector, for every native install - not just the one that installed Postgres itself.
# This used to live inside the "no PostgreSQL found" branch, so anyone who already had
# Postgres running (including anyone re-running after a failure) skipped it entirely and hit
# "could not open extension control file .../vector.control" at the schema step.
if [ "$DB_URL" = "dbname=$DB" ] && command -v apt-get >/dev/null; then
  PGMAJ="$(ls /usr/lib/postgresql/ 2>/dev/null | sort -n | tail -1)"
  if ! ls /usr/share/postgresql/*/extension/vector.control >/dev/null 2>&1; then
    warn "pgvector extension missing - installing it"
    install_pgvector() {
      sudo apt-get install -y "postgresql-${PGMAJ}-pgvector" >/dev/null 2>&1 \
        || sudo apt-get install -y postgresql-pgvector >/dev/null 2>&1
    }
    # Ubuntu only started shipping pgvector with 24.04, so on 22.04 - still the most common
    # server image - neither package exists. The extension IS published for every supported
    # release in PostgreSQL's own apt repository, so add that rather than sending people off
    # to install Docker.
    if ! install_pgvector; then
      warn "not in the distro repos - adding PostgreSQL's official apt repository"
      CODENAME="$(lsb_release -cs 2>/dev/null || { . /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}"; })"
      if [ -n "$CODENAME" ]; then
        sudo install -d /usr/share/postgresql-common/pgdg 2>/dev/null || true
        sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
          https://www.postgresql.org/media/keys/ACCC4CF8.asc >/dev/null 2>&1 || true
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
          | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null 2>&1 || true
        sudo apt-get update -qq >/dev/null 2>&1 || true
      fi
      install_pgvector || warn "pgvector still unavailable (alternative: install Docker and re-run)"
    fi
    ls /usr/share/postgresql/*/extension/vector.control >/dev/null 2>&1 \
      && ok "pgvector available" || warn "pgvector still missing - the schema step will fail"
  else
    ok "pgvector already available"
  fi
fi

# For a local/native Postgres: give this OS user a role + the database (peer auth on the socket).
if pg_ready && [ "$DB_URL" = "dbname=$DB" ]; then
  # Homebrew initdb creates the role for its unprivileged owner; there is no
  # Linux 'postgres' OS account and no reason to request elevated privileges.
  if [ "$(uname -s)" != "Darwin" ]; then
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$USER'" 2>/dev/null | grep -q 1 \
      || sudo -u postgres createuser -s "$USER" 2>/dev/null || true
  fi
  createdb "$DB" 2>/dev/null || true
  .venv/bin/python -c "import psycopg; psycopg.connect('dbname=$DB').close()" 2>/dev/null \
    && ok "database '$DB' ready" || warn "could not connect to '$DB' yet"
fi

# 5) configuration: DB url + RAM-adaptive embedding model -------------------
step "Writing configuration"
[ -f .env ] || cp .env.example .env
if [ "$EXISTING_CONFIG" = 0 ]; then
  write_env DATABASE_URL "$DB_URL"
fi
# Pick the embedding model by available RAM. BGE-M3 is excellent for Czech but needs ~2.3 GB to
# load (and serve + web each load a copy). On small servers fall back to a light multilingual
# model (still handles Czech + 100 languages) that loads in a fraction of the RAM.
RAM_MB=0
if [ "$(uname -s)" = "Darwin" ]; then
  RAM_MB="$(( $(sysctl -n hw.memsize) / 1024 / 1024 ))"
else
  RAM_MB="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
fi
RAM_MB="${RAM_MB:-0}"
if [ "${EXISTING_CONFIG:-0}" = 1 ] && [ -n "${EMBED_MODEL:-}" ] && [ -n "${EMBED_DIM:-}" ]; then
  EMB_MODEL="$EMBED_MODEL"; EMB_DIM="$EMBED_DIM"
  ok "preserving the configured embedding model and dimension"
elif [ "$RAM_MB" -lt 6000 ]; then
  EMB_MODEL="intfloat/multilingual-e5-small"; EMB_DIM="384"
  warn "small server (${RAM_MB} MB RAM) - using a lighter multilingual model (e5-small, 384d)"
else
  EMB_MODEL="BAAI/bge-m3"; EMB_DIM="1024"
  ok "using BGE-M3 (server has ${RAM_MB} MB RAM)"
fi
for kv in "EMBED_MODEL=$EMB_MODEL" "EMBED_DIM=$EMB_DIM"; do
  write_env "${kv%%=*}" "${kv#*=}"
done
ok "wrote .env"
chmod +x haw 2>/dev/null || true

# 5b) swap — safety net so the embedding model loads when free RAM is tight --
SWAP_KB=0
if [ "$(uname -s)" = "Linux" ]; then
  SWAP_KB="$(free -k 2>/dev/null | awk '/^Swap:/{print $2}')"
fi
SWAP_KB="${SWAP_KB:-0}"
if [ "$(uname -s)" = "Linux" ] && [ "$SWAP_KB" -eq 0 ] && [ "$RAM_MB" -lt 8000 ] && [ ! -e /swapfile ] && command -v sudo >/dev/null; then
  step "Adding 4 GB swap (safety for model loading on a small server)"
  if { sudo fallocate -l 4G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none 2>/dev/null; } \
     && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null 2>&1 && sudo swapon /swapfile 2>/dev/null; then
    grep -q '/swapfile' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    ok "4 GB swap active (survives reboot)"
  else warn "could not add swap (continuing anyway)"; fi
fi

# 6) schema -----------------------------------------------------------------
if command -v pg_isready >/dev/null && pg_isready -q 2>/dev/null \
   || { command -v docker >/dev/null && docker info >/dev/null 2>&1; }; then
  step "Creating database schema"
  load_env || die "Invalid .env"
  # A model migration must be explicit; never destroy an existing index on re-run.
  CUR_DIM="$(.venv/bin/python - <<'PY' 2>/dev/null
import os, psycopg
try:
    c = psycopg.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
    cur.execute("select a.atttypmod from pg_attribute a join pg_class c on a.attrelid=c.oid "
                "where c.relname='chunks' and a.attname='embedding'")
    r = cur.fetchone(); print(r[0] if r and r[0] > 0 else "")
except Exception:
    print("")
PY
)"
  if [ -n "$CUR_DIM" ] && [ "$CUR_DIM" != "${EMBED_DIM:-1024}" ]; then
    die "Embedding dimension mismatch ($CUR_DIM -> ${EMBED_DIM}). Restore the original model/dimension in .env or back up the database and perform an explicit migration. No tables were deleted."
  fi
  if .venv/bin/python cli.py init-db; then
    ok "schema ready"
  else
    die "Database schema failed — the pgvector 'vector' extension is not available.
       This install cannot work without it, so stopping here instead of finishing 'green'.
       Fix it one of these ways, then re-run ./install.sh:
         - Ubuntu/Debian: install pgvector from the official PGDG apt repo
           (https://wiki.postgresql.org/wiki/Apt), i.e. the postgresql-<ver>-pgvector package;
         - or install Docker and re-run — this script then uses the bundled pgvector image."
  fi
else
  die "No reachable PostgreSQL — cannot create the schema.
       Install PostgreSQL + pgvector (or Docker) and re-run ./install.sh."
fi

# 7) notes folder (empty, or seeded with bundled examples) ------------------
step "Notes folder"
NOTES_VAL="${HAW_NOTES_DIR:-${NOTES_DIR:-$DIR/notes}}"
mkdir -p "$NOTES_VAL"
seed_default=Y
[ "$EXISTING_CONFIG" = 0 ] || seed_default=N
seed="${HAW_SEED_NOTES:-$(ask "  Seed with bundled example notes? [y/n]: " "$seed_default")}"
if [ "$(lc "$seed")" != "n" ] && [ -d "$DIR/sample_notes" ]; then
  cp -R "$DIR/sample_notes/." "$NOTES_VAL/" 2>/dev/null || true
  ok "seeded example notes -> notes/"
else
  ok "empty notes/ folder (drop your Markdown here)"
fi
[ -d "$NOTES_VAL/.git" ] || git -C "$NOTES_VAL" init -q 2>/dev/null || true
write_env NOTES_DIR "$NOTES_VAL"

# 8) index the notes --------------------------------------------------------
if command -v pg_isready >/dev/null && pg_isready -q 2>/dev/null \
   || { command -v docker >/dev/null && docker info >/dev/null 2>&1; }; then
  step "Indexing notes (first run loads the embedding model - be patient)"
  load_env || die "Invalid .env"
  .venv/bin/python cli.py index || die "Indexing failed; check the error above before starting services"
  ok "notes indexed"
fi

# Headless preparation stops before wiring/restarting ANY agent or web service.
# Configure .env (bind addresses + auth) and an explicit supervisor afterwards.
if [ "${HAW_PREPARE_ONLY:-0}" = "1" ] || [ "$EXISTING_CONFIG" = 1 ]; then
  chmod 600 .env
  ok "Prepared $DIR; agents and services were not started or changed."
  ok "For an existing installation, restart only this wiki through its supervisor when safe."
  exit 0
fi

# 9) connect ALL this server's AI agents via MCP ----------------------------
step "Connect AI agents (MCP)"
load_env || die "Invalid .env"
MCP_PORT_VAL="${MCP_PORT:-8802}"
MCP_URL="http://127.0.0.1:${MCP_PORT_VAL}/mcp"
NAME="${HAW_MCP_NAME:-humanagentwiki}"
wire="$(ask "  Point ALL AI agents on this server (Claude / Codex / Hermes / OpenClaw) at the wiki? [Y/n]: " "Y")"
if [ "$(lc "$wire")" != "n" ]; then
  any=0
  if command -v claude >/dev/null; then
    claude mcp add -s user --transport http "$NAME" "$MCP_URL" >/dev/null 2>&1 \
      && { ok "Claude Code -> $NAME"; any=1; } || warn "Claude Code: skipped (maybe already set)"
  fi
  if command -v codex >/dev/null; then
    codex mcp add "$NAME" --url "$MCP_URL" >/dev/null 2>&1 || true   # idempotent; verify below
    if codex mcp list 2>/dev/null | grep -q "^$NAME\b\|[[:space:]]$NAME[[:space:]]"; then
      ok "Codex -> $NAME"; any=1
    else warn "Codex: couldn't add (try: codex mcp add $NAME --url $MCP_URL)"; fi
  fi
  # Hermes: its 'mcp add' prompts on the tty for an optional token (hangs / aborts when scripted).
  # Write the server straight into its config instead — non-interactive and persistent.
  if command -v hermes >/dev/null; then
    if hermes config set "mcp_servers.$NAME.url" "$MCP_URL" >/dev/null 2>&1 \
       && hermes config set "mcp_servers.$NAME.enabled" true >/dev/null 2>&1; then
      ok "Hermes -> $NAME"; any=1
    else warn "Hermes: couldn't set MCP (add manually: hermes mcp add $NAME --url $MCP_URL)"; fi
  fi
  # OpenClaw probes the URL before saving; the wiki MCP is FastMCP streamable-HTTP at /mcp,
  # so we MUST pass --transport streamable-http or the probe fails and nothing is saved.
  if command -v openclaw >/dev/null; then
    if openclaw mcp add "$NAME" --url "$MCP_URL" --transport streamable-http </dev/null >/dev/null 2>&1; then
      ok "OpenClaw -> $NAME"; any=1
    elif openclaw mcp list 2>/dev/null | grep -q "[[:space:]]$NAME$\|^- $NAME$"; then
      ok "OpenClaw -> $NAME (already set)"; any=1
    else
      warn "OpenClaw: couldn't add (try: openclaw mcp add $NAME --url $MCP_URL --transport streamable-http)"
    fi
  fi
  if [ "$any" = 1 ]; then
    ok "agents can use: brain_search / brain_get / brain_neighbors"
    # Running agents are reloaded after the MCP server is up and answering (step 12).
  else warn "no agent CLI found on PATH - register $MCP_URL manually in each agent"; fi
else
  warn "agents not wired - the MCP endpoint is $MCP_URL"
fi

# 10) network reach: localhost only, or visible from outside ----------------
step "Web UI access"
WEB_PORT_VAL="${WEB_PORT:-8808}"
echo "  The web UI is always reachable on this machine (http://127.0.0.1:$WEB_PORT_VAL)."
if [ "$EXISTING_CONFIG" = 1 ]; then
  WEB_HOST_VAL="${WEB_HOST:-127.0.0.1}"
  ok "preserving web bind and authentication from .env"
else
vis="$(ask "  Also make it visible from outside - the server's IP / the internet? [y/N]: " "N")"
if [ "$(lc "$vis")" = "y" ]; then WEB_HOST_VAL="0.0.0.0"; else WEB_HOST_VAL="127.0.0.1"; fi
write_env WEB_HOST "$WEB_HOST_VAL"
if [ "$WEB_HOST_VAL" = "0.0.0.0" ]; then warn "visible from outside - set a password below (strongly recommended)"
else ok "kept private (127.0.0.1 only)"; fi

# 11) HTTP auth (defaults to YES when the UI is visible from outside) --------
step "Web UI password (HTTP auth)"
warn "The web UI shows ALL your notes."
if [ "$WEB_HOST_VAL" = "0.0.0.0" ]; then want="$(ask "  Protect it with a username:password? [Y/n]: " "Y")"
else want="$(ask "  Protect it with a username:password? [y/N]: " "N")"; fi
if [ "$(lc "$want")" = "y" ]; then
  u="$(ask "    username: " "")"
  p="$(ask_secret "    password: ")"
  if [ -n "$u" ] && [ -n "$p" ]; then
    cred="$u:$p"
    write_env WEB_AUTH "$cred"
    ok "web UI protected (login: $u / password hidden)"
  else warn "username or password empty - left open"; fi
else
  # "no password" must really mean open: strip any WEB_AUTH left by a previous run.
  if grep -q '^WEB_AUTH=' .env 2>/dev/null; then
    tmp="$(mktemp)"; grep -v '^WEB_AUTH=' .env > "$tmp" && mv "$tmp" .env
  fi
  warn "web UI left open (no password)"
fi
fi  # Preserve existing web authentication on unattended re-runs.
chmod 600 .env 2>/dev/null || true

# 12) (re)start it so the CURRENT config (model, host, auth) actually applies -
step "Starting HumanAgentWiki"
# First installation only. Existing instances stop before this section. Never
# use broad process-name kills: another checkout may serve a different wiki.
# Re-load .env so the FINAL config (WEB_HOST / WEB_AUTH chosen by the prompts above) is in the
# environment the servers inherit. An earlier `set -a; . ./.env` baked stale defaults (notably
# WEB_HOST=127.0.0.1) into this shell; since ./haw only sets vars NOT already in the environment,
# that stale value would otherwise shadow the updated file and the web would bind to localhost
# even when the user chose "visible from outside".
load_env || die "Invalid .env"
mkdir -p state
nohup .venv/bin/python "$DIR/cli.py" serve >state/serve.log 2>&1 </dev/null &
serve_pid=$!; printf '%s\n' "$serve_pid" >state/serve.pid
nohup .venv/bin/python "$DIR/cli.py" web >state/web.log 2>&1 </dev/null &
web_pid=$!; printf '%s\n' "$web_pid" >state/web.pid
sleep 3
if kill -0 "$serve_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; then
  ok "MCP/web processes started; model warmup may take longer (logs: $DIR/state/)"
else
  die "A wiki process exited. Check $DIR/state/serve.log and web.log; no other instance was stopped."
fi
# Reload running agents so they pick up the wiki MCP now (they only read MCP at startup).
if [ "${any:-0}" = 1 ]; then
  step "Reloading running agents so they see the wiki"
  # Gateways: clean restart. setsid detaches from this terminal (no foreground hijack) and from
  # this script's process group (survives our exit); a supervisor reconciles to one instance.
  if command -v setsid >/dev/null 2>&1; then
    command -v hermes   >/dev/null 2>&1 && { setsid hermes gateway restart   </dev/null >/dev/null 2>&1 & ok "Hermes gateway reloaded"; }
    command -v openclaw >/dev/null 2>&1 && { setsid openclaw gateway restart </dev/null >/dev/null 2>&1 & ok "OpenClaw gateway reloaded"; }
  fi
  # Codex/Claude have no hot-reload and no gateway. A blind kill took an agent down with no
  # respawn, so only a supervisor that owns them restarts them: Agents Monitoring (0.3.11+)
  # resumes the same conversation. An agent reads its MCP list once at start, so wait until the
  # wiki MCP actually answers (first start downloads the model) - an earlier restart sees nothing.
  if command -v agentsmon >/dev/null 2>&1 && agentsmon restart --help >/dev/null 2>&1 \
     && { command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1; }; then
    reload="$(ask "  Restart running Codex / Claude agents so they load the wiki (same conversation)? [Y/n]: " "Y")"
    if [ "$(lc "$reload")" != "n" ]; then
      if wait_for_mcp "$MCP_URL" "${HAW_MCP_WAIT:-300}"; then
        if agentsmon restart --all </dev/null; then ok "running agents restarted - the wiki is available to them"
        else warn "some agents did not restart - use the restart button in the Agents Monitoring dashboard"; fi
      else
        warn "MCP not answering yet (see $DIR/state/serve.log) - restart Codex/Claude once it does: agentsmon restart --all"
      fi
    else
      warn "Codex/Claude: restart them later to load the wiki: agentsmon restart --all"
    fi
  else
    command -v codex  >/dev/null 2>&1 && warn "Codex: restart its session to load the wiki (new sessions already have it)"
    command -v claude >/dev/null 2>&1 && warn "Claude: restart its session to load the wiki (new sessions already have it)"
  fi
fi
# Keep MCP + web running after a reboot (Linux: user crontab, no root). Without it the agents
# lose the wiki at the next server restart while everything else comes back.
if [ "$(uname -s)" = "Linux" ] && command -v crontab >/dev/null 2>&1; then
  keep="$(ask "  Start the wiki automatically after a reboot (user crontab)? [Y/n]: " "Y")"
  if [ "$(lc "$keep")" != "n" ]; then
    cat >state/keep.sh <<KEEP
#!/usr/bin/env bash
# Generated by install.sh: start MCP/web if they are not running (cron @reboot + every minute).
cd "$DIR" || exit 0
started=""
for svc in serve web; do
  pid="\$(cat "state/\$svc.pid" 2>/dev/null)"
  if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null; then continue; fi
  nohup ./haw "\$svc" >>"state/\$svc.log" 2>&1 </dev/null &
  echo \$! >"state/\$svc.pid"
  started="\$started \$svc"
done
# After a boot the agents come up before the MCP finished loading and miss the wiki for good
# (they read MCP servers only at start). Once it answers, restart them through Agents Monitoring.
case "\$started" in *serve*) ;; *) exit 0 ;; esac
up="\${HAW_UPTIME:-\$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 99999)}"
[ "\$up" -lt 900 ] || exit 0
command -v agentsmon >/dev/null 2>&1 || PATH="\$HOME/.local/bin:\$PATH"
command -v agentsmon >/dev/null 2>&1 && agentsmon restart --help >/dev/null 2>&1 || exit 0
(
  for _ in \$(seq 1 100); do
    code="\$(curl -s -o /dev/null -m 5 -w '%{http_code}' -X POST "$MCP_URL" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"haw-keep","version":"1"}}}' 2>/dev/null)"
    if [ "\$code" = "200" ]; then agentsmon restart --all >>state/keep.log 2>&1; exit 0; fi
    sleep 3
  done
) </dev/null >/dev/null 2>&1 &
KEEP
    chmod 700 state/keep.sh
    { crontab -l 2>/dev/null | grep -v '# humanagentwiki-keep$' || true
      printf '@reboot /bin/bash %s/state/keep.sh # humanagentwiki-keep\n' "$DIR"
      printf '* * * * * /bin/bash %s/state/keep.sh # humanagentwiki-keep\n' "$DIR"; } | crontab - \
      && ok "wiki starts automatically after a reboot (crontab, checked every minute)" \
      || warn "could not write the crontab - add $DIR/state/keep.sh to your boot/supervisor"
  else
    warn "for persistence after reboot, add './haw serve' and './haw web' to your boot/supervisor"
  fi
else
  warn "for persistence after reboot, add './haw serve' and './haw web' to your boot/supervisor"
fi

# done + link ---------------------------------------------------------------
PRIMARY_IP="$(.venv/bin/python - <<'PYIP'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("1.1.1.1", 80))
    print(s.getsockname()[0]); s.close()
except Exception:
    print("127.0.0.1")
PYIP
)"
step "Done!"
printf "\n  ${B}${G}HumanAgentWiki is installed${X}  ->  %s\n" "$DIR"
printf "\n  ${B}Open the wiki:${X}\n"
if [ "$WEB_HOST_VAL" = "0.0.0.0" ]; then
  printf "    ${C}http://%s:%s${X}   (local: http://127.0.0.1:%s)\n" "$PRIMARY_IP" "$WEB_PORT_VAL" "$WEB_PORT_VAL"
else
  printf "    ${C}http://127.0.0.1:%s${X}\n" "$WEB_PORT_VAL"
fi
printf "\n  Agents query it via MCP at %s\n" "$MCP_URL"
printf "  Add your own notes as Markdown under %s   then  ./haw index\n\n" "$NOTES_VAL"
