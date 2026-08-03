# hermes-brain — Railway setup

The Sunday brain: NousResearch hermes-agent, **pinned `v2026.7.30` (release 0.19.1,
2026-07-30)**, speaking to Postgres only through the six tools in `plugins/artec/`.

Every command form below is verified against
[the CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) and
[the plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
unless marked otherwise.

## Create the service

1. Railway → project → New Service → **Deploy from GitHub repo** (this repo).
2. Settings → Build: **Dockerfile path** = `deploy/hermes-brain/Dockerfile`.
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
5. Deploy. The entrypoint, in order: hard-fails without a writable volume → `hermes
   profile create artec` + `profile use artec` → installs the plugin **package**
   (`plugin.yaml` + `__init__.py` + modules) into `$HERMES_HOME/plugins/artec/` →
   `hermes config set TELEGRAM_BOT_TOKEN …` → **`hermes plugins enable artec`** (plugins
   are opt-in; without this the six tools never load) → prints
   `HERMES_PLUGINS_DEBUG=1 hermes plugins list` and hard-fails if artec is absent →
   registers the two Sunday cron jobs idempotently (`hermes cron create`) →
   **`exec hermes gateway run`** (the documented foreground mode for containers).

## Verify (in the service shell)

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list   # artec loaded, six tools, skip reasons if not
hermes cron list                             # the two Sunday jobs, Asia/Singapore times
hermes doctor                                # agent-side health
hermes tools --summary                       # terminal/code_execution/file disabled
```
Then `artec doctor` (via `/commands/doctor`) — the "hermes-brain volume" line reads
"marker present since …" after the SECOND deploy, which is the survives-redeploy proof.

## Not verified in this build — confirm on first boot

- The Telegram allowed-chat whitelist: configure per
  [/docs/user-guide/messaging/telegram](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram).
- `hermes cron create` timezone semantics: the container sets `TZ=Asia/Singapore`;
  confirm `hermes cron list` shows the jobs at 07:00/09:00 SGT.
- That `agent.disabled_toolsets` names (`terminal`, `code_execution`, `file`) cover every
  shell/file surface on this tag: check `hermes tools --summary`. The plugin's
  `pre_tool_call` hook blocks the same families as defense in depth regardless.

## Upgrading the agent (manual, never scheduled)

Bump `HERMES_AGENT_TAG` in the Dockerfile to a newer PINNED tag, redeploy, re-run the
verify block. Never track `main`; never run `hermes update` on a schedule.

## Monthly hygiene (first Sunday)

```bash
hermes curator run        # skill hygiene (in the hermes-brain shell)
artec agent-review        # prints skill list + MEMORY.md
artec audit-memory        # metric-shaped content in memory → visible, then scrubbed
```
