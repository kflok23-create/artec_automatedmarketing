# VERIFY.md — hermes-agent contract verification for v4

Every claim below was **probed**, not assumed. Method is stated per item. Where the v4 prompt
asserted something that does not verify, the finding is recorded here and the prompt's
instruction is NOT implemented against — per §7·A4 ("if a key does not exist, record that in
DECISIONS.md rather than writing a key that silently does nothing").

Probe environment: hermes-agent **v0.18.2 (2026.7.7.2)** installed locally + the published
docs. Pinned production tag is **v2026.7.30 (0.19.1)**. Where the two could differ, the
mitigation is stated. Verification against the pinned tag itself happens at brain boot
(`hermes doctor`, `hermes tools --summary`, `hermes cron list`) and any drift fails RED.

---

## V1 — The `learning:` config block does NOT exist ❌ (gap A4)

**Probe:** `hermes config show` on a clean `HERMES_HOME`; docs
`/docs/user-guide/features/memory` and `/docs/user-guide/features/skills`.

**Finding:** there is no `learning:` block and no keys named `playbook_memory`,
`gate_taste`, `skill_creation`, or `trend_scouting`. Those four names were invented by the
v3 prompt. Writing them would have been four keys that silently do nothing — exactly the
failure this project keeps hitting.

**The four intended behaviours map onto real, verified keys:**

| Intended v3 "learning feature" | Real mechanism | Verified key(s) |
|---|---|---|
| 1. Creative playbook memory | the memory system | `memory.memory_enabled: true`, `memory.write_approval: false` (false = write freely, incl. background self-improvement review) |
| 2. Gate-taste learning from approve/edit/reject | **no config toggle exists** — it is emergent behaviour of the memory system, fed by what the gate conversation puts into memory. Our durable record of taste is `posts.gate_action` (edit deltas), which is ours, not the agent's | none (by design) |
| 3. Autonomous skill creation | the skills system | `skills.write_approval: false` (false = write freely, default), `skills.guard_agent_created: true` (content scan for dangerous patterns in agent-authored skills) |
| 4. Trend scouting | the `web` toolset — **see V4, it is blocked** | toolset `web`, tool `web_search` |

Additional verified keys used: `memory.memory_char_limit`, `memory.user_profile_enabled`.

**Implemented:** a `memory:` and `skills:` block with the verified keys. No `learning:` block
is written. Recorded in DECISIONS.md.

---

## V2 — Cron declaration syntax ✅ (verified empirically, twice)

**Probe:** `hermes cron create --help`, then live creates against a scratch `HERMES_HOME`.

```
hermes cron create [--name NAME] [--deliver DELIVER] [--repeat REPEAT]
                   [--skill SKILLS] [--script SCRIPT] [--no-agent] [--workdir WORKDIR]
                   schedule [prompt]
```

**Two findings that matter, both already cost this project a silent failure:**

1. **Day names are rejected.** `hermes cron create "0 7 * * SUN" …` →
   `Failed to create job: Invalid schedule '0 7 * * SUN'`. Numeric day-of-week only —
   Sunday is `0`. Accepted forms: duration (`30m`), interval (`every 2h`), cron
   (`0 9 * * *`), timestamp.
2. **`cron create` EXITS 0 ON FAILURE.** The rejected `SUN` create above returned exit code
   **0**. Under `set -e` this sails past silently — which is precisely how v3 shipped with
   zero registered cron jobs while the boot log looked clean.

**Implemented:** numeric schedules everywhere; every registration is **verified by listing**
(`hermes cron list` for the brain, an in-process registry dump for the scheduler) and the boot
hard-fails if a job is absent. Exit code is never treated as evidence. Next-run timestamps are
asserted to carry `+08:00`.

**Also verified:** `--deliver telegram` is a valid target, and next runs render in the
container's local timezone (`TZ=Asia/Singapore` is pinned in the image), e.g.
`Next run: 2026-08-09T07:00:00+08:00`.

---

## V3 — Toolset identifiers ✅ (verified, with a version-drift caveat)

**Probe:** `hermes tools --summary` against a config carrying `agent.disabled_toolsets`;
docs `/docs/reference/toolsets-reference`; the operator's live `hermes doctor` output.

**Finding:** `agent.disabled_toolsets` **works** — the summary dropped from 25 toolsets to
the allowed set with the block in place. Verified identifiers:

`artec` (ours) · `clarify` · `code_execution` · `cronjob` · `delegation` · `file` · `memory` ·
`project` · `session_search` · `skills` · `terminal` · `todo` / `kanban` · `tts` · `video` ·
`vision` · `web` · `bfl` · `browser` · `browser-cdp` · `computer_use` · `image_gen` ·
`video_gen`

**Drift caveat:** 0.18.x lists the task board as `kanban`; the operator's 0.19.1 doctor listed
`todo`. Both are disabled defensively. `artec agent-review` now asserts every
expected-disabled identifier is still recognised at the running tag and fails RED if one has
disappeared (gap C6).

---

## V4 — ❌ BLOCKING: `web_search` does not support Brave (gap A5)

**Probe:** `hermes config show` API-keys section on a clean home; docs
`/docs/reference/tools-reference`.

**The v4 prompt states:** *"use hermes-agent's BUILT-IN `web_search` toolset, backed by Brave
… The Brave API key IS SET as a Railway dashboard variable on artec-brain. VERIFY … the exact
variable name hermes-agent reads (`BRAVE_API_KEY` or otherwise) and … rename the dashboard
variable to match rather than adding a shim."*

**Finding: there is no name to rename it to. Brave is not a supported backend.**

`hermes config show` enumerates every API key the agent reads:

> OpenRouter · OpenAI (STT/TTS) · Exa · Parallel · Firecrawl · Tavily · Browserbase ·
> Browser Use · FAL · Anthropic

The tools reference confirms `web_search` (toolset **`web`**) accepts exactly four search
backends:

| Provider | Environment variable |
|---|---|
| Exa | `EXA_API_KEY` |
| Parallel | `PARALLEL_API_KEY` |
| Firecrawl | `FIRECRAWL_API_KEY` |
| Tavily | `TAVILY_API_KEY` |

Brave appears nowhere. Renaming `BRAVE_API_KEY` to any of the four would hand a Brave
credential to a different vendor's API and fail authentication at first use — the *exact*
class of failure (`presence ≠ validity`) that already cost this project a cycle with
`ANTHROPIC_API_KEY`.

**Corroborating live evidence:** the running brain already logs
`WARNING tools.registry: check_fn check_web_api_key returned False; dependent tools will be
unavailable this turn` — the agent is *already* planning without search.

**Implemented:**
- The `web` toolset is enabled in config (it is correct and ready).
- Boot probes the configured search backend **against its real endpoint**. No key present, or
  a key that fails → the brain logs `SCOUTING UNAVAILABLE` loudly, records it in
  `agent_runs`, surfaces it in the digest's SPEND & HEALTH block, and **plans without
  scouting rather than silently omitting the step** (§7·A5 requirement).
- `browser`, `browser-cdp`, `computer_use` stay disabled; the `pre_tool_call` hook keeps
  blocking them. No scraping, no custom search integration, no shim.

**⛔ OPERATOR ACTION REQUIRED — one variable swap, no code change:** delete `BRAVE_API_KEY`
from artec-brain and set **one** of `TAVILY_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`, or
`FIRECRAWL_API_KEY`. Recommendation: **Tavily** — it is purpose-built for LLM agents, has a
free tier that comfortably covers one weekly planning session, and needs no crawl budget.
Until then the system runs correctly with scouting disabled and says so every day.

---

## V5 — Native Telegram video delivery ✅ (mechanism verified; ownership decision recorded)

**Probe:** `hermes send --help`.

**Finding:** media attachment is supported via a `MEDIA:<path>` convention in the message
body, and `--json` returns the raw platform result (which carries the message id):

> `-f PATH, --file PATH  Read message body from PATH (text only). To send an image/document
> as an attachment, use MEDIA:<path> in the message text instead.`

`hermes send` explicitly needs no running gateway for bot-token platforms and reuses the
gateway's credentials — so it does not create a second **poller** (only `getUpdates` polling
conflicts; see V6).

**Design decision (logged in DECISIONS.md):** the §4 tool list is fixed at fourteen and
declares `read_digest` READ ONLY, yet something must both *deliver* the video natively and
*record* the delivery `message_id` so `review_video` can refuse an unseen video. Resolution:
**`read_digest` performs the delivery and records the receipt.** It remains read-only with
respect to `orders`, `events`, `config`, and all post *content*; it writes only
`posts.video_review.telegram_message_id` + `delivered_at`. This keeps the tool count at
fourteen, keeps the brain the sole Telegram owner (the plugin runs inside the brain process),
and makes "nobody approves a video they were never shown" a database fact rather than a
convention. The alternative — a fifteenth `deliver_video` tool — was rejected only because the
count is specified; it is the cleaner shape if the count is ever relaxed.

---

## V6 — Telegram single-owner ✅ (verified mechanism, closes C1)

**Probe:** `hermes send --help` ("no running gateway required for bot-token platforms"); v4
prompt §4; live 409 behaviour of duplicate `getUpdates` pollers.

**Finding:** only `getUpdates` long-polling conflicts on a bot token. `sendMessage`/`sendVideo`
do not.

**Implemented:** the bespoke gate's long-poller is **deleted**. Its gating logic survives as a
library function reachable over authenticated HTTPS for emergencies and never polls. A startup
assertion plus a doctor check fail RED if a second poller is detected against the token.

---

## V7 — `hermes curator` ✅ (exists; v3's claim was right)

**Probe:** `hermes curator --help`.

> Background skill maintenance (curator) — subcommands: `status`, `usage`, `run`, `pause`,
> `resume`, `pin`, `unpin`, `restore`, `list-archived`, `archive`, `prune`, `backup`,
> `rollback`. "Bundled and hub-installed skills are never touched. Archives are recoverable;
> auto-deletion never happens."

`hermes curator run` is the monthly hygiene command. (`hermes skills reset` is a different
thing — it restores bundled skills, not curation.) Also available and useful:
`hermes journey` / `hermes learning` — a timeline of learned skills and memories over time,
which is a good monthly review companion to `artec agent-review`.

---

## Verification summary

| # | Item | Verdict |
|---|---|---|
| V1 | `learning:` config block | ❌ does not exist — mapped to real `memory:` / `skills:` keys |
| V2 | cron syntax | ✅ numeric DOW; **create exits 0 on failure** → verify by listing |
| V3 | toolset identifiers | ✅ verified; drift guarded by `agent-review` |
| V4 | web_search backed by Brave | ❌ **BLOCKING** — Brave unsupported; operator must swap to Tavily/Exa/Parallel/Firecrawl |
| V5 | native Telegram video | ✅ `MEDIA:<path>`; delivery receipt recorded by `read_digest` |
| V6 | single Telegram owner | ✅ only polling conflicts; bespoke poller deleted |
| V7 | `hermes curator` | ✅ exists, `curator run` |

**Which failure class could kill each new capability, and how it is verified** (§8):

| Capability | Class | Verification |
|---|---|---|
| cron registration (12 jobs) | third-party contract drift | verify by listing + `+08:00` assertion; boot fails otherwise |
| search backend | config/credential silence | real-endpoint probe at boot; degrades loudly, never silently |
| native video delivery | third-party contract drift | delivery receipt (`message_id`) must exist before review is accepted; Telegram refusal parks the post |
| price reconciliation | config/credential silence | fal API pull with `as_of`; stale >30d warns in digest |
| digest resources (fonts, prompts) | packaging/environment | already covered by wheel-content CI + `/healthz resources_packaged` |
| required config keys | config/credential silence | REQUIRED_CONFIG_KEYS validated at boot on every service; refuse to start naming the key |

---

## PROBED · the hermes-agent message store (v4 Stage 2c-i)

**Probe:** a real hermes-agent install (`$HERMES_HOME=C:\Users\KahFa\AppData\Local\hermes`,
the same install whose presence makes `test_hermes_discovers_artec_with_all_tools` run
rather than skip). Read-only SQLite inspection — not documentation, not inference.

```
$HERMES_HOME/state.db                       SQLite, WAL (state.db-wal / state.db-shm)
  sessions(id, source, user_id, session_key, chat_id, started_at, …)
      id      e.g. '20260802_212025_9f03c9'  (cli), 'cron_dacb65fa6fae_20260801_001534'
      source  'cli' | 'cron' | …
  messages(id, session_id, role, content, tool_call_id, tool_calls, tool_name,
           timestamp, token_count, …, active, compacted)
      role    'user' | 'assistant' | 'tool'      (observed counts: 71 / 448 / 540)
      content TEXT — a plain string for operator turns
```

**An operator turn is `role='user'`.** Tool results are `role='tool'` with `tool_name` set;
they are NOT user turns. That is the property that makes this store usable as an authority
for the transcription guard: a tool result carrying digits cannot authorise those digits.

### The heuristic did NOT match, and it was wrong permissively

The first implementation looked for `$HERMES_HOME/sessions/{task_id}.jsonl`. That directory
**exists**, which is exactly why the guess was dangerous — but it holds:

```
sessions/request_dump_20260708_172444_99d5de_20260708_172709_462377.json
```

**`request_dump_*.json` are debug artefacts**, written only on a non-retryable API error
(`"reason": "non_retryable_client_error"`), containing the full provider request body — a
constructed message list including `{"role": "user", …}` entries. The module's glob
fallback (`sessions/*{task_id}*`) would have matched one whenever the dump filename carried
the session id, and parsed provider-format `user` entries out of it.

So the heuristic was fixed rather than widened: **one source, `state.db`, no fallback**, and
a `task_id` that names no row in `sessions` returns "cannot verify" instead of a loose
match. `tests/unit/test_v4_transcription_guard.py` asserts the request-dump case directly.

**Not yet verified:** that the `task_id` hermes passes to `pre_tool_call` equals
`sessions.id` for a TELEGRAM session (the probed sessions are `cli` and `cron`). If it does
not, the guard fails closed — refuses and names `artec measure` — which is the safe
direction, but it means metrics entry does not work until confirmed. One real digest
session settles it.

---

## PROBED · deployed environment (operator probes A–E, 2026-08-04)

| Fact | Value |
|---|---|
| hermes-agent on artec-brain | v0.19.1 (tag 2026.7.30), git install, `/opt/hermes-agent`, Python 3.12.13 |
| `HERMES_HOME` | `/data/hermes` (Railway volume `hermes-brain-volume`) |
| artec api interpreter | **`/opt/venv/bin/python`** — NOT the system Python. `python` at an SSH prompt has no pydantic; every in-container diagnostic must use the venv path |
| artec api start command | `/opt/venv/bin/python /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| `RAILWAY_PRIVATE_DOMAIN` (artec api) | `artecautomatedmarketing.railway.internal` |

### Internal networking WORKS — the IPv6 concern was wrong

```
$ getent hosts artecautomatedmarketing.railway.internal
fd12:223a:dbc4:1:d000:164:71b2:a7f2

$ urlopen('http://artecautomatedmarketing.railway.internal:8080/healthz')
b'{"status":"ok","db":true,"migrations_current":true,"resources_packaged":true}'
```

The name resolves to IPv6 and the request succeeds against a service bound to
`--host 0.0.0.0`. **No start-command change is needed.** Recorded so nobody revisits it.

`ARTEC_API_BASE` pins `:8080` while Railway injects `PORT`, so `_publish_bytes` now fails
with `cannot reach artec api at <url>` naming the attempted URL — a port change must not
present as a mute `deliver_video` refusal that looks like a bad file.

### CORRECTED · the message store is PROFILE-SCOPED

```
/data/hermes/active_profile                          ->  artec-brain
/data/hermes/profiles/artec-brain/state.db           <- THE STORE
/data/hermes/state.db                                <- DECOY, exactly 1048576 bytes
```

The earlier entry in this file named `$HERMES_HOME/state.db`. That file exists, **opens
cleanly, and answers `no such table: sessions`** — a plausible error rather than an
absence, which is why the first probe concluded the schema was wrong. Both earlier
investigations ran against a development machine whose layout differs.

Resolution is now `active_profile` → `profiles/<profile>/state.db`, with the `sessions` and
`messages` tables asserted before the file is trusted, and **no fallback, no glob, no
search**. Anything else is `cannot verify` → refuse → `artec measure`.

### Tool posture, verified BY LISTING on the deployed brain

```
⚕ Tool Summary
  🖥️  CLI  (8/28)      Artec · Clarifying Questions · Cron Jobs · Memory ·
                       Session Search · Skills · Vision · Web Search & Scraping
  📱 Telegram  (6/28)  Artec · Clarifying Questions · Memory · Session Search ·
                       Skills · Web Search & Scraping
```

Absent from both surfaces: Browser Automation, Code Execution, Computer Use, File
Operations, Terminal & Processes, Task Delegation, Task Planning, Image Generation,
Text-to-Speech, **and both `kanban` and `todo`**. A default install shows all of them, so
the lockdown is doing real work rather than describing a default. **C6 is satisfied by
observation**, and `artec agent-review` keeps it satisfied by asserting the identifiers
still exist.

`hermes tools --summary` lists TOOLSETS, not tools — "Artec" present does not prove fifteen
handlers registered. The entrypoint now counts them by listing and names the shortfall.

**`Web Search & Scraping` is already live** on the deployed brain, which runs `main`. So
scouting is CALLABLE whether or not a backend was ever probed — which is why the brief now
carries the search-backend state, and why an absent probe renders as NOT YET PROBED.

### Tavily is LIVE

```
scouting: AVAILABLE via tavily — HTTP 200, 1 result(s) for the probe query
```

The credential is valid. A5's remaining work is enablement and the boot probe, not the key.
The same run failed to write `config.scouting_status` (`postgres.railway.internal` does not
resolve outside Railway — expected for `railway run`), which leaves the key **absent**
rather than wrong. Hence the three-state rendering.

### The profile `config.yaml` lives only on the volume — mitigated

Four in-place versions in one day (`config.yaml.bak.20260803_*`), plus a `kanban.db` fossil.
The entrypoint already copies the repo-committed `deploy/hermes-brain/config.yaml` onto the
volume at **every boot** (step 5/10), so the canonical file IS version-controlled and a
volume loss cannot take the posture with it. `artec agent-review` now also diffs the live
file against the committed one and goes RED on drift, for the window between boots.
