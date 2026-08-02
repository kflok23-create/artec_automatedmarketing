# hermes-brain — Railway setup

The Sunday brain: NousResearch hermes-agent, pinned to `v2026.7.30`, speaking to Postgres
only through the six tools in `plugins/artec_hermes.py`.

## Create the service

1. Railway → project → New Service → **Deploy from GitHub repo** (this repo).
2. Settings → Build: **Dockerfile path** = `deploy/hermes-brain/Dockerfile`.
3. Settings → **Volumes → Add volume**, mount path `/data/hermes`. MANDATORY — config,
   memory, sessions, skills and the plugin live here and must survive redeploys.
4. Variables (and nothing more — the agent receives NO payment/Drive/fal/Brevo secret):
   ```
   HERMES_HOME      = /data/hermes
   DATABASE_URL     = ${{ Postgres.DATABASE_URL }}
   ANTHROPIC_API_KEY = <same key as artec-api>
   ANTHROPIC_MODEL  = <same model as artec-api>
   TELEGRAM_BOT_TOKEN = <the gate bot>
   TELEGRAM_CHAT_ID   = <the operator chat>
   ```
5. Deploy. The entrypoint hard-fails if the volume is missing or read-only, installs the
   plugin + profile config into the volume, then `hermes gateway --profile artec`.

## artec-scheduler (while you're in the dashboard)

New Service → same GitHub repo → Settings → Deploy → **Custom start command**:
`python -m app.scheduler`. Same variables as artec-api. No volume needed.

## Verify

- `hermes doctor` in the service shell — green, Telegram connected, cron shows the two
  Sunday jobs (07:00 learn-ideate, 09:00 weekly-gate, Asia/Singapore).
- `artec doctor` — the "hermes-brain volume" line is green; after the NEXT redeploy it
  reads "marker present since …" which is the survives-redeploy proof.
- First Sunday: watch `agent_runs` and `plans_shadow` fill; the gate presents the BESPOKE
  plan while `plan_source=shadow`.

## Upgrading the agent (manual, never scheduled)

Bump `HERMES_AGENT_TAG` in the Dockerfile to a newer PINNED tag, redeploy, run both
doctors. Never track `main`; never run `hermes update` on a schedule.

## Monthly hygiene (first Sunday)

```
hermes curator            # skill hygiene, in the hermes-brain shell
artec agent-review        # prints skill list + MEMORY.md
artec audit-memory        # metric-shaped content in memory → visible, then scrubbed
```
