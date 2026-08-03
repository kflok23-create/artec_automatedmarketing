#!/bin/sh
# hermes-brain boot. Every command form verified against
# https://hermes-agent.nousresearch.com/docs/reference/cli-commands unless marked.
set -eu

: "${HERMES_HOME:?HERMES_HOME must be set (the /data/hermes volume)}"

if [ ! -d "$HERMES_HOME" ]; then
    echo "FATAL: HERMES_HOME=$HERMES_HOME does not exist — is the volume mounted?" >&2
    exit 1
fi
if ! touch "$HERMES_HOME/.write-probe" 2>/dev/null; then
    echo "FATAL: HERMES_HOME=$HERMES_HOME is not writable — read-only volume?" >&2
    exit 1
fi
rm -f "$HERMES_HOME/.write-probe"

# Profile isolation — verified: `hermes profile create <name>` / `hermes profile use`.
hermes profile create artec 2>/dev/null || true
hermes profile use artec

# Install the plugin PACKAGE (plugin.yaml + __init__.py + modules) into the volume.
# Layout per /docs/developer-guide/plugins: $HERMES_HOME/plugins/<plugin-name>/.
mkdir -p "$HERMES_HOME/plugins"
rm -rf "$HERMES_HOME/plugins/artec"
cp -r /bootstrap/plugins/artec "$HERMES_HOME/plugins/artec"
cp /bootstrap/config.yaml "$HERMES_HOME/profiles/artec/config.yaml" 2>/dev/null \
    || cp /bootstrap/config.yaml "$HERMES_HOME/config.yaml"

# Credentials go to the profile .env via the CLI, not config.yaml — verified:
# /docs/user-guide/configuration (Telegram Gateway Settings).
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"

# Plugins are OPT-IN — without this the six tools never load. Verified:
# `hermes plugins enable <name>`.
hermes plugins enable artec

# Discovery verification at every boot — the check that catches a silently-skipped
# plugin. Verified: HERMES_PLUGINS_DEBUG=1 hermes plugins list.
echo "--- plugin discovery ---"
HERMES_PLUGINS_DEBUG=1 hermes plugins list
if ! hermes plugins list | grep -qi "artec"; then
    echo "FATAL: artec plugin did not load — see discovery log above" >&2
    exit 1
fi

# The two Sunday cron jobs (2 of the 4 total; artec-scheduler owns the daily 2).
# The cron-create CLI form is verified; `hermes cron list` is the idempotency guard so a
# redeploy never duplicates jobs. Schedule timezone handling:
# NOT VERIFIED — confirm with `hermes cron list` on first boot that the two jobs show
# Asia/Singapore times (the container TZ is set in the Dockerfile as a belt).
if ! hermes cron list 2>/dev/null | grep -qi "LEARN then IDEATE"; then
    hermes cron create "0 7 * * SUN" "$(cat /bootstrap/cron-learn-ideate.txt)"
fi
if ! hermes cron list 2>/dev/null | grep -qi "WEEKLY GATE over Telegram"; then
    hermes cron create "0 9 * * SUN" "$(cat /bootstrap/cron-weekly-gate.txt)"
fi
echo "--- cron jobs ---"
hermes cron list

# Foreground mode for containers — verified: `hermes gateway run`.
exec hermes gateway run
