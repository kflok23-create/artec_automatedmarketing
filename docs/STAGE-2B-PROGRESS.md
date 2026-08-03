# Stage 2b progress — branch `v4-stage-2b`

**Branch base:** `36fdb55` (main) · **`main` stays deployable and is NOT touched.**
**Merge gate:** this branch merges only when A–I are all green together. Nothing here
deploys, so a partially-built branch cannot strand a post.

| Pass | Scope | State |
|---|---|---|
| **2b-i** | A · F · G · H + the Postgres test substrate | **complete (this pass)** |
| 2b-ii | C · D — digest preparation and delivery, ending in a dry-run digest | not started |
| 2b-iii | B · E · I — skip rules, review gates end to end, remaining tests, then merge | not started |

---

## What the next pass must read

`SPEC.md`, `DECISIONS.md`, this file, and only the files it will touch. Do **not** re-read
the repo. For 2b-ii that means: `app/models.py` (Digest), `app/scheduler.py`,
`app/stages/report.py`, `plugins/artec/tools_v4.py` (`_read_digest_impl`),
`app/stages/preflight.py`, `app/locks.py`, `plugins/artec/agent_runs.py`.

**Load-bearing facts for 2b-ii:**
- `digests(digest_date UNIQUE, payload jsonb, delivered_at)` already exists (migration 0003).
- `read_digest` already exists and marks `delivered_at` on first read. It is READ ONLY of
  post content; `deliver_video` is the fifteenth tool and owns video delivery + receipt.
- The digest payload must carry a **public URL** for each pending video (job 11 uploads to
  fal storage — the pattern already used for Brevo hero images) because Telegram cannot
  fetch a Drive link and the brain has no Drive client.
- `sweep_orphaned_slots(session)` in `app/scheduler.py` returns orphan-slot posts for the
  digest. Wire it into the payload.
- `agent_runs.week_to_date_spend_cents()` is the meter for SPEND & HEALTH.
- `preflight()` returns `PreflightResult(ok, checks, failures)`; parked pre-flight failures
  use `preflight_wishlist()`.

---

## Built in 2b-i

### A · Publish pre-flight — `app/stages/preflight.py`
Mandatory on every asset, blocks on failure, parks with a wishlist entry, never shows a
failed file to a human.
- **Video:** real `ffprobe` (the binary, not ffmpeg-python, so a missing ffprobe is a named
  error), leading `moov` atom, ≥1 video stream of non-zero duration, duration within the
  platform bound, aspect within 4% of target, size in a sane band.
- **Image:** `verify()` **plus a forced `load()`** — `verify()` checks headers only and a
  truncated JPEG passes it; only a full decode catches truncation. Found by testing against
  a really-truncated file.
- **Caption:** `length(rendered) == length(stored)`, closing the truncation class that
  produced "gives yo".
- **Calibration corrected by real files:** a 3s 1080×1920 solid-colour clip encodes to
  ~8 KB, so the initial 20 KB video floor would have parked legitimate short video. Floors
  are now 2 KB (video) / 1 KB (image) — they exist to catch empty and truncated files, not
  to judge compressibility.

### F · Slot validation (A7) — both guards
- **Write time:** bespoke `ideate` now raises `OperatorError` naming the bad slot and the
  valid set. The seam already did this; bespoke did not.
- **Sweep:** `sweep_orphaned_slots()` finds RENDERED, unpublished posts whose slot is not a
  key of `slot_times` — rows written before the guard existed, or orphaned by an operator
  editing `slot_times`. Reported, never deleted.

### G · `agent_runs` writes (A3) — `plugins/artec/agent_runs.py`
Zero INSERTs existed. Now `start_run` / `record_tool_call` / `finish_run` /
`week_to_date_spend_cents`. **Best-effort but logged** — observability must not be able to
take down the job it observes, and a silent write failure would read as "no spend" rather
than "no data".

### H · Advisory lock (C2) — `app/locks.py`
`pg_try_advisory_lock` keyed on a stable 63-bit hash of the job name. Session-scoped, so a
crashed replica does not leave a job permanently unrunnable. **Raises `NotPostgres` on any
other dialect rather than pretending to lock.**

### The test substrate (the §2 correction)
- `pg` pytest marker; `pg_engine` / `pg_session` fixtures; CI runs `pytest -m pg` against
  the throwaway Postgres it already stands up.
- **Safety guard:** the fixture drops `SCHEMA public CASCADE`, so it refuses any
  `TEST_DATABASE_URL` that does not look disposable and hard-fails on any hosted host
  (railway.app, rlwy.net, amazonaws, supabase). An irreversible action must not be one
  environment variable away.
- `artec doctor` gained two RED checks: **`ffprobe on PATH`** (separate binary from ffmpeg —
  "ffmpeg is green" was an inference, not evidence) and **`database is postgres`** (advisory
  locks, `post_id_seq` and jsonb are all silently void on another dialect).

---

## Test counts — SPLIT BY SUBSTRATE

| Substrate | Written | Executed | Result |
|---|---|---|---|
| SQLite | 221 | 221 | **passing** |
| Postgres (`-m pg`) | 13 | **2** | 2 passing; **11 written but NOT YET EXECUTED** |

The 2 executed pg-marked tests are the ones needing no server (`lock_key` stability, and
the assertion that advisory locks *refuse* on SQLite). The other **11 have never run** —
Docker's daemon is not running on this machine, so a real Postgres could not be started.
**They are reported as unverified, not green.** CI executes them on the pull request.

Postgres-only behaviours now covered by written tests: two-replica advisory-lock no-op,
lock release on holder disconnect, distinct jobs not blocking, sequence monotonicity,
sequence seeded past existing ids, **nextval surviving a rolled-back transaction**,
concurrent allocation without collision, `digests.payload` round-tripping as real jsonb
(queried with `->` / `->>`), concurrent `agent_runs` inserts, and metrics NULL-vs-zero.

---

## Not built (do not assume any of this exists)

B (skip rules) · C (digest preparation) · D (digest delivery) · E (review gates end to end
— the *tools* exist from 2a, the expiry sweep and live Brevo count do not) · I (remaining
tests) · everything in Stage 2c (spend cap, HTTPS mirrors, pg_dump/restore-check, price
reconciliation, `artec prove`, cron registration, DECISIONS/RUNBOOK consolidation).

**Nothing is registered with cron.** Every job body is a plain function invocable by CLI and
HTTPS. All twelve register in one pass in 2c, verified by listing, `+08:00` asserted.

---

## Failure-class statement (§5)

| Item | Class that could kill it | How it is verified |
|---|---|---|
| A pre-flight | packaging/environment — `ffprobe` absent in the container | `artec doctor` asserts **ffprobe specifically**, RED if absent; tests use the real binary, never a mock, and skip loudly when it is missing |
| A caption check | config/credential silence — none; pure logic | asserted against a really-truncated string |
| F write-time guard | third-party contract drift — the planner emitting a new slot vocabulary | rejected at write time naming the valid set; sweep catches pre-existing rows |
| G agent_runs | config/credential silence — a bad DATABASE_URL makes the meter read zero | writes are logged on failure; concurrent-insert test on real Postgres |
| H advisory lock | packaging/environment — a non-Postgres dialect silently voiding the guarantee | raises `NotPostgres`; `artec doctor` RED; two-real-connection pg test |
| post_id_seq | packaging/environment — tested on SQLite, deployed on Postgres | **this was the §2 finding**; now has 5 pg-marked tests including rollback semantics |
| Test substrate | the class itself | pg tests skip rather than fall back; counts reported split, never merged |

---

## Unsafe to deploy

Nothing on this branch is deployed, by design. If it *were* merged today it would still be
safe — A, F, G, H are additive and no publish path calls the pre-flight yet (B wires it in,
in 2b-iii). The dangerous half (skip rules) deliberately lands **last**.
