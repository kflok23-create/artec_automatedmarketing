# Stage 2b progress — branch `v4-stage-2b`

**Branch base:** `36fdb55` (main) · **`main` stays deployable and is NOT touched.**
**Merge gate:** this branch merges only when A–I are all green together. Nothing here
deploys, so a partially-built branch cannot strand a post.

| Pass | Scope | State |
|---|---|---|
| **2b-i** | A · F · G · H + the Postgres test substrate | **complete** |
| **2b-ii(a)** | §0 CI gate · §0.1 bitrate floor · D2 investigation | **complete** |
| 2b-ii(b) | **C · D — digest preparation and delivery, ending in a dry-run digest** | **NOT STARTED — next pass** |
| 2b-iii | B · E · I — skip rules, review gates end to end, remaining tests, then merge | not started |

## MERGE RULE — binding

**`v4-stage-2b` cannot merge to `main` with any `pg` test red or unexecuted.**
The CI job *Postgres-substrate tests (advisory locks, sequences, jsonb)* is the gate.
Draft PR: kflok23-create/artec_automatedmarketing#1. A CI job that runs but does not gate
is a job that will eventually be ignored — **make this a required status check on `main`
in GitHub branch protection** (repo Settings → Branches → add rule for `main` → require
status check `test`). That is an operator action; I cannot set branch protection.

**CI status at `1fa6553`: fully green.**
- SQLite substrate: **224 passed**, 12 skipped (ffmpeg-dependent locally, present in CI)
- Postgres substrate: **13 passed, 0 skipped — all executed, none written-but-unverified**

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

## Test counts — SPLIT BY SUBSTRATE (CI run 30830353317, `1fa6553`)

| Substrate | Written | Executed | Result |
|---|---|---|---|
| SQLite | 224 | 224 | **passing** |
| Postgres (`-m pg`) | 13 | **13** | **passing — all executed in CI** |

### What CI caught that local testing could not

**The two-replica advisory-lock test was meaningless and passed for the wrong reason.**
The fixture used `poolclass=StaticPool` (copied from the SQLite fixture, where in-memory
databases require it). StaticPool hands every `connect()` the *same* underlying
connection, and Postgres advisory locks are session-scoped and **re-entrant** — so
"replica B" was replica A locking twice, which correctly returns True. The test asserted
False and failed, which is how it was found.

Fixed by using a real pool, and hardened so it cannot silently hollow out again: the test
now asserts the two connections report different `pg_backend_pid()` before testing the
lock at all. **This is the single strongest argument for the pg substrate** — the test had
been "written and passing" against SQLite semantics and was verifying nothing.

Second CI-only finding: the pg fixture drops and recreates the public schema, so
`alembic upgrade head` must run **before** the pg tests or it collides with tables that
have no `alembic_version`. CI step order now enforces this.

Postgres-only behaviours now covered by written tests: two-replica advisory-lock no-op,
lock release on holder disconnect, distinct jobs not blocking, sequence monotonicity,
sequence seeded past existing ids, **nextval surviving a rolled-back transaction**,
concurrent allocation without collision, `digests.payload` round-tripping as real jsonb
(queried with `->` / `->>`), concurrent `agent_runs` inserts, and metrics NULL-vs-zero.

---

## Open operator items carried into the next pass

| Item | State |
|---|---|
| `which ffprobe && ffprobe -version` on the deployed `artec api` | **outstanding** — the packaging risk against A is still open. A is not wired into any publish path (B does that, in 2b-iii), so nothing is blocked yet |
| Delete `BRAVE_API_KEY`, set `TAVILY_API_KEY` on artec-brain | **outstanding** — scouting stays unavailable and the digest will report it nightly |
| Make CI `test` a required status check on `main` | **outstanding** — see the merge rule above |

### D2 · `RESTORE_TARGET_URL` — investigated, findings

**Nothing in the repo reads it. Zero matches** for `RESTORE_TARGET_URL` across all source,
config, workflows and docs. There is no call site to name.

It is set on `artec-brain` only (not on `artec api`, `artec-scheduler`, or Postgres, per
the variable listings taken during v3 verification). It is therefore a live variable that
no code reads — the config-silence class sitting in the open — and the correct action is
to **delete it**, not to leave it as a puzzle. I have not touched it, per instruction.

It will **not** be used as the restore target regardless. The agreed design stands: a
uniquely-named scratch DATABASE created and dropped by `restore-check` itself, free-disk
check first, RED rather than a schema-restore fallback if `CREATE DATABASE` is denied. The
`_assert_disposable` guard already written for `TEST_DATABASE_URL` will be applied to any
restore-target resolution, and the production `DATABASE_URL` rejected under any alias.

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
