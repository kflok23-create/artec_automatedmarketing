# hermes-brain — Railway setup

The Sunday brain: NousResearch hermes-agent, **pinned `v2026.7.30` (release 0.19.1,
2026-07-30)**, speaking to Postgres only through the six tools in `plugins/artec/`.

Every command form below is verified against
[the CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) and
[the plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
unless marked otherwise.

## Create the service

1. Railway → project → New Service → **Deploy from GitHub repo** (this repo).
2. Settings → **Config-as-code → Config File Path** = `railway.hermes-brain.json`.
   This is MANDATORY: without it Railway applies the root `railway.json`, whose
   `preDeployCommand: alembic upgrade head` fails on this image (no alembic) — the
   "Pre deploy command failed" error. The config file also carries the Dockerfile path.
3. Settings → **Volumes → Add volume**, mount path `/data/hermes`. MANDATORY.
4. Variables (the agent receives NO payment/Drive/fal/Brevo secret):
   ```
   HERMES_HOME       = /data/hermes
   DATABASE_URL      = ${{ Postgres.DATABASE_URL }}
   ANTHROPIC_API_KEY = <same key as artec-api>
   ANTHROPIC_MODEL   = <same model as artec-api>
   TELEGRAM_BOT_TOKEN = <the gate bot>
   TELEGRAM_CHAT_ID   = <the operator chat>
   ```
5. Deploy. The entrypoint runs eight labelled steps, idempotent on every restart:
   volume probe → profile **`artec-brain`** (create-if-missing, then use; the name is NOT
   `artec` because hermes-agent creates a wrapper binary named after the profile at
   `/root/.local/bin/<profile>`, which would collide with the bespoke `artec` CLI) →
   plugin package copied onto the volume in BOTH candidate locations
   (`$HERMES_HOME/plugins/artec/` and `$HERMES_HOME/profiles/artec-brain/plugins/artec/`,
   with `ls` verification of each — the volume mounts over `/data/hermes`, so this runs
   after the mount, every boot) → profile config.yaml → `hermes config set
   TELEGRAM_BOT_TOKEN …` → `HERMES_PLUGINS_DEBUG=1 hermes plugins list` (always printed,
   so scanned directories and skip reasons are in the log BEFORE any failure) →
   **`hermes plugins enable artec`** + verification, hard-failing with a pointer to the
   discovery log → idempotent `hermes cron create` ×2 → **`exec hermes gateway run`**.

## Verify (in the service shell)

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list   # artec loaded, six tools, skip reasons if not
hermes cron list                             # the two Sunday jobs, Asia/Singapore times
hermes doctor                                # agent-side health
hermes tools --summary                       # terminal/code_execution/file disabled
```
Then `artec doctor` (via `/commands/doctor`) — the "hermes-brain volume" line reads
"marker present since …" after the SECOND deploy, which is the survives-redeploy proof.

## Verified empirically against a real hermes-agent install

- **Cron schedules must be numeric** (`0 7 * * 0`; the parser rejects `SUN` — and exits 0
  doing so). The entrypoint verifies registration via `hermes cron list` and hard-fails
  if either job is missing.
- **Cron times resolve in the container's local TZ**: with `TZ=Asia/Singapore` the next
  runs display `+08:00`; the boot hard-fails if they don't.
- **`agent.disabled_toolsets` works** — `hermes tools --summary` drops to the allowed set
  with the config in place. The plugin's `pre_tool_call` hook doubles the shell/file
  blocks regardless.

## Not verified in this build — confirm on first boot

- The Telegram allowed-chat whitelist: configure per
  [/docs/user-guide/messaging/telegram](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram).
- Exact toolset ids can drift between agent versions (0.18.x calls the task board
  `kanban`, 0.19.x lists `todo` — both are disabled). After any tag bump, re-check
  `hermes tools --summary`.

## Upgrading the agent (manual, never scheduled)

Bump `HERMES_AGENT_TAG` in the Dockerfile to a newer PINNED tag, redeploy, re-run the
verify block. Never track `main`; never run `hermes update` on a schedule.

## Monthly hygiene (first Sunday)

```bash
hermes curator run        # skill hygiene (in the hermes-brain shell)
artec agent-review        # prints skill list + MEMORY.md
artec audit-memory        # metric-shaped content in memory → visible, then scrubbed
```
