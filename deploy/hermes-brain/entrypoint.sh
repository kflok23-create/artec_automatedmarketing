#!/bin/sh
# hermes-brain boot. Idempotent on every restart (the volume persists profile, plugins,
# cron and memory). Each step prints a labelled banner so a failing step is unambiguous.
#
# Hard-won empirical facts encoded here (verified against a real hermes-agent install):
# - `hermes cron create` REJECTS day names ('SUN') and EXITS 0 ON FAILURE — schedules
#   must be numeric (Sunday = 0) and registration must be verified by listing, never
#   trusted by exit code. That combination silently shipped zero cron jobs once.
# - Cron next-run times resolve in the container's local TZ; the image pins
#   TZ=Asia/Singapore, and this script hard-fails unless next runs show +08:00.
# - hermes reads credentials from the PROFILE .env — every needed key must be written
#   via `hermes config set` and asserted present.
#
# Profile name is artec-brain, NOT artec: hermes-agent creates a wrapper binary named
# after the profile, which would collide with the bespoke `artec` CLI.
set -eu

step() { printf '\n=== [hermes-brain boot] %s ===\n' "$1"; }
fail() { printf '\n=== [hermes-brain boot] FATAL: %s ===\n' "$1" >&2; exit 1; }

PROFILE=artec-brain
ENVFILE="$HERMES_HOME/profiles/$PROFILE/.env"

step "1/9 volume probe (HERMES_HOME=$HERMES_HOME)"
: "${HERMES_HOME:?HERMES_HOME must be set (the /data/hermes volume)}"
[ -d "$HERMES_HOME" ] || fail "HERMES_HOME does not exist — is the volume mounted?"
touch "$HERMES_HOME/.write-probe" 2>/dev/null || fail "HERMES_HOME is not writable — read-only volume?"
rm -f "$HERMES_HOME/.write-probe"
python /bootstrap/report_volume.py || echo "WARN: volume marker report to Postgres failed — artec doctor will show this RED"
echo "volume ok"

step "2/9 profile '$PROFILE' (create if missing, then use)"
if hermes profile list 2>/dev/null | grep -q "$PROFILE"; then
    echo "profile exists — skipping create (volume persisted it)"
else
    hermes profile create "$PROFILE"
fi
hermes profile use "$PROFILE"

step "3/9 remove stray pre-rename 'artec' profile (wrapper-binary collision trap)"
if hermes profile list 2>/dev/null | grep -Eq "(^|[[:space:]])artec[:[:space:]]"; then
    hermes profile delete artec -y && echo "stray 'artec' profile deleted" \
        || echo "WARN: could not delete stray 'artec' profile — remove manually"
else
    echo "no stray profile"
fi

step "4/9 install plugin package onto the volume"
# The volume mounts OVER /data/hermes at runtime, so the package must be copied on every
# boot, after the mount, into BOTH candidate scan locations.
for dest in "$HERMES_HOME/plugins" "$HERMES_HOME/profiles/$PROFILE/plugins"; do
    mkdir -p "$dest"
    rm -rf "$dest/artec"
    cp -r /bootstrap/plugins/artec "$dest/artec"
done
echo "--- verify: $HERMES_HOME/plugins/artec ---"
ls -la "$HERMES_HOME/plugins/artec" || fail "plugin package missing at \$HERMES_HOME/plugins/artec"
echo "--- verify: $HERMES_HOME/profiles/$PROFILE/plugins/artec ---"
ls -la "$HERMES_HOME/profiles/$PROFILE/plugins/artec" || fail "plugin package missing at profile plugins dir"

step "5/9 profile config.yaml"
if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    cp /bootstrap/config.yaml "$HERMES_HOME/profiles/$PROFILE/config.yaml"
else
    cp /bootstrap/config.yaml "$HERMES_HOME/config.yaml"
fi
echo "config installed"

step "6/9 credentials → profile .env (hermes reads THE PROFILE .env, not Railway env)"
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
hermes config set ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
[ -f "$ENVFILE" ] || fail "profile .env missing at $ENVFILE after config set"
grep -q "ANTHROPIC_API_KEY=." "$ENVFILE" || fail "ANTHROPIC_API_KEY absent from $ENVFILE — the agent cannot call Claude"
grep -q "TELEGRAM_BOT_TOKEN=." "$ENVFILE" || fail "TELEGRAM_BOT_TOKEN absent from $ENVFILE — the gateway cannot connect"
echo "credentials present in profile .env"

step "7/9 plugin discovery + enable"
HERMES_PLUGINS_DEBUG=1 hermes plugins list || true
hermes plugins enable artec || fail "hermes plugins enable artec failed — see the discovery log above for scanned directories and skip reasons"
if hermes plugins list --plain 2>/dev/null | grep -i "artec" | grep -qi "enabled"; then
    echo "artec plugin ENABLED"
elif hermes plugins list 2>/dev/null | grep -qi "artec"; then
    echo "artec plugin discovered (enable state not visible in --plain output — continuing)"
else
    fail "artec plugin did not load — see the discovery log above"
fi

step "8/9 cron jobs (numeric day-of-week; create exits 0 on failure, so VERIFY by listing)"
if ! hermes cron list 2>/dev/null | grep -qi "learn-ideate"; then
    hermes cron create "0 7 * * 0" "$(cat /bootstrap/cron-learn-ideate.txt)" \
        --name learn-ideate --deliver telegram
fi
if ! hermes cron list 2>/dev/null | grep -qi "weekly-gate"; then
    hermes cron create "0 9 * * 0" "$(cat /bootstrap/cron-weekly-gate.txt)" \
        --name weekly-gate --deliver telegram
fi
echo "--- proof: hermes cron list ---"
CRON_LIST=$(hermes cron list 2>&1)
printf '%s\n' "$CRON_LIST"
printf '%s' "$CRON_LIST" | grep -qi "learn-ideate" || fail "cron job learn-ideate did not register — create exits 0 even on failure; read its output above"
printf '%s' "$CRON_LIST" | grep -qi "weekly-gate" || fail "cron job weekly-gate did not register — create exits 0 even on failure; read its output above"
printf '%s' "$CRON_LIST" | grep -q "+08:00" || fail "cron next-run times are not Asia/Singapore (+08:00) — check the image TZ"
echo "both Sunday jobs registered, times resolve to +08:00 (Asia/Singapore)"

step "9/9 gateway (foreground)"
exec hermes gateway run
