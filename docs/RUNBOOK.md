# ARTEC RUNBOOK

The operating manual for HERMES v4. One file, because an operator under pressure reads one
file.

**Every capability claim below carries its evidence class. A claim without one is a defect
in this document.**

| class | means |
|---|---|
| **P** | proven in production — `artec prove` recorded a dated pass against the live system |
| **A** | armed — built, deployed, never exercised |
| **T** | covered by tests only |
| **U** | unproven — neither exercised nor tested end to end |

---

## 1 · THE CONTRACT — seven touches

| touch | when | what | budget |
|---|---|---|---|
| 1 | SUN 09:00 | the weekly gate: approve / edit / reject / inject each draft | ~45 min |
| 2–7 | MON–SAT 21:00 | asset drop + digest, replies relayed through the agent | ≤15 min each |

**If you are doing anything else on a normal week, the system has a defect.** Not a
preference — a defect. Chasing a post, checking whether something published, re-entering a
number, wondering whether the digest ran: each is a thing this build was meant to remove.
Report it rather than absorbing it.

Sunday has no evening digest. The gate is that day's touch. `read_digest` returns
`deliver: false` on a Sunday **in the body**, not only in the cron expression **(T)**.

---

## 2 · THE TWELVE JOBS

`app/jobs.py` is the schedule — not a document about the schedule, the schedule itself. A
repo test fails on a thirteenth **(T)**.

| # | job | when | runtime | reads | writes | failure looks like |
|---|---|---|---|---|---|---|
| 1 | report | SUN 06:00 | scheduler | posts, orders, metrics, endpoint_prices | runs | no weekly report; digest still runs |
| 2 | bespoke learn + ideate | SUN 06:30 | scheduler | v_brief, learnings | posts (DRAFT), learnings | **plan-diff has one side and agrees with itself** |
| 3 | agent LEARN → IDEATE | SUN 07:00 | brain cron | brief, learnings, inventory | plans_shadow | plan-diff has one side |
| 4 | plan-diff | SUN 08:00 | scheduler | posts, plans_shadow | runs | the gate has nothing to compare |
| 5 | weekly gate | SUN 09:00 | brain cron | draft posts | posts (gate_action) | **Touch 1 does not happen** |
| 6 | render all approved | SUN 10:00, retry MON 10:00 | scheduler | posts (APPROVED), assets | posts (RENDERED), Drive `_generated/` | nothing to publish all week |
| 7 | publish by slot | daily, per `slot_times` | scheduler | posts (RENDERED, APPROVED_TO_SEND) | posts (PUBLISHED) | posts sit unpublished; digest shows them still RENDERED |
| 8 | pg_dump → Drive `_backups/` | daily 03:00 | scheduler | whole database | Drive `_backups/` | digest SPEND & HEALTH; no backup |
| 9 | assets sync + wishlist match | daily 20:30 | scheduler | Drive bank | assets, posts | tonight's wishlist is yesterday's |
| 10 | doctor sweep | daily 20:40 | scheduler | everything | config.last_doctor | **digest carries no RED lines — they become invisible** |
| 11 | digest preparation | daily 20:55 | scheduler | posts, orders, metrics, runs | digests | brain reads an empty row at 21:00 |
| 12 | DIGEST DELIVERY | MON–SAT 21:00 | brain cron | digests | posts (video_review) | no digest, and nothing else says so |

**The nightly chain is a sequence, not four times.** 20:30 → 20:40 → 20:55 → 21:00. Assets
sync so tonight's wishlist reflects last night's drop; doctor so its RED lines exist to be
carried; preparation so the payload is written before the brain reads it. Preparing at 21:00
races delivery, and the failure mode is an empty digest on a night something needed you.

**Job 12 does not run on Sunday.** The gate is that day's touch, and a second Telegram session
the same evening spends your attention twice.

**Job 7 has no fixed time** — it fires per `slot_times` entry. That makes it the one job that
appears in no next-run listing, so it is verified differently: present in the registry, has a
dispatch, and a selection test over a **non-empty** board **(T)**. See §10, defect 6.

---

## 3 · THE DIGEST

Five sections, in delivery order, split on section boundaries under Telegram's
4096-character limit — never mid-section **(T)**.

**NEEDS YOU is first because it is the only blocking section.** Everything after it is
information. An empty NEEDS YOU is one line, and then it stops:

```
HERMES · 2026-08-27
━━ 1 · NEEDS YOU ━━
Nothing needs you tonight. 👍
```

Otherwise, worked:

```
━━ 1 · NEEDS YOU ━━
🎬 VIDEO REVIEW — post_1502 · tiktok · slot evening
   Two blocks, four seconds, and the moment it clicks.
   expires in 3d · approve / reject / rerender
✉️  EMAIL REVIEW — post_1503 · slot morning · list 3: 1 recipients
   subject:  The 10-minute focus builder
   cta:      Get S$10 off
   expires in 3d · approve / edit / reject / test send
📊 METRICS — 2 unmeasured: post_1497, post_1498
🔁 RETRY — post_1499 · youtube · upload-post 429: rate limited
📦 PARKED — post_1485 · needs raw-video/assembled
🚨 DOCTOR RED — google drive bank: _generated write probe: permission denied
⚠️  ORPHAN SLOT — post_1504: slot 'afternoon' is not in slot_times

━━ 2 · WENT OUT TODAY ━━
✅ post_1496 · instagram · morning · up_1496
⏳ post_1505 · linkedin — approved, goes out at the next morning slot

━━ 3 · TONIGHT'S ASSET DROP ━━
📁 raw-photo/child-face/
   post_1501: child mid-build, face visible, natural light

━━ 4 · NUMBERS ━━
REVENUE (orders only)
   MYR: 1 order · net CM RM212.00
   SGD: 1 order · net CM S$74.00
   unattributed: 1
ENGAGEMENT (events + metrics only)
   post_1496: impressions=4200, saves=45, clicks=118
   unmeasured (not zero): post_1497, post_1498

━━ 5 · SPEND & HEALTH ━━
fal · last render run (2026-08-09 10:04): US$0.06 · run cap US$2.50
fal · week to date: US$0.12 across 2 render runs (no weekly fal cap — the cap is per run)
agent · week to date: US$0.31 · weekly cap US$15.00
production cost per attributed order (health only, never a kill rule): US$0.25
brevo list 3: 1 recipients — below measurement threshold
price table: seeded 2026-08-03, never reconciled against fal
agent memory audit: clean (2 files, 2026-08-09)
unproven capabilities (9): …  ·  S1 UNPROVEN: video-pipeline, publish-by-slot
scouting: NOT YET PROBED — an absent result is not a passing one
```

Money renders in currency; minor units are the storage invariant and never appear here.
Unmeasured is labelled unmeasured, never zero. Revenue and engagement never combine.

---

## 4 · THE TWO HELD SURFACES

Neither auto-publishes. **No configuration value and no code path can exempt either** — the
gate is the absence of a route, not a flag that happens to be off **(T)**.

### Email

Held because **a Brevo campaign to list 3 is the only surface in this system with no
remedy.** A bad social post can be deleted; a sent email has arrived.

- machine gate: render → `status=RENDERED`, presented in the digest
- human gate: `review_email` records the decision
- decisions: **approve** → `APPROVED_TO_SEND`, sends at the next occurrence of its slot,
  never immediately · **edit** → overwrites any of the seven Brevo variables, re-renders,
  re-presented next digest, nothing sent · **reject** → PARKED with the reason ·
  **test send** → to your own address, status unchanged
- expiry: **3 days** (`email_review_expiry_days`) → PARKED as *review expired*. There is no
  auto-approve and no expire-to-send, under any framing
- publish additionally requires `email_review.decision == "approve"`. Status alone is not the
  gate: a status is reachable by routes the review never took; a receipt is not

### Video

Held because **the risk is per-render, not per-pipeline.** A pipeline that worked yesterday
says nothing about today's file. `video_pipeline_proven` is **deleted** and is not coming
back — it was a boolean that would have let one good render vouch for every later one.

- machine gate: publish pre-flight — ffprobe, leading moov atom, duration and aspect in
  bounds, bitrate floor 0.05 bits/pixel-second **(T)**
- human gate: `deliver_video` uploads the **publish bytes** multipart to Telegram so you watch
  the exact artefact that ships, and records the `message_id`. `review_video` refuses without
  that receipt **(T)**
- decisions: **approve** → `APPROVED_TO_SEND` · **reject** → PARKED with the reason ·
  **rerender** → back to APPROVED with your reason as toolbox guidance
- expiry: **3 days** (`video_review_expiry_days`) → PARKED
- Telegram refusing the upload PARKs the post. That refusal is independent evidence the file
  is malformed and more trustworthy than our own pre-flight — which is only true because the
  bytes are ours

---

## 5 · THE TRANSCRIBER INVARIANT

**The agent may move a number you typed. It may not originate one.**

It may not: compute · estimate · infer · round · average · interpolate · carry forward —
including "same as last week", and **including arithmetic you asked for**. Ask it to add
Monday's 300 to Tuesday's 400 and it refuses and says why. The number that reaches the
database must be one you typed, because everything the loop learns is built on it.

Four enforcement mechanisms:

1. **`pre_tool_call` hook, transcript-backed** — every digit must appear in some message *you*
   sent this session, read from hermes-agent's own message store, which the agent does not
   author. Fails closed: no readable transcript → refuse, with `artec measure` named as the
   fallback **(T)**
2. **`operator_message` must itself be one of your turns** — an invented utterance appears in
   no turn **(T)**
3. **`confirm: true`** — the first call writes nothing and returns an echo of exactly what
   would be recorded and what stays NULL **(T)**
4. **capability absence** — no tool writes `orders`, `events` or `config`; `metrics` is
   writable by transcription only **(T)**

An empty position is unmeasured, never zero. A thousands separator is refused rather than
parsed: `4,200` would become two positions and shift every later figure into the wrong
column, which is worse than no reading at all.

---

## 6 · RECOVERY — every command over authenticated HTTPS

`$HERMES_API_TOKEN` is referenced by name and never expanded. `$ARTEC` is the base URL.

```bash
curl -sS -X POST "$ARTEC/commands/doctor"          -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -sS -X POST "$ARTEC/commands/report"          -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{}'
curl -sS -X POST "$ARTEC/commands/learn"           -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{}'
curl -sS -X POST "$ARTEC/commands/ideate"          -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{}'
curl -sS -X POST "$ARTEC/commands/plan-diff"       -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{"week":"2026-08-24"}'
curl -sS -X POST "$ARTEC/commands/render"          -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{"all_approved":true}'
curl -sS -X POST "$ARTEC/commands/publish-slot"    -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{"post_id":"evening"}'
curl -sS -X POST "$ARTEC/commands/assets-sync"     -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{}'
curl -sS -X POST "$ARTEC/commands/digest-prepare"  -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{}'
curl -sS -X POST "$ARTEC/commands/sweep-reviews"   -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -sS -X POST "$ARTEC/commands/backup"          -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -sS -X POST "$ARTEC/commands/measure"         -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{"rows":[]}'
curl -sS -X POST "$ARTEC/commands/prove"           -H "Authorization: Bearer $HERMES_API_TOKEN" -d '{"post_id":"budget-refusal"}'
```

**Railway shell fallback.** The artec api runtime is **`/opt/venv/bin/python`**, not the
system Python — `python` at an SSH prompt has no pydantic, and every diagnostic run that way
is meaningless:

```bash
/opt/venv/bin/python -m app.cli doctor
/opt/venv/bin/python -m app.cli digest-prepare --show
```

---

## 7 · ROLLBACK

One config row. **Never a redeploy.**

```bash
/opt/venv/bin/python -m app.cli config set plan_source '"bespoke"'
```

`plan_source` ∈ `shadow` (current) | `bespoke` (full rollback) | `agent`. It stays on
`shadow` until you decide otherwise. Nothing in this build flips it.

---

## 8 · THE NINE PROOFS

`artec prove <capability>`. Doctor reports any proof absent or older than **90 days** as
YELLOW; the digest lists the unproven weekly until each is green.

| capability | exercises | a pass looks like | class |
|---|---|---|---|
| agent-session | the brain's message store is readable and holds sessions | *N session(s), M operator turn(s)* | **U** |
| sunday-cron | `hermes cron list` shows the three, `+08:00` | *registered: [...]* | **U** |
| **video-pipeline** (S1) | a real encode through real ffprobe pre-flight | *real encode passed publish pre-flight* | **T** |
| **publish-by-slot** (S1) | the slot pass SELECTS over a non-empty board | *N would publish, M held by a review gate* | **U** |
| brevo-send | template contract, substitution, campaign create + delete. Never `sendNow` | *contract proven; campaign N created and deleted* | **U** |
| stripe-attribution | the webhook joins `client_reference_id` to a post | *joined to the post* | **T** |
| budget-refusal | oversize refused before the call | *refused; one 1080×1920 = 62,208 micros* | **T** |
| audit-memory | the audit runs where the memory is | *memory clean* | **U** |
| restore | the dump restores into a scratch database | *restored and verified across 8 tables* | **U** |

`--live` on `brevo-send` is refused: the single live send is a deliberate operator action.

**`NotProvable` is a third state**, distinct from failure. Recording a skip as a proof is how
a capability comes to be believed without ever having run.

---

## 9 · BACKUP AND RESTORE

Job 8, daily 03:00, `pg_dump --format=custom` → Drive `_backups/` **(T)**. Custom format
because that is what `pg_restore` consumes, and therefore what the restore check proves.

`artec restore-check` rides job 8 on the **first of the month**. It creates a scratch
DATABASE — never a scratch schema, because a schema-scoped restore rewrites the dump's
qualification and would verify a different artefact from the one an incident uses. Free disk
is checked before creating. `CREATE DATABASE` denied is **RED with the reason**, never a
degradation. Row counts are compared across eight tables including `config` and
`endpoint_prices`. The scratch database is dropped in a `finally`.

**Restore had never been exercised before this build.** Retention is 14 days.

---

## 10 · THE STANDING REVIEW QUESTION

> **For every guard, name what supplies each side of the comparison. If the thing under test
> supplies either side, or one side can be absent while the check still passes, it is not a
> guard.**

Two corollaries:

- **A check aimed at the wrong thing is worse than a missing one, because it reports green.**
- **Internal consistency cannot catch a wrong premise.** A test and its implementation can
  agree perfectly and both be wrong.

Seven instances in this build:

| # | guard | what was missing | how it was caught |
|---|---|---|---|
| 1 | transcription hook | the agent supplied both the figures AND the message they had to appear in | operator read the confirm flow and asked what "immediately preceding" meant after "yes" |
| 2 | advisory-lock test | `StaticPool` gave both "replicas" one session; a re-entrant lock looked like a working guard | CI, because the assertion was inverted |
| 3 | memory audit | patterns aimed at numbers while the danger was capability claims that had gone false | reading the live system prompt |
| 4 | CI gate | matched "runs exist for this branch", not "a run exists for this commit" | the counts did not reconcile |
| 5 | the fix for #4 | `pull_requests[0].head.sha` is the PR's *current* head, so every run claimed the newest commit | probing the real API instead of trusting the diagnosis |
| 6 | `publish-by-slot` proof | reported PROVEN over an empty board — "the pass ran" mistaken for "the pass selects correctly" | asking what would make each proof pass without demonstrating the thing |
| 7 | **job 2 dispatch** | job 2 had no dispatch, so plan-diff at SUN 08:00 would compare one plan against nothing and report agreement with itself | **a test asserting a structural property — *every timed job has a dispatch* — not a test of plan-diff** |

**#7 is the shape of guard that works: assert the property from OUTSIDE the thing.** A test of
plan-diff would have passed, because plan-diff was correct. What was absent was its input, and
only a check standing outside both could see that.

---

## 11 · THE THREE FAILURE CLASSES

| class | counter |
|---|---|
| **packaging / environment** | verify from the **deployed artefact**, not the source tree. Fonts and prompts both reached production missing because they resolved from the repo root |
| **third-party contract drift** | verify **by listing or probing**, never by exit code. `cron create` rejects `SUN` while exiting 0 |
| **config / credential silence** | probe against the **real endpoint at boot**. A key that passes a presence check and 401s at first use has already cost this project a cycle |

**Extension: the class covers PROBED FACTS, not just code.** A fact probed on a laptop is not
a fact about the container. The session store was recorded as `$HERMES_HOME/state.db` from a
development machine; in production it is `$HERMES_HOME/profiles/<active_profile>/state.db`,
and the laptop path exists there as a 1 MiB decoy that opens cleanly and answers *no such
table: sessions* — a plausible error rather than an absence.

---

## 12 · DO NOT PROCEED IF

- a red doctor line has a **workaround** instead of a fix
- `plan_source` has moved off `shadow` without a decision
- any tool can write `orders`, `events` or `config`
- a **thirteenth job** appears
- any path lets email or video skip review
- the agent **computes** a number

---

## 13 · POST-DEPLOY OPERATOR SEQUENCE

1. **Deploy** — merging to `main` triggers Railway on all four services.
2. **Delete `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`** from `artec api` and
   `artec-scheduler`. **After the deploy, never before** — pydantic fails fast on a missing
   required variable, so deleting while the old manifest is live stops `artec api` booting.
   After deploy, doctor on those services goes **RED if a token is still present**.
3. **Both doctors green** — `artec doctor` and `hermes doctor`.
4. **The nine proofs** — `artec prove <capability>` for each.
5. **The full-loop dry run.**

**Restoring bespoke Telegram access is a deliberate act**, not a convenience: set the
variable, redeploy, and **confirm the brain gateway is stopped first**. Two pollers on one
token is a 409 that breaks the live gate.

---

## 14 · THE MERGE RULE

> **No merge to `main` without a green CI run on the exact commit being merged — every `pg`
> test EXECUTED, not skipped.**

Enforced: `main` requires the `ci` status check and branches must be up to date before
merging. `artec agent-review` also checks after the fact and reports RED if `main`'s HEAD
carries no green run — including the case that matters most, a commit CI never ran against.

**The merge commit names the CI run id that was green.** A merge without one is self-evidently
outside the rule.

```bash
gh run list --branch v4-stage-2b --limit 1 --json databaseId,headSha,conclusion
gh run view <run-id> --log | grep "selected"      # expect "N selected", never "N skipped"
git merge --no-ff v4-stage-2b -m "merge v4-stage-2b (CI run <id> green on <sha>)"
```

**A run on any other sha is not evidence.** Two defects have lived here, and both reported
green.

---

## 15 · WHAT THE AGENT DISCOVERS MUST REACH THE GAP REGISTER

The agent found a real defect in production — `read_brief` under-reporting the parked backlog
— and wrote it into its own memory. It never reached the gap register, so it survived four
passes of review by people who do not read agent memory.

**Memory is not a defect tracker.** Anything the agent discovers about the system needs a path
out of memory and into the register. The weekly memory audit surfaces capability claims and
imperatives; a defect note is neither, so moving it is the reader's job.
