#!/bin/sh
# hermes-brain boot. Idempotent on every restart (the volume persists profile, plugins,
# cron and memory). Each step prints a labelled banner so a failing step is unambiguous
# even with hermes-agent's own output interleaved.
#
# Profile name is artec-brain, NOT artec: hermes-agent creates a wrapper binary named
# after the profile (/root/.local/bin/<profile>), which would collide with the bespoke
# `artec` CLI if this image ever gains it. artec-brain can never collide.
set -eu

step() { printf '\n=== [hermes-brain boot] %s ===\n' "$1"; }
fail() { printf '\n=== [hermes-brain boot] FATAL: %s ===\n' "$1" >&2; exit 1; }

PROFILE=artec-brain

step "1/8 volume probe (HERMES_HOME=$HERMES_HOME)"
: "${HERMES_HOME:?HERMES_HOME must be set (the /data/hermes volume)}"
[ -d "$HERMES_HOME" ] || fail "HERMES_HOME does not exist — is the volume mounted?"
touch "$HERMES_HOME/.write-probe" 2>/dev/null || fail "HERMES_HOME is not writable — read-only volume?"
rm -f "$HERMES_HOME/.write-probe"
# Report the volume marker to Postgres so `artec doctor` on artec-api verifies this for
# real (marker predating this boot = survived a redeploy). Non-fatal but loud: without
# the report, artec doctor's volume line is RED, which is the correct signal.
python /bootstrap/report_volume.py || echo "WARN: volume marker report to Postgres failed — artec doctor will show this RED"
echo "volume ok"

step "2/8 profile '$PROFILE' (create if missing, then use)"
if hermes profile list 2>/dev/null | grep -q "$PROFILE"; then
    echo "profile exists — skipping create (volume persisted it)"
else
    hermes profile create "$PROFILE"
fi
hermes profile use "$PROFILE"

step "3/8 install plugin package onto the volume"
# The volume mounts OVER /data/hermes at runtime, so the package must be copied on every
# boot, after the mount. Discovery scans differ by profile setup, so install into BOTH
# candidate locations; the debug listing in step 6 shows which one hermes actually scans.
for dest in "$HERMES_HOME/plugins" "$HERMES_HOME/profiles/$PROFILE/plugins"; do
    mkdir -p "$dest"
    rm -rf "$dest/artec"
    cp -r /bootstrap/plugins/artec "$dest/artec"
done
echo "--- verify: $HERMES_HOME/plugins/artec ---"
ls -la "$HERMES_HOME/plugins/artec" || fail "plugin package missing at \$HERMES_HOME/plugins/artec"
echo "--- verify: $HERMES_HOME/profiles/$PROFILE/plugins/artec ---"
ls -la "$HERMES_HOME/profiles/$PROFILE/plugins/artec" || fail "plugin package missing at profile plugins dir"

step "4/8 profile config.yaml"
if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    cp /bootstrap/config.yaml "$HERMES_HOME/profiles/$PROFILE/config.yaml"
else
    cp /bootstrap/config.yaml "$HERMES_HOME/config.yaml"
fi
echo "config installed"

step "5/8 telegram token → profile .env (hermes config set)"
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"

step "6/8 plugin discovery (debug — scanned directories and skip reasons below)"
HERMES_PLUGINS_DEBUG=1 hermes plugins list || true

step "7/8 enable + verify artec plugin"
hermes plugins enable artec || fail "hermes plugins enable artec failed — see the discovery log in step 6 for scanned directories and skip reasons"
if hermes plugins list --plain 2>/dev/null | grep -i "artec" | grep -qi "enabled"; then
    echo "artec plugin ENABLED"
elif hermes plugins list 2>/dev/null | grep -qi "artec"; then
    echo "artec plugin discovered (enable state not visible in --plain output — continuing)"
else
    fail "artec plugin did not load — see the step 6 discovery log"
fi

step "8/8 cron jobs (idempotent) + gateway"
if ! hermes cron list 2>/dev/null | grep -qi "LEARN then IDEATE"; then
    hermes cron create "0 7 * * SUN" "$(cat /bootstrap/cron-learn-ideate.txt)"
fi
if ! hermes cron list 2>/dev/null | grep -qi "WEEKLY GATE over Telegram"; then
    hermes cron create "0 9 * * SUN" "$(cat /bootstrap/cron-weekly-gate.txt)"
fi
hermes cron list || true

exec hermes gateway run
