# Agentsmon health plugin

This generic **service-card module** belongs to HumanAgentWiki. It uses Agentsmon's
documented `services[].command`/JSON `rows` interface, with the existing service
renderer, uptime, SLA and 90-day timeline. It needs no product-specific core edits, private plugin
fork, custom JavaScript, database password or web password. Table-plugin workers
are intended for agent tables; this integration uses the supported service module
path because that path already persists health history.

## Installation

1. Copy `config.example.json` to a private configuration path. Set local endpoints,
   database name/socket/port and a unique path fragment from the **existing** minute
   cron keeper. Web and MCP must use literal loopback addresses. The web probe never
   follows redirects or sends authentication: **401 is healthy; 200 is a failure**.
2. With HAW's Python (only psycopg is required), run:

   ```sh
   /path/to/wiki/.venv/bin/python /path/to/wiki/monitoring/agentsmon.py --config /path/to/health.json
   /path/to/wiki/.venv/bin/python /path/to/wiki/monitoring/install_agentsmon.py \
     --agentsmon-config /path/to/agentsmon/config.json \
     --python /path/to/wiki/.venv/bin/python --monitor-config /path/to/health.json
   ```

   Registration is idempotent, backs up the config with mode 0600 and preserves
   other entries. No service is started or restarted. Agentsmon reloads service
   configuration on each probe; the section appears after its next pass. Keep
   `probe.sla_window_days` and `probe.timeline_days` at 90. Prior unobserved days
   remain gray, never fabricated as healthy.
   The generic Agentsmon option `services[].row_details: true` makes count/time
   details visible instead of hover-only. Install an Agentsmon version supporting
   this display option; older versions still probe correctly but hide details in
   tooltips. Updating the renderer requires a dashboard-only restart.
3. For an intentionally empty wiki only, set `allow_empty: true`.

## What the evidence means

- Web: anonymous request requires login (401). It does not test a password.
- MCP: a real initialize + tools/list exchange checks all three read tools.
  It reads no notes and closes the temporary session.
- PostgreSQL: a read-only SELECT via a local UNIX socket, with connect/statement
  timeouts. No application `.env`, embedding model, auth file or note text is read.
- Index: count of **files**, not chunks. Time is `max(files.updated_at)`, the last
  committed indexing write for a note. **It is not proof of a complete successful
  batch, or that every file on disk is current.** No timestamp is invented for a
  new empty wiki; a missing schema/DB is unavailable, never "zero notes".
- Recovery: confirms a matching every-minute cron keeper exists. This is scheduler
  configuration evidence, not proof of its latest run. Only the existing keeper
  owns recovery (including database startup and authenticated web environment).
  The plugin never starts another instance, calls a restart command, or kills a PID.

Subchecks run concurrently and fail independently. HTTP/RPC response bodies,
SQL/connection exceptions and cron output never reach the dashboard or logs.
Agentsmon's 12-second subprocess deadline bounds the entire probe. Unregister by
removing only the `HumanAgentWiki` service from config; history remains in its
existing uptime store. Nothing changes the application's own keeper or FIM baseline.

## Tests

`python -m pytest tests` runs local fixture HTTP/JSON/SSE probes, negative auth/MCP
cases, DB failure/empty distinction, no-restart assertions and config preservation.
Also run Agentsmon's complete `python -m unittest discover -s tests -v` in its repo.
