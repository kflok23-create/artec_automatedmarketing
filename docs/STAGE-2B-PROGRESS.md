# Stage 2b progress — branch `v4-stage-2b`

**Branch base:** `36fdb55` (main) · **`main` stays deployable and is NOT touched.**
**Merge gate:** this branch merges only when A–I are all green together. Nothing here
deploys, so a partially-built branch cannot strand a post.

| Pass | Scope | State |
|---|---|---|
| **2b-i** | A · F · G · H + the Postgres test substrate | **complete** |
| **2b-ii(a)** | §0 CI gate · §0.1 bitrate floor · D2 investigation | **complete** |
| **2b-ii(b)** | **C — digest preparation + the two dry-run digests** | **complete** |
| **2b-ii(c)** | **D — digest delivery (job 12 body) + five polish items from reading the dry run** | **complete** |
| **2b-iii** | **B · E · I + two corrections (circular guard, deliver_video bytes)** | **complete — merge gate is CI** |

## MERGE RULE — binding · SUPERSEDED 2c-i, see below

### THE MERGE GATE — five conditions, all required (updated after operator probes A–E)

1. **jobs 11 and 12 registered and verified BY LISTING**, `+08:00` next-run timestamps —
   end of 2c-iv, immediately before Checkpoint 1
2. every `pg` test executed and green
3. **`POST /commands/*` accepts the correct bearer** (probe A′ — still open, see below)
4. metrics entry either works against the production store layout (probe B — now
   implemented, needs one live session) **or** is consciously accepted as HTTPS-only for
   week one
5. the CI gate, per the operator's decision on branch protection (403 on this plan)

Condition 1 is the governing one and the reason the branch runs long:

Why this outranks everything below: B makes production's already-scheduled publish job skip
every email and every video-bearing post. Nothing on a clock prepares or delivers a digest
until 2c-iv, so `APPROVED_TO_SEND` would be reachable only by an operator invoking the
digest by hand every night. Merging a COMPLETE branch before its release path fires on a
clock strands every held post — the same failure this project has refused to ship since
Stage 2a, arriving from the direction nobody was watching.

The branch runs long and that has a drift cost. It is smaller than stranding every held
post for four passes.

Everything below remains required, in addition.

## The earlier rule (still required, no longer sufficient)

**`v4-stage-2b` cannot merge to `main` with any `pg` test red or unexecuted.**
The CI job *Postgres-substrate tests (advisory locks, sequences, jsonb)* is the gate.
Draft PR: kflok23-create/artec_automatedmarketing#1. A CI job that runs but does not gate
is a job that will eventually be ignored — **make this a required status check on `main`
in GitHub branch protection**.

**BLOCKED, and not by me.** `GET /repos/.../branches/main/protection` returns
**HTTP 403: "Upgrade to GitHub Pro or make this repository public to enable this
feature."** Branch protection is unavailable on a private repo on the free plan, so the
second half of the merge condition — "every pg test green AND the CI check required" —
**cannot currently be satisfied by anyone**. Three ways out, operator's call:
  1. GitHub Pro on this account (protection becomes available; nothing else changes);
  2. make the repo public (NO — it carries the marketing plan and infrastructure layout);
  3. accept a written manual gate: **no merge to `main` without a green CI run on the
     merging commit, checked by hand.** Weaker, and it should be written down as the
     deliberate exception it is rather than left as an assumption.

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
- ~~The digest payload must carry a public URL for each pending video.~~ **WITHDRAWN in
  2b-iii.** A URL — fal's or Drive's — means the operator approves one artefact while
  publish streams another, and it makes Telegram's rejection a check on somebody else's
  bytes. `deliver_video` now uploads the publish bytes multipart, read from the app's
  authenticated `GET /commands/media/{post_id}`. See DECISIONS.md #40.
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

### Counts reconciled (they did not add up before — 273 + 13 = 286, reported as 285)

`pytestmark = pytest.mark.pg` marked the WHOLE substrate file, including two tests that
need no database: `test_lock_keys_are_stable_and_distinct` (pure hashing) and
`test_advisory_lock_refuses_on_non_postgres` (which asserts SQLite behaviour on purpose).
The SQLite job therefore ran them too, and they were counted on both sides. The marker is
now per-test.

| Substrate | Tests | Executed | Result |
|---|---|---|---|
| non-pg | **318** | 318 | passing |
| pg (real Postgres required) | **11** | 11 in CI | passing |
| **total** | **329** | | no test counted twice |

`pytest` with no database: 318 passed, 11 skipped. `pytest -m pg` with Postgres: 11
selected, 11 executed.

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

## Built in 2b-ii — C · D

### C · Digest preparation — `app/stages/digest.py` (job 11 body)
Five sections in reading order; idempotent on `digest_date`; the live Brevo count read at
preparation time and reported UNAVAILABLE, never 0. `assert_complete()` is the
safety-critical guard — a post that changes state and appears nowhere is invisible to the
operator permanently — and its warning surfaces **inside** the digest, not only in a log
line. `artec digest-prepare --date --show` and `POST /commands/digest-prepare`.

**Two defects the dry run exposed that the unit tests did not:** `expires in Noned` (an
unstarted review has the FULL window left, not an unknown one), and
`system CAC: SGD=49.66, MYR=49.66` — the same USD spend divided by each currency's order
count separately, presented as two currency-specific figures. Replaced by one honest
number, production cost per attributed order, with the per-currency counts alongside,
uncombined.

### D · Digest delivery — job 12 body
`deploy/hermes-brain/cron-nightly-digest.txt` (not registered with cron). Three things the
prompt asks for are enforced in code instead, because a prompt is a request and a refusal
is a property:
- **Sunday.** `read_digest` returns `deliver: false` with the reason on a Sunday in SGT and
  hands over no payload. The cron expression will say so too — but it is one edit away from
  being wrong.
- **The transport split.** `prepare_digest` stores a `messages` list already split on
  SECTION boundaries under Telegram's 4096-character limit; the brain sends them verbatim.
  NEEDS YOU is first by construction. A section too large for one message splits at item
  boundaries, never mid-line, marked `(continued)`.
- **The metrics echo.** `record_metrics` writes NOTHING without `confirm: true`; the first
  call returns an echo of exactly what would be recorded and what stays NULL. A single
  ordered line is accepted; an empty position is unmeasured, never zero; a thousands
  separator is refused rather than guessed at.

### Five polish items from reading the dry run as the operator
Money renders in currency (`RM212.00`, `S$74.00`) with the integer arithmetic untouched ·
spend is shown against its own denominator (last render run vs the per-run cap; week to
date separately, and it says so when there was only one run) · the price-table staleness
line is emitted every night in every state · orphaned-slot posts confirmed wired and now
visible in the dry run · `agent_weekly_cap_minor` 500 → 1500 with a `SUPERSEDED_DEFAULTS`
upgrade path that corrects a stale shipped default without ever touching an operator value.

---

## Open operator items carried into the next pass

| Item | State |
|---|---|
| `which ffprobe && ffprobe -version` on the deployed `artec api` | **outstanding** — the packaging risk against A is still open. A is not wired into any publish path (B does that, in 2b-iii), so nothing is blocked yet |
| Delete `BRAVE_API_KEY`, set `TAVILY_API_KEY` on artec-brain | **outstanding** — scouting stays unavailable and the digest will report it nightly |
| Make CI `test` a required status check on `main` | **outstanding** — see the merge rule above |
| Delete `RESTORE_TARGET_URL` from artec-brain | **outstanding** — dead config, see D2 below |
| **A live delivery of the digest over Telegram** | **blocked until this branch deploys** — the brain holds the only Telegram credentials and nothing on this branch is deployed. Job 12 is proven to the wire (`sendVideo` request asserted, receipt recorded) but has never reached a real chat |

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

Everything in Stage 2c (spend cap, HTTPS mirrors, pg_dump/restore-check, price
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


---

## Built in 2b-iii — B · E · I, and two corrections

### The transcription guard was circular (DECISIONS #39)
The hook compared the agent's `figures` against the agent's own `operator_message`
argument. Demonstrated live against `06d3d79`: a call carrying figures the operator never
sent returned `None` — permitted. It now reads hermes-agent's session transcript, which the
agent does not author, and **fails closed** when no transcript can be read.

**Operator item:** the transcript store layout is discovered at runtime and is NOT verified
against a live hermes-agent. Until it is, agent-relayed metrics will refuse and name
`artec measure` as the fallback. Verify with a real session after deploy:
`ls $HERMES_HOME/sessions` on artec-brain, then one `record_metrics` through the digest.

### `deliver_video` was showing the wrong file (DECISIONS #40)
Fixed to multipart bytes with a sha256 match against what publish streams.
**Operator item:** set `ARTEC_API_BASE` and `HERMES_API_TOKEN` on artec-brain, or
`deliver_video` refuses and says so — it never falls back to a URL.

### B · the two skip rules — `app/stages/publish.py`
`skip_reason()` is pure and evaluated for every post on every pass. Pre-flight is wired in,
blocking, before the upload. `APPROVED_TO_SEND` enters `select_due_posts` and nowhere
earlier, so an approval waits for the next occurrence of its slot.

### E · the gates end to end — `sweep_expired_reviews`
Both surfaces, 3-day windows measured from presentation/delivery, PARK with the reason.
No auto-approve and no expire-to-send exists to be requested. `artec sweep-reviews` and
`POST /commands/sweep-reviews`; registered with no cron.

### A finding the completeness assertion caught
§B made `APPROVED_TO_SEND` a RESTING state — a post sits there up to a day — and it
appeared in NO section of the digest. `assert_complete` failed, which is exactly what it is
for. Added as `queued` in WENT OUT TODAY: "⏳ approved, goes out at the next <slot> slot".


---

## Why 2b-iii did NOT merge

Two conditions were set: every `pg` test executed and green, **and** the CI check required.

  * pg: **11 executed, 11 green** in CI run 30859841222 at `e4909b1`. Satisfied.
  * required check: **impossible on this plan** — see the 403 above. Not satisfied.

Two deployment preconditions also stand, and merging before they are met would ship a
system where two paths refuse in production:

| Precondition | Until it is done |
|---|---|
| `ARTEC_API_BASE` + `HERMES_API_TOKEN` on artec-brain | `deliver_video` refuses and says so — it never falls back to a URL, so no video review can complete |
| Verify the hermes-agent transcript store path on a real session | agent-relayed `record_metrics` refuses and names `artec measure` — fails closed, loudly, by design |

Neither is a bug; both are the fail-closed behaviour working. But merging with them open
means the Monday digest has two dead paths, and that is a worse outcome than waiting.


---

## 2c-i — spend, search, memory, and three preconditions

| Item | State |
|---|---|
| Transcript store probed | **done** — `$HERMES_HOME/state.db`, see VERIFY.md. The heuristic did NOT match and was wrong permissively; fixed by narrowing to one source, not widening |
| `ARTEC_API_BASE` on artec-brain | **set** — `http://artecautomatedmarketing.railway.internal:8080` (private networking; port probed from artec api's deploy log). `skipDeploys` — it applies at the next deploy |
| Media route hardened | **done** — post key only, `_generated/` enforced, no listing, traversal parametrised |
| A6·2 spend posture | **done** — scouting first, gate conversation second, gate never |
| A5 Tavily probe | **written + unit-tested; REAL RESULT NOT OBTAINED** — see below |
| A4 memory/skills config + audit | **done** — real key names; audit runs on the brain at boot, digest renders it |
| C6 toolset drift | **done** — `artec agent-review` is RED if a disabled identifier vanishes |

### Why the Tavily probe has no real result yet

Both `artec api` and `artec-brain` deploy from **`main`** (confirmed via Railway service
config). The probe lives on this branch, so the brain cannot run it without the merge —
and the merge is now gated behind cron registration. `TAVILY_API_KEY` is set on
artec-brain only, and reading its value into a chat or a file is forbidden, so it cannot be
probed from here either.

**It resolves itself:** the probe runs in the brain's boot (step 7/10) on the first deploy
after merge, and its real result lands in `config.scouting_status`, which the digest prints
every night. Until then the digest correctly says scouting is UNAVAILABLE.

Operator alternative, if you want the answer sooner — one command, key never printed:

```bash
railway run --service artec-brain python deploy/hermes-brain/probe_scouting.py
```


---

## 2c-i(b) — operator probes A–E applied

| Probe | Outcome |
|---|---|
| **A** internal networking | WORKS over IPv6 against `--host 0.0.0.0`. No start-command change. Hardened: `_publish_bytes` names the attempted URL, so a PORT change cannot present as a mute refusal |
| **A′** 401 on `POST /commands/*` | **NOT REPRODUCIBLE locally** — see below. Fixed a different live bug on the same route; added the over-HTTP acceptance suite and a self-naming 401 log |
| **B** session store | Profile-scoped; the obvious path is a decoy. Module rewritten, tests re-run against the production layout |
| **B′** tool posture | C6 satisfied by observation. Entrypoint now counts the fifteen artec tools by listing |
| **B″** profile config.yaml | Already restored from the repo at every boot (step 5/10); `agent-review` now also diffs live vs committed |
| **C** Tavily | LIVE. Three-state rendering added — absent is NOT YET PROBED, never passing |
| **D** `RESTORE_TARGET_URL` | Deleted. Closed |
| **E** real video fixture | Wired in at 1.803 bits/pixel-second, paired with the solid-colour clip that fails on bitrate alone |

### A′ — what I found, and what I could not

**Could not reproduce.** Driven over HTTP against the real app: `GET` → 405, `POST` without
a header → 401, `POST` with the correct bearer → **past auth** into the route body. The
dependency, the router-level dependency, the CORS middleware and the routing all behave.

**Found instead:** `POST /commands/doctor` raised `KeyError: 'endpoint_prices_cents'` on
`main` — a live bug on the exact route being probed, on every call, CLI and HTTPS alike.
Fixed (DECISIONS #53). It does not explain a 401, but that route could not have succeeded
regardless.

**The next probe is now one request.** A 401 logs which failure it was, with `sha256[:8]`
of the presented and expected tokens. If the fingerprints differ, the running process holds
a different value from the one on disk; if they match, the failure is upstream of the app.
Either way it stops being guesswork:

```bash
railway logs --service "artec api" | grep "401 on an authenticated route"
```
