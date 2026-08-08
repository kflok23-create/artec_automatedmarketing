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

step "1/11 volume probe (HERMES_HOME=$HERMES_HOME)"
: "${HERMES_HOME:?HERMES_HOME must be set (the /data/hermes volume)}"
[ -d "$HERMES_HOME" ] || fail "HERMES_HOME does not exist — is the volume mounted?"
touch "$HERMES_HOME/.write-probe" 2>/dev/null || fail "HERMES_HOME is not writable — read-only volume?"
rm -f "$HERMES_HOME/.write-probe"
python /bootstrap/report_volume.py || echo "WARN: volume marker report to Postgres failed — artec doctor will show this RED"
echo "volume ok"

step "2/11 profile '$PROFILE' (create if missing, then use)"
if hermes profile list 2>/dev/null | grep -q "$PROFILE"; then
    echo "profile exists — skipping create (volume persisted it)"
else
    hermes profile create "$PROFILE"
fi
hermes profile use "$PROFILE"

step "3/11 remove stray pre-rename 'artec' profile (wrapper-binary collision trap)"
if hermes profile list 2>/dev/null | grep -Eq "(^|[[:space:]])artec[:[:space:]]"; then
    hermes profile delete artec -y && echo "stray 'artec' profile deleted" \
        || echo "WARN: could not delete stray 'artec' profile — remove manually"
else
    echo "no stray profile"
fi

step "4/11 install plugin package onto the volume"
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

step "5/11 profile config.yaml"
if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    cp /bootstrap/config.yaml "$HERMES_HOME/profiles/$PROFILE/config.yaml"
else
    cp /bootstrap/config.yaml "$HERMES_HOME/config.yaml"
fi
echo "config installed"

step "6/11 credentials → profile .env (hermes reads THE PROFILE .env, not Railway env)"
# Strip ALL whitespace defensively: a trailing newline from a dashboard paste survives the
# presence check and then 401s at the first real conversation — which happened live.
ANTHROPIC_KEY_CLEAN=$(printf '%s' "$ANTHROPIC_API_KEY" | tr -d '[:space:]')
TELEGRAM_TOKEN_CLEAN=$(printf '%s' "$TELEGRAM_BOT_TOKEN" | tr -d '[:space:]')
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_TOKEN_CLEAN"
hermes config set ANTHROPIC_API_KEY "$ANTHROPIC_KEY_CLEAN"
# deliver_video reads the PUBLISH BYTES from the artec api (the brain holds no Drive
# credentials by design). Absent → deliver_video reports it and refuses; it never falls back
# to a remote URL, because that is how the operator ends up approving a different file.
if [ -n "${ARTEC_API_BASE:-}" ] && [ -n "${HERMES_API_TOKEN:-}" ]; then
    hermes config set ARTEC_API_BASE "$(printf '%s' "$ARTEC_API_BASE" | tr -d '[:space:]')"
    hermes config set HERMES_API_TOKEN "$(printf '%s' "$HERMES_API_TOKEN" | tr -d '[:space:]')"
    echo "media endpoint configured for deliver_video"
else
    echo "WARN: ARTEC_API_BASE / HERMES_API_TOKEN not set — deliver_video will refuse and say so"
fi
[ -f "$ENVFILE" ] || fail "profile .env missing at $ENVFILE after config set"
grep -q "ANTHROPIC_API_KEY=." "$ENVFILE" || fail "ANTHROPIC_API_KEY absent from $ENVFILE — the agent cannot call Claude"
grep -q "TELEGRAM_BOT_TOKEN=." "$ENVFILE" || fail "TELEGRAM_BOT_TOKEN absent from $ENVFILE — the gateway cannot connect"

# Presence is not validity (a wrong key passed the old check and 401'd in production):
# probe the Anthropic API with the exact key value before letting the gateway start.
ANTHROPIC_KEY_PROBE="$ANTHROPIC_KEY_CLEAN" python - <<'PY' || fail "ANTHROPIC_API_KEY is PRESENT but REJECTED by the Anthropic API — the value on this service differs from artec api's working key; re-paste it (watch for truncation/whitespace)"
import os, sys
import httpx
resp = httpx.get(
    "https://api.anthropic.com/v1/models",
    headers={"x-api-key": os.environ["ANTHROPIC_KEY_PROBE"],
             "anthropic-version": "2023-06-01"},
    timeout=30,
)
print(f"anthropic key probe: HTTP {resp.status_code}")
sys.exit(0 if resp.status_code == 200 else 1)
PY
echo "credentials present in profile .env and VALID against the Anthropic API"

step "7/11 scouting probe + memory audit (neither may fail the boot)"
# A5: presence is not validity — probe the REAL search endpoint and record the answer where
# the digest reads it. A5 failure is reported, never fatal: losing trend input costs a
# worse week, refusing to boot costs the whole week.
python /bootstrap/probe_scouting.py || echo "WARN: scouting probe script itself failed"
# A4: memory.write_approval is false, so the agent writes memory autonomously. Numbers must
# never live there. Audited HERE because $HERMES_HOME is this volume; job 10 will run it
# weekly once the twelve jobs are registered.
# D-2 · A.1 — purge stale CAPABILITY claims BEFORE the audit, so the audit's result
# reflects the store the next session will actually read. Prints everything verbatim first
# and records before/after to config.memory_purge, because a brain boot log has already
# been dropped once at Railway's 500/s limit and evidence that lives only in a log can
# vanish. Never fatal: a memory problem must not cost the week.
python /bootstrap/purge_memory_claims.py || echo "WARN: memory purge script itself failed"
python /bootstrap/audit_memory_report.py || echo "WARN: memory audit script itself failed"
# READ-ONLY production report: the board, agent_runs, and the orphaned digest payload.
# Every statement is a SELECT. Never fatal.
python /bootstrap/report_board.py || echo "WARN: board report script itself failed"

step "8/11 plugin discovery + enable"
HERMES_PLUGINS_DEBUG=1 hermes plugins list || true
hermes plugins enable artec || fail "hermes plugins enable artec failed — see the discovery log above for scanned directories and skip reasons"
if hermes plugins list --plain 2>/dev/null | grep -i "artec" | grep -qi "enabled"; then
    echo "artec plugin ENABLED"
elif hermes plugins list 2>/dev/null | grep -qi "artec"; then
    echo "artec plugin discovered (enable state not visible in --plain output — continuing)"
else
    fail "artec plugin did not load — see the discovery log above"
fi

# S2, CLOSED 2026-08-05 — this used to print "artec tools registered: N / 15" and WARN when
# N < 15. It printed 0 / 15 on every boot, including boots where the tools then worked
# perfectly in the live session. The count was structurally premature: `hermes plugins
# enable` says so itself, two lines above —
#
#     ✓ Plugin artec enabled. Takes effect on next session.
#
# Tools register at SESSION START. A boot-time count runs before any session exists, so it
# can only ever report zero. The warning was not detecting a fault; it was measuring the
# wrong moment, and it was wrong every single time.
#
# A WARNING THAT IS ALWAYS WRONG TRAINS EVERYONE TO IGNORE IT — including the boot output
# that carries the checks which are real. That is why this is a defect worth fixing rather
# than noise worth tolerating: it degrades every other line printed beside it.
#
# What is checked instead is what CAN be known at boot: that the plugin is enabled and that
# the toolset is discoverable. The count belongs to the session, and the live catalog is
# authoritative there.
echo "--- proof: artec toolset present (COUNT DEFERRED — tools register at session start) ---"
# NO ASSERTION HERE, DELIBERATELY. The first attempt at this fix replaced the always-wrong
# count with `hermes tools | grep artec` as a FATAL — and it took the service down on the
# very next deploy, because `hermes tools` cannot see the toolset at boot FOR THE SAME
# REASON the count was zero. Registration happens at session start. Swapping an always-wrong
# warning for an always-wrong fatal made it strictly worse.
#
# There is nothing about tool registration that boot can honestly check. Step 8/10 already
# hard-fails if the plugin does not load, which IS knowable here. The catalog is
# authoritative at session start and that is where the count belongs.
echo "artec plugin enabled; the 15 handlers register at SESSION START, so no boot-time"
echo "count is possible — step 8/10 above is the check that can actually be made here"

step "9/11 cron jobs (numeric day-of-week; create exits 0 on failure, so VERIFY by listing)"
if ! hermes cron list 2>/dev/null | grep -qi "learn-ideate"; then
    hermes cron create "0 7 * * 0" "$(cat /bootstrap/cron-learn-ideate.txt)" \
        --name learn-ideate --deliver telegram
fi
if ! hermes cron list 2>/dev/null | grep -qi "weekly-gate"; then
    hermes cron create "0 9 * * 0" "$(cat /bootstrap/cron-weekly-gate.txt)" \
        --name weekly-gate --deliver telegram
fi
# Job 12 - MON-SAT 21:00. Numeric day-of-week ONLY: `cron create` has been observed
# REJECTING `SUN` while exiting 0, which is why nothing here trusts an exit code.
if ! hermes cron list 2>/dev/null | grep -qi "nightly-digest"; then
    hermes cron create "0 21 * * 1-6" "$(cat /bootstrap/cron-nightly-digest.txt)" \
        --name nightly-digest --deliver telegram
fi
echo "--- proof: hermes cron list ---"
CRON_LIST=$(hermes cron list 2>&1)
printf '%s\n' "$CRON_LIST"
printf '%s' "$CRON_LIST" | grep -qi "learn-ideate" || fail "cron job learn-ideate did not register — create exits 0 even on failure; read its output above"
printf '%s' "$CRON_LIST" | grep -qi "weekly-gate" || fail "cron job weekly-gate did not register — create exits 0 even on failure; read its output above"
printf '%s' "$CRON_LIST" | grep -qi "nightly-digest" || fail "cron job nightly-digest (job 12) did not register — create exits 0 even on failure; read its output above"
# Job 12 is MON-SAT. A listing whose next run lands on a Sunday means the expression was
# taken as daily, and the operator would be interrupted on the one evening the gate owns.
printf '%s' "$CRON_LIST" | grep -i "nightly-digest" | grep -qi "sun" \
    && fail "nightly-digest lists a SUNDAY next-run — job 12 must be MON-SAT (0 21 * * 1-6)"
printf '%s' "$CRON_LIST" | grep -q "+08:00" || fail "cron next-run times are not Asia/Singapore (+08:00) — check the image TZ"
# EXACT SET, NOT A SUBSET. Every check above asks "is this job present?" — none asks "is
# anything ELSE present?". A thirteenth job would pass all of them. The schedule is twelve
# jobs; three of them are the brain's, and a fourth brain cron is a defect however it got
# there. Cron mutation is now blocked at the pre_tool_call hook, so this is the check that
# catches anything that arrived before the block, or around it.
CRON_NAMES=$(printf '%s\n' "$CRON_LIST" | sed -n 's/^ *Name: *//p' | sort)
EXPECTED_NAMES=$(printf '%s\n' "learn-ideate" "nightly-digest" "weekly-gate" | sort)
if [ "$CRON_NAMES" != "$EXPECTED_NAMES" ]; then
    fail "the brain's cron set is not EXACTLY the three expected jobs.
  expected: $(printf '%s' "$EXPECTED_NAMES" | tr '\n' ' ')
  actual:   $(printf '%s' "$CRON_NAMES" | tr '\n' ' ')
An EXTRA job is a defect, not a curiosity: the twelve jobs are a deployment artefact and
the only supported way to change them is app/jobs.py plus a deploy. A MISSING job means a
touch does not happen. Both are fatal here."
fi
echo "all THREE brain cron jobs registered (3 learn-ideate, 5 weekly-gate, 12 nightly-digest); times resolve to +08:00 (Asia/Singapore)"

step "10/11 the three proofs that can only run here (never fatal)"
# §9 classifies nine capabilities. `artec prove-all` runs on artec-scheduler and can only
# ever report three of them NOT PROVABLE — agent-session, sunday-cron, audit-memory — because
# the evidence is the hermes message store, the cron registry and the memory files, and all
# three live on THIS volume. A prover pointed at a machine that cannot hold the evidence
# reports not_provable forever, however correct it is.
#
# Runs AFTER step 9, deliberately: sunday-cron proves itself by reading `hermes cron list`,
# so the jobs must already be registered. Results merge into config.proofs, the same row the
# other six write, so proof_status and the digest see one set.
#
# NEVER FATAL, and the reason is on the record twice: escalating an always-wrong check to a
# FATAL took this service down once, and a boot-time diagnostic that can hang cost the
# scheduler nineteen minutes on 2026-08-08. A proof that cannot prove must report, not halt.
# Step 9 above already hard-fails on the cron facts that genuinely must hold at boot.
python /bootstrap/prove_brain.py || echo "WARN: brain proof script itself failed"

step "11/11 gateway (foreground)"
exec hermes gateway run
