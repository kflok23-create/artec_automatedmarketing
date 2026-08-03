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
