# DECISIONS

Choices made during the one-pass build where the prompt was silent, ambiguous, or collided
with a verified external contract. Each was implemented fully; none requires operator action
unless marked.

1. **Upload-Post endpoints (verified against docs.upload-post.com/openapi.json).** Photos
   go to `POST /api/upload_photos` with `photos[]`; video goes to `POST /api/upload` with
   `video`. Shared fields: `user`, `platform[]`, `title`, `description`. Auth header
   `Authorization: Apikey <key>`. The prompt's single `/api/upload` for both media kinds is
   amended accordingly. The official PyPI SDK was not used: it wraps the same two routes and
   pinning our own httpx client keeps the request shape test-assertable.

2. **Drive `includeItemsFromAllDrives` is a list/changes-only parameter.** The Drive v3 API
   rejects it on `files.get` / `files.create` / `files.delete`. Implementation: every
   `files.list` and `changes.list` call passes `supportsAllDrives=true`,
   `includeItemsFromAllDrives=true`, `corpora='drive'` + `driveId`; every get/create/delete
   passes `supportsAllDrives=true`. The acceptance test asserts exactly this split.

3. **Billplz attribution joins on bill_id, not reference_1** (revised at CHECKPOINT 2,
   operator decision). artec.my's live checkout already occupies BOTH reference slots —
   `reference_1` = discount code (read by billplz-callback.php and thank-you.html),
   `reference_2` = pack (single/twin) — so neither can carry a post_id without breaking the
   existing order flow. Flow now: checkout.php POSTs a server-side `order_created` event to
   `/event` the moment the bill is created (before payment), carrying bill_id + post_id
   (read from the spine's `utm_campaign` param) + code/value/pack/market/utm. HERMES stores
   it as a pending row in `events` keyed `order_created|billplz|{bill_id}`. The paid
   callback (forwarded verbatim from artec.my with x_signature intact, verified
   independently) joins on bill_id to resolve post_id. No matching pending row — direct
   Billplz link or failed pre-payment POST — → UNATTRIBUTED, never guessed. The
   bill-fetch-for-reference_1 path is deleted; the webhook now performs no external HTTP,
   which keeps it comfortably inside Billplz's 20-second / 5-retry callback contract.

4. **Seedance endpoint slugs are config, not code — and unverified.** fal.ai rate-limited
   the model page during the build, so the operator-supplied slugs
   (`bytedance/seedance-2.0/text-to-video`, `bytedance/seedance-2.0/reference-to-video`)
   are seeded into `config.video_family` together with `reference_syntax: "bracket"`
   (`[Image1] [Video1] [Audio1]`), max 3 ref videos, 4–15 s, 480p/720p. `artec doctor`
   flags the family as UNVERIFIED until the first successful render. Switching to Wan v2.6
   is a config edit (endpoint + `reference_syntax: "at"`), no code change.

5. **CAC needs a spend figure the loop does not otherwise capture.** There is no ad spend
   in the system (Meta Ads is out of scope). CAC per channel is computed as
   `config.weekly_spend_minor[channel] / attributed orders` only when the operator has
   seeded that config key (per-channel `{currency, amount_minor}`); otherwise CAC is
   reported "unmeasurable" and the kill rule is not applied. Kill lines compare only within
   one currency, never converted.

6. **KPI_WEIGHTS blend normalized lane scores inside the scoring function only.** The lane
   rule forbids summing revenue figures with engagement figures; scoring normalizes each
   lane to 0–1 within a lever group and combines the normalized scores with KPI_WEIGHTS.
   No raw order amount ever meets a raw metric count; reports keep the lanes in separate
   blocks.

7. **SQLite is the unit-test database.** JSONB columns use
   `JSON().with_variant(JSONB, "postgresql")`; `V_BRIEF_SQL` is written in the portable
   subset (`||`, `CAST(... AS TEXT)`, subquery-with-LIMIT instead of parenthesized UNION
   limbs) so the same string runs on both engines. CI additionally runs
   `alembic upgrade head` against a throwaway Postgres.

8. **Fonts are operator-committed** (per the groundwork list): the three static .ttf
   families go into `assets/fonts/` by hand; `artec doctor` is RED and TEXT CARD raises a
   named error until they exist. No font bytes are fetched or committed by the build.

9. **`app/stages/doctor.py` and `app/stages/cycle.py`** are added beyond the §12 layout —
   `doctor` and `cycle --dry-run` are mandated commands and deserve their own stage modules.
   Likewise `app/integrations/fakes.py` holds the dry-run/test fakes.

10. **SEO_SEEDS ships empty.** The prompt left it `<<STILL BLANK>>`. It is seeded as `[]`;
    `artec doctor` warns and `artec ideate` refuses to run with a named operator error
    until 5–15 seeds are set (`artec config seed --file config.yaml` or
    `artec config set seo_seeds '["…"]'`).

11. **Stripe signature verification is hand-rolled** (HMAC-SHA256 over `t.payload`,
    constant-time compare, 5-minute tolerance) because the stripe SDK is not in the locked
    stack. The webhook reads only `client_reference_id`, `amount_total`, `currency`,
    `customer_details.email` (hashed), `created`, `id`. The module contains no reference to
    UTM anywhere (test-asserted).

12. **post_id allocation** is a `config` row (`post_id_counter`, starts at
    POST_ID_START=1482) incremented inside the insert transaction with a row lock on
    Postgres; monotonic, gap-tolerant.

13. **Idempotent ideate**: re-running `artec ideate` for the same week only tops up each
    channel to its cadence count (counting existing non-REJECTED posts for that week), so a
    re-run never duplicates a plan.

14. **Gate transport is Telegram long polling** (`getUpdates` with a 25 s poll inside the
    blocking CLI command) — no webhook, no scheduler, works from a laptop.

15. **Email posts store their copy as JSON in `posts.caption`** (`subject`, `headline`,
    `body_copy`, `cta_text`, `story_block`); social posts store plain caption text. One
    column, discriminated by channel.

16. **Interactive gate/measure are CLI-only.** Their POST mirrors exist but the gate route
    returns 409 explaining it is a Telegram/CLI session; `/commands/measure` accepts the
    full JSON payload (that is its non-interactive form).

17. **Rate limiting on `/event`** is an in-memory fixed window (120 req/60 s per client IP)
    — adequate for one service instance, replaced trivially if the service is ever scaled
    (it must not be; ONE SERVICE rule).

18. **`ENVIRONMENT=test` never selects fakes implicitly.** Fakes are injected explicitly by
    `artec cycle --dry-run` and the test suite; production code paths always construct real
    clients.

19. **Aspect derivation:** width/height ratio < 0.9 → vertical, ≤ 1.1 → square, else
    landscape; unknown dimensions → NULL aspect (matches any aspect filter).

20. **Person-asset policy follows the letter of the spec:** when `allow_person_assets` is
    false only `has_person = true` rows are excluded; UGC's unknown (`NULL`) stays eligible.
    Flip the config key when releases are settled.

21. **The asset bank is a Shared Drive on the techup.my Google Workspace account** (final
    state after two CHECKPOINT-3 revisions), with the service account as **Content
    Manager**. `GOOGLE_SHARED_DRIVE_ID` (`0ACcG7AD2xK67Uk9PVA`) and
    `GOOGLE_DRIVE_ROOT_FOLDER_ID` (`17gYS0IbakBLNVDLfX8-wZIpL61gOoXSr`) are both set, to
    different values — the drive id is not the folder id. History and lessons, kept
    because each cost a doctor cycle:
    - **My Drive mode was tried and abandoned: service accounts have ZERO Drive storage
      quota**, so every upload the service account makes into a My Drive folder fails on
      `storageQuota` regardless of how the folder is shared. The My Drive code path
      (empty `GOOGLE_SHARED_DRIVE_ID` → parent-only queries, no `corpora`/`driveId`)
      remains implemented and tested, but it is **read-only in practice** — unusable for
      `_generated/` writes.
    - **The service account's GCP project must have the Drive API explicitly enabled**
      (console → APIs & Services → enable "Google Drive API"). A fresh project does not
      have it on by default; the failure reads as an API error, not a permissions one.
    - **Drive is not read-after-write consistent on Shared Drives**: a get/delete by id
      immediately after `files.create` can 404 even though create returned the id. The
      doctor write probe treats the successful create as proof of write capability,
      retries cleanup ~3 times over ~5 s, and defers cleanup (pass, with a note) if the
      id still hasn't propagated. Doctor distinguishes 404 (stale/unpropagated id) from
      403 (permission) from quota errors, each with its own remedy.
    - **A bank migration invalidates every persisted Drive id.** `assets sync` stores a
      `drive_root_marker`; when the configured root changes, it auto-resets the changes
      cursor and forces a full rescan, marking all pre-migration assets `missing`.

22. **v3 — WHY the toolbox was locked down, not just the rules** (each rule earned by a
    production failure in one cycle):
    - **Models cannot render text.** A diffusion model asked to place words produced
      "Robotics class 1 termı $15" and "One box of blucks $9%" on real posts. No prompt
      tuning fixes letterforms — so all words, numbers and prices go through Pillow and
      ffmpeg drawtext with the four committed brand fonts, and a guard inside `Fal.run`
      raises on any prompt smuggling text (quoted strings, price patterns, the words
      text/caption/title/label/write/says). There is no fallback to a model when the
      Python path is inconvenient.
    - **Generated video is expensive and off-brand.** One generative-video call cost USD 8
      and returned blocks that are not our product; the output also failed to play
      (missing leading moov atom). All generative video endpoints are deleted from code
      and config; video is EDITED from real `raw-video/` footage with ffmpeg only, every
      output written `-movflags +faststart`.
    - **479 real photographs exist and should be used before anything is synthesised.**
      Bank-first became BANK-ONLY for anything depicting the product; no match → PARK
      with a wishlist. GENERATE via the artec LoRAs is disabled (`generate_enabled=false`)
      with config rows retained — reversible by one flip, and doctor reports it DORMANT.
    - **The budget is structural, not advisory.** Config price table in integer cents; a
      per-call ceiling AND a per-run USD 1.00 cap, both tested; unknown endpoints are
      uncallable; spend prints after every call; over-cap parks the remainder.
    - **The governing sentence:** the agent does interpretation and generation; SQL does
      arithmetic; Python does pixels.

23. **v3 — the agent seam is a capability boundary, not a policy.** hermes-agent (pinned
    `v2026.7.30` = release 0.19.1 of 2026-07-30; never `main`, never `hermes update` on a
    schedule) gets exactly six tools: four reads, `write_plan`, `record_gate_decision`.
    No tool writes orders/events/metrics/config; no SQL tool. A simulated attempt to
    write an order fails with "no such tool" — the capability does not exist to be
    permitted. The plugin is self-contained (sqlalchemy textual SQL only) so the brain
    image never imports the artec codebase. The bespoke CLI was renamed `artec` because
    hermes-agent's own CLI collides on `doctor` and `update`.

    **Corrected after review against the docs
    (https://hermes-agent.nousresearch.com/docs/developer-guide/plugins,
    …/docs/reference/cli-commands, …/docs/user-guide/configuration,
    …/docs/reference/toolsets-reference, …/docs/user-guide/features/cron) — the original
    brief's plugin instructions were wrong and the first implementation shipped
    undiscoverable:**
    - A loose `.py` in the plugins dir is NOT a plugin and fails silently. The real
      contract: a `plugins/artec/` package with `plugin.yaml` + `__init__.py` exposing
      `register(ctx)`, tools registered via `ctx.register_tool(name, toolset, schema,
      handler)`; max one level of directory nesting.
    - Handlers are `(args: dict, **kwargs) -> str` returning a JSON STRING always —
      success and error — and never raising (exceptions break the tool loop). All six
      wrap in an `{"ok": …}` envelope; contract-tested with garbage inputs.
    - Plugins are OPT-IN: `hermes plugins enable artec` is in the entrypoint and the
      boot hard-fails unless `HERMES_PLUGINS_DEBUG=1 hermes plugins list` shows artec.
    - The gateway foreground command is `hermes gateway run`, not `hermes gateway`.
    - Cron jobs are NOT config.yaml entries: registered via `hermes cron create`
      (storage `$HERMES_HOME/cron/jobs.json`), idempotently guarded in the entrypoint.
    - Telegram credentials go to the profile `.env` via `hermes config set`, not
      config.yaml.
    - There is no scriptable `hermes tools disable`: shell/file-write are disabled via
      the documented `agent.disabled_toolsets: [terminal, code_execution, file]`
      (toolset names verified) PLUS a `pre_tool_call` hook in the plugin that blocks the
      same families — defense in depth we control.
    - Extras verified in pyproject.toml at the pinned tag: `messaging`, `cron`, `mcp`
      all exist; `anthropic` added for the native transport (also a real extra).
    - `security.redact_secrets` / `privacy.redact_pii` / `hermes profile create <name>`:
      verified verbatim.
    - Still on trust (labelled in deploy/hermes-brain/README.md, verify on first boot):
      Telegram allowed-chat configuration; `hermes cron create` timezone semantics
      (container TZ pinned to Asia/Singapore as a belt).

24. **v3 — the four scheduled jobs are the entire lift of the no-scheduler rule.** SUN
    07:00 agent LEARN→IDEATE and SUN 09:00 agent gate (hermes-agent cron, Asia/Singapore);
    daily publish-by-slot and daily 06:30 measure prompt (`python -m app.scheduler`, a
    plain 30-second loop — still no APScheduler/celery/cron imports). `slot` became a real
    firing time via config `slot_times` AND remains a learned lever. A test counts exactly
    four jobs across both codebases. The daily measure job sends the unmeasured-post list
    to Telegram; figures still enter through `artec measure` (no channel APIs, no CSVs).

25. **v3 — shadow mode is the cutover instrument.** `plan_source` starts and stays on
    `shadow`: the agent plans into `plans_shadow`, the gate presents the bespoke plan,
    nothing the agent produces goes live. `artec plan-diff --week` (per-field agreement on
    channel/angle/hook/cta_type/slot, paired on channel+slot) is the operator's evidence
    for 2–3 Sundays. Flip to `agent` reverses the mirror; flip to `bespoke` is full
    rollback — one config row, no redeploy, agent cron output ignored. Edit deltas are
    stored in `posts.gate_action` because the deltas, not the verdicts, train taste.
    `artec audit-memory` makes metric leakage into agent memory visible (it cannot be
    perfect); numbers live in Postgres only.

26. **v4 · `ffprobe` is present in the deployed container — with a qualification that
    changes the implementation.** Verified in the `artec api` Railway shell:
    `ffprobe version 7.1`, `--enable-ffprobe`, at `/root/.nix-profile/bin/ffprobe`. That
    closes the packaging-class risk against the publish pre-flight *for a login shell* —
    which is not the same thing as for the running process. Railway starts uvicorn and the
    scheduler as non-login processes that may not source the nix profile, and treating a
    shell reading as a process reading would be the identical substitution that made the
    SQLite allocator look tested and StaticPool look like two replicas. Therefore:
    `shutil.which` is resolved at runtime **inside the app process**, never a hardcoded
    path; the doctor `ffprobe` check is reachable over authenticated HTTPS
    `/commands/doctor` so it executes in-process, and **only that reading is evidence**;
    and ffprobe is parsed as `-print_format json`, never human-readable output.

27. **v4 · `TAVILY_API_KEY` is set on artec-brain; `BRAVE_API_KEY` is gone.** Set and
    deliberately unused — `web` toolset enablement and the boot probe are Stage 2c. The
    probe must hit Tavily's real search endpoint and gate on the response, exactly as the
    Anthropic probe does: presence is not validity.

28. **v4 · `RESTORE_TARGET_URL` is dead config.** Zero matches across source, config,
    workflows and docs; set on artec-brain only; being deleted by the operator. It is not
    the restore target under any circumstance — `restore-check` creates and drops its own
    uniquely-named scratch database, with a free-disk check first and RED rather than a
    schema-restore fallback if `CREATE DATABASE` is denied.

29. **v4 · the absolute video byte floor was WITHDRAWN — do not reinstate it.** It had been
    tuned down to 2 KB to accommodate a synthetic solid-colour fixture (~0.010
    bits/pixel-second), and in doing so stopped catching a truncated render landing at
    12 KB: the fixture had reshaped the spec. Replaced by a bitrate floor of **0.05
    bits/pixel-second** — `(size_bytes × 8) ÷ (width × height × duration)` — which is
    resolution- and duration-independent. Measured on this encoder: real 1080×1920 social
    H.264 ≈ 1.45, `testsrc2` ≈ 2.95, solid colour ≈ 0.010, pure lavfi noise ≈ 105
    (incompressible, and as unrepresentative as solid colour in the opposite direction).
    The floor sits ~29× below real footage and ~5× above a degenerate encode. Fixtures for
    anything measuring *content* must be realistic; solid colour remains fine for moov,
    duration and aspect, which do not.

30. **`artec measure --csv` from the smoke-run example is not implemented** — the locked
    decision table says "No channel APIs, no CSV files"; measure is interactive, `--json`,
    or `POST /commands/measure`. The RUNBOOK shows the correct invocation.

31. **v4 · the digest renders money in currency, not minor units.** Minor units stay the
    storage invariant and the arithmetic is untouched; formatting happens only at the
    render boundary (`format_money_minor`, `format_usd_cents`). `net CM 21200 minor` asked
    a human at 21:00 to divide by 100 in their head — the digest is a human surface and a
    figure that must be decoded is a figure that gets misread. An unknown currency renders
    its code rather than guessing a symbol.

32. **v4 · spend is always shown against its own denominator.** The dry run set
    `fal (week): 18.66¢` beside `cap 250¢/run` — a week-to-date figure next to a per-RUN
    cap. The same class of error the per-megapixel correction fixed in the price table: a
    number displayed against the wrong denominator is how a cap silently stops meaning
    what it was set to mean. The digest now reports the most recent render run against the
    run cap, and week-to-date separately; when there was exactly one run this week it says
    so rather than leaving the reader to assume it.

33. **v4 · the price-table staleness line is emitted every night, in every state.** Absent
    warnings read as "no problem" — the config-silence failure class, in the one place
    designed to surface problems. Until Stage 2c lands reconciliation the honest line is
    `price table: seeded <date>, never reconciled against fal`, and it becomes accurate
    automatically the moment `acknowledge_price_table` writes `acknowledged_at`.

34. **v4 · `agent_weekly_cap_minor` raised 500 → 1500 (USD 15.00/week).** USD 5.00 was a
    guess made before the brain ran nightly. Its weekly load after this build is one
    LEARN→IDEATE, one ~45-minute gate conversation and six digest sessions that stay open
    to relay replies. The degradation order on approach is drop scouting, then SHORTEN THE
    GATE CONVERSATION — so a cap set too low does not fail loudly, it quietly shortens the
    single most valuable human touch in the system, every week, while reporting green.
    `agent_runs.cost_cents` now meters it, so after two real weeks the number is set from
    evidence. Revisit then; do not inherit it.

35. **v4 · superseded shipped defaults are upgraded on seed; operator values never are.**
    A non-destructive seed keeps whatever is stored, which is right for an operator's
    choice and wrong for a corrected default — the old number was never chosen by anyone,
    so it would be inherited forever. `SUPERSEDED_DEFAULTS` upgrades a key only when the
    stored value is still exactly the old shipped default, and `artec config seed` reports
    it under `upgraded`. Anything the operator set is untouched and still reported `kept`.

36. **v4 · the digest is split for Telegram in tested code, not by the model.** The payload
    knows nothing about the 4096-character limit and a silently truncated digest is a post
    that becomes invisible forever. `prepare_digest` stores a `messages` list split on
    SECTION boundaries (item boundaries only when one section exceeds a whole message,
    never mid-line, marked `(continued)`), and `read_digest` hands it over to be sent
    verbatim. NEEDS YOU is first by construction, not by compliance.

37. **v4 · job 12 refuses to run on Sunday inside the body.** `read_digest` returns
    `deliver: false` with the reason on a Sunday in Asia/Singapore and hands over no
    payload: the 09:00 gate is that day's human touch, and a second session the same
    evening spends the operator's attention twice. The cron expression says so too, but a
    cron expression is one edit away from being wrong.

38. **v4 · `record_metrics` writes nothing without `confirm: true`.** The first call
    returns an echo of exactly what would be recorded and what would stay NULL, so a
    mistyped figure is caught by the operator at 21:00 rather than by `learn` three weeks
    later. Making the echo a *return value* rather than a prompt instruction is what makes
    it happen every time. A single ordered line — `4200, 0.62, 12, 45, 8, 118` — is
    accepted as one reply; an empty position is unmeasured, never zero; a thousands
    separator is REFUSED rather than guessed at, because `4,200` would parse as two
    positions and shift every later figure into the wrong column, which is worse than no
    reading at all. The transcription hook polices an ordered line exactly like a figures
    dict.

39. **v4 · the transcription guard was CIRCULAR and is now transcript-backed.** As shipped
    in 06d3d79 the `pre_tool_call` hook compared the agent's `figures` against the agent's
    own `operator_message` ARGUMENT — the agent supplied both sides, so the check compared
    something against itself and reported green. Same shape as the StaticPool advisory-lock
    test. Demonstrated live: a call carrying figures the operator never sent returned
    `None` (permitted). The guard now reads hermes-agent's session transcript
    (`plugins/artec/transcript.py`), which the agent does not author.
    **The rule, stated because it is no longer "the immediately preceding message":** every
    digit submitted must appear in SOME message the OPERATOR sent in this session, and
    `operator_message` must itself be one of those messages. The window widened because the
    confirm flow makes the last operator turn "yes" — and it widened only across the
    operator's own turns; assistant turns are never a source.
    **Fail closed:** the transcript store LAYOUT is not verified against a live
    hermes-agent (VERIFY.md carries no such fact), so the module discovers it at runtime
    and returns None when it finds nothing — and None REFUSES, naming `artec measure` as
    the fallback. An unverifiable transcription is not a verified one.

40. **v4 · `deliver_video` uploads the PUBLISH BYTES multipart; it never sends a URL.**
    The first build handed Telegram the fal URL, which broke both halves of the design: the
    operator approved fal's copy while publish streams the Drive copy (§7.9), and Telegram's
    rejection of a malformed file — the independent validity check the gate rests on — was
    validating somebody else's bytes. The brain holds no Drive credentials and must not
    grow any, so it reads them from the app's authenticated `GET /commands/media/{post_id}`,
    which resolves through `publish_media_path`, the function publish itself calls.
    Byte-identity is proven by sha256, not assumed. A missing Drive file PARKS; there is no
    fal fallback, because a silent fallback is precisely the divergence being closed. New
    env on artec-brain: `ARTEC_API_BASE`, `HERMES_API_TOKEN`.

41. **v4 · `config` rows carry provenance (`set_by`), so supersession cannot overwrite
    intent.** Without it, `SUPERSEDED_DEFAULTS` could not tell a stored 500 written by an
    old seed from a stored 500 the operator chose — config-silence inside the mechanism
    built to prevent it. `set_by` is 'seed' | 'operator' | NULL(unknown). Only 'seed' is
    corrected. NULL is REPORTED under `needs_decision` with the exact `artec config set`
    command, never taken silently. Migration 0006.

42. **v4 · §B — the two skip rules are evaluated as rules, not as ordering.**
    `skip_reason(session, post)` is pure and runs for every post on every publish pass:
    email never publishes unless APPROVED_TO_SEND **and** `email_review.decision ==
    'approve'`; video never publishes unless APPROVED_TO_SEND **and** a delivery
    `telegram_message_id` exists **and** the decision is approve. Status alone is not the
    gate — a status is reachable by routes the review never took; a receipt is not.
    APPROVED_TO_SEND enters `select_due_posts` and nowhere earlier, so an approval at 21:15
    waits for the next occurrence of its slot.

43. **v4 · §5.3 pre-flight is wired into publish and is BLOCKING.** First point at which A
    is load-bearing. It runs before the upload, parks with a wishlist entry on failure, and
    resolves ffprobe via `shutil.which` in-process. Consequence accepted: `FakeDrive` now
    returns realistic media (1080×1080 noise, and the artefact actually uploaded where one
    exists) because pre-flight measures CONTENT — a 64×64 flat swatch would have let the
    fixture decide the test.

44. **v4 · the pg marker was over-applied and the counts double-counted two tests.**
    `pytestmark = pytest.mark.pg` marked the whole file, including `lock_key` (pure hashing)
    and the test that asserts NotPostgres on SQLite — neither needs a database, so the
    SQLite job ran them too and "13 pg + 273 SQLite" counted them twice. The marker is now
    per-test: **329 total = 318 non-pg + 11 pg**, and every pg test genuinely requires
    Postgres.

45. **THE REFERENCE EXAMPLE, kept verbatim: the tests passed because they asked the same
    question the code did.** The transcription guard compared the agent's figures against
    the agent's own `operator_message` argument, and its tests asserted exactly that
    comparison. Both were internally consistent and jointly worthless. The StaticPool
    advisory-lock test had the same shape: a fixture that made the question unaskable, and
    an assertion that agreed. Neither was found by running the suite; both were found by a
    human reading what the system actually does. When a guard's test and its implementation
    share an assumption, the suite cannot see it — so for anything load-bearing, ask what
    the check compares AGAINST, and whether the thing being checked could have supplied it.

46. **v4 · the message store is `$HERMES_HOME/state.db`, probed, not inferred.** See
    VERIFY.md. `sessions/` holds `request_dump_*.json` — error artefacts carrying a
    constructed provider message list — and the original glob fallback would have parsed
    one as a transcript. The fix was to narrow to a single source with no fallback, not to
    widen the heuristic: a guard that finds its authority by guessing will one day guess in
    the permissive direction. An operator turn is `role='user'`; tool results are
    `role='tool'` and can never authorise a figure.

47. **v4 · A6·2 — the agent spend degradation ORDER is code, and the gate is a constant.**
    Scouting drops at 60% of the weekly cap; the gate CONVERSATION shortens at 85%; the gate
    itself never stops, at any spend level. `gate_runs` is a literal `True`, not a
    threshold, and a property test walks the whole spend range — "never skip the gate" must
    not depend on getting a boundary right. The posture reaches the brain as DATA on
    `read_brief`, the read it makes first, rather than as a sentence in a prompt.

48. **v4 · A5 — the Tavily probe calls the real search endpoint and gates on the response,
    and never fails the boot.** Presence is not validity. A 401, a zero-result 200, or a
    timeout all record `scouting: UNAVAILABLE` with the backend's own reason into
    `config.scouting_status`, which the digest reports nightly. Failing the boot instead
    would trade a whole week of planning for a trend feed. A test asserts the key never
    appears in the recorded status, because that status is rendered to Telegram.

49. **v4 · A4 — `memory.write_approval: false` is written with the REAL key names, and the
    audit rides the same service.** There is no `learning:` block in hermes-agent; the
    original brief invented one, and an invented key is silence with extra steps — it
    parses, changes nothing, reports green. The agent now writes memory autonomously, so
    `audit_memory_report.py` runs on the brain at boot (and will ride job 10 weekly),
    writes `config.memory_audit`, and the digest renders it — including "NOT YET RUN",
    because an absent audit must not read as a clean one. The patterns are duplicated from
    `app/stages/agent_review.py` because the brain image has no app package; a test asserts
    the two lists stay identical. Duplicated is fine, silently divergent is not.

50. **v4 · `ARTEC_API_BASE` uses Railway private networking.**
    `http://artecautomatedmarketing.railway.internal:8080` — the port probed from artec
    api's own deploy log (`Uvicorn running on http://0.0.0.0:8080`), not assumed. Video
    bytes crossing the public internet on every digest would be slower, billed as egress,
    and would needlessly expose an authenticated media route.

51. **v4 · the media route's boundary is structural, not validated.** `GET
    /commands/media/{post_id}` takes a POST KEY — never a path, never a file id — resolves
    the file id from that post's row, requires the resolved Drive path to sit under
    `_generated/`, and never lists. A traversal-shaped id is simply a key that matches no
    row. Both properties are tested, including a parametrised set of hostile ids.

52. **The packaging/environment class extends to PROBED FACTS, not just to code.** The
    original class was "works from the repo root, vanishes under `pip install .`" — code
    tested one way and deployed another. Probe B is the same error one level up: a fact
    established on a development laptop (`$HERMES_HOME/state.db`) and relied on in a
    container where the real path is
    `$HERMES_HOME/profiles/$(cat active_profile)/state.db`. It was worse than an absence,
    because the laptop path EXISTS in production as a 1 MiB file that opens cleanly and
    answers `no such table: sessions` — a plausible error, which reads as "wrong schema"
    rather than "wrong file". **A probed fact carries the environment it was probed in.
    If it will be relied on in the container, probe it in the container.**

53. **v4 · `POST /commands/doctor` raised KeyError on `main`, and the check now reports RED
    instead of escaping.** `run_doctor` read `OPERATOR_CONSTANTS['endpoint_prices_cents']`,
    which v4 deleted when prices moved to the `endpoint_prices` TABLE (§7·C5). Every call —
    CLI and HTTPS — died. A doctor that cannot run is worse than a missing check: it is a
    green light that never lights. The price check now reads the table, and a database it
    cannot query produces a RED CHECK rather than a 500 that takes every other check with
    it.

54. **v4 · a 401 now names itself, server-side only.** A production 401 against a token
    verified byte-identical in the shell, in `/proc/1/environ` and in a fresh interpreter
    left nothing to go on. `require_token` logs which failure it was — no header, empty
    after `Bearer `, non-ASCII, or value mismatch — with lengths and **sha256[:8]** of each
    side. Eight hex characters of a digest is not reversible and is the same comparison the
    operator made by hand across layers. The RESPONSE stays generic: an unauthenticated
    caller learns nothing. The bearer is now asserted OVER HTTP against the real app, not
    against the dependency in isolation — the dependency was already correct, which is
    exactly why testing it again would have kept agreeing with it (see #45).

55. **v4 · the real-encode fixture, and what it cost.** `tests/fixtures/real_raw_video.mp4`
    — 1,401,754 bytes, 3.000 s, 1920×1080, h264+aac = **1.803 bits/pixel-second** against a
    floor of 0.05, i.e. 36× headroom. The 1.45 previously used as the "real footage"
    reference was a guess standing in for a measurement, and it was about right; it is now
    measured. **The source was 19.8 Mbps (9.54 bits/pixel-second) and this is a CRF 24
    re-encode, so re-encoding cost a factor of five.** Recorded so nobody later "fixes" it
    back to a stream copy without knowing what changed. The solid-colour clip is KEPT
    alongside it: structurally perfect (leading moov, real stream, legal duration, right
    aspect) and still not footage. That pairing is the point of having a bitrate floor.

56. **v4 · agent memory can go FALSE about capabilities, and the audit now catches that.**
    The live MEMORY block asserted *"artec plugin exposes exactly 6 tools … There is NO
    render, publish, measure … tool … Don't promise those; say so plainly"* while the seam
    had grown to FIFTEEN. Memory is injected into EVERY turn, so the next gate would have
    been told, with authority, that capabilities the agent holds do not exist — and
    instructed to say so to the operator. **That is not a stale note; it is a standing
    instruction to misreport.** The figure patterns could never have caught it: they look
    for numbers and dates.
    `audit-memory` now also flags (a) a tool COUNT or a "there is no X tool" claim that
    contradicts the LIVE plugin manifest at audit time, and (b) IMPERATIVES — memories are
    declarative facts, and an imperative in memory is re-read as a directive in every later
    session. An unknown registry never accuses: it can only fail to detect.
    **MEMORY utilisation is reported too** (observed at 89% of a 2,200-character cap): at
    the cap a new durable fact evicts an old one with NO signal, so the digest says so
    before it happens.

57. **v4 · `v_brief` under-reported the parked backlog — found by the AGENT, in production,
    and written into its own memory rather than into the gap register.** The post section
    was a pure recency window (`ORDER BY week_start DESC LIMIT 14`), so a post parked weeks
    ago — post_1485 exactly — fell out of it while `read_parked_posts` still returned it.
    `read_brief` is the read the planner makes FIRST, and a backlog it cannot see is a
    backlog it plans over. Fixed: the post section is now the 14 most recent UNION every
    PARKED post (bounded at 20), and the outer cap rose 40 → 70 so the sections most at
    risk of silent truncation — the parked COUNT and the asset INVENTORY, the two the
    planner needs to know what it cannot service — are not eaten by the post rows. A test
    asserts `read_brief` and `read_parked_posts` agree on the PARKED set.
    **The second finding is where it was recorded.** A defect the agent discovers belongs
    in the gap register, not only in agent memory, or it is invisible to everyone who does
    not read the agent's memory.

58. **v4 · a Telegram session carries TWO plausible identifiers; accept both, exactly.**
    `sessions.id` (`20260803_134659_591d8efc`) and `sessions.session_key`
    (`agent:main:telegram:dm:2111270140`). Rather than guess which one `pre_tool_call`
    passes as `task_id`, the guard matches EITHER column exactly — same table, same row, no
    LIKE, no prefix. That is deterministic, not widened. Which one the runtime actually
    passes is logged once on first resolution so VERIFY.md can record it and the other can
    be dropped. `ended_at` is NULL for the life of the gateway; nothing filters on it.

59. **v4 · the system prompt advertises disabled tools — ACCEPTED NOISE, with the reason.**
    `agent/prompt_builder.py` at this tag emits *"Terminal backend: ssh. Your `terminal`,
    `read_file`, `write_file`, `patch`, and `search_files` tools all operate…"* from the
    REMOTE-backend branch, built from the terminal-backend setting alone —
    `disabled_toolsets` is not referenced anywhere in that module, so the block cannot be
    suppressed by disabling the toolset. The sentence originates in the remote branch only;
    a LOCAL backend emits host hints instead and never claims those tools. Our committed
    `config.yaml` sets no terminal backend, so the `ssh` value does not come from this
    repo. The `pre_tool_call` hook blocks the calls it invites, so the cost is a wasted
    turn, not a capability. Recorded here so it is not rediscovered as a bug.

60. **v4 · the doubled profile path in the prompt is not ours.** *"reads and writes
    /data/hermes/profiles/artec-brain/profiles/artec-brain/"* — no code in the installed
    hermes-agent at this tag emits that string, so it could not be traced from here; it is
    consistent with a display-only concatenation of a base that already includes
    `profiles/<profile>`. **What was verifiable is that nothing WE write uses it:** the
    entrypoint writes `$HERMES_HOME/profiles/$PROFILE/…` and the transcript module resolves
    `HERMES_HOME / "profiles" / profile / "state.db"`. One `ls` on the container settles the
    rest; it is the decoy shape again — a path that looks plausible and is not the one that
    matters.

61. **v4 · the `v_brief` PARKED fix is ACCEPTED-WITH-EXPIRY on `main`, deliberately.**
    Operator decision D1: the fix may require a migration, C1 has not established whether
    it does, and a schema change to production ahead of that answer is the wrong order. It
    rides the merge instead of the hotfix.
    **The consequence, stated rather than left implicit: until the merge, the Sunday gate
    reads the defective view.** `read_brief` under-reports the parked backlog — post_1485
    and anything else parked outside the 14-row recency window is invisible to it while
    `read_parked_posts` still returns it. **Expiry: the merge.** Mitigation until then: the
    gate operator cross-checks with `read_parked_posts`, which has always been correct.

62. **v4 · the merge gate is a WRITTEN rule, and written rules need a place to be seen.**
    Operator decision D2. Branch protection returns 403 on this plan and the repo stays
    private, so condition 5 cannot be enforced by GitHub:
    *no merge to `main` without a green CI run on the exact commit being merged, every `pg`
    test EXECUTED not skipped.* Recorded in `docs/STAGE-2B-PROGRESS.md` and
    `docs/RUNBOOK.md`; `artec agent-review` reports RED when main's HEAD has no green run
    — including the case that matters most, a commit CI never ran against — and NOT CHECKED
    (never a pass) without a token; the merge commit names the green run id; Checkpoint 1
    reports whether the rule held. None of that PREVENTS a bad merge. Nothing on this plan
    can. It makes one visible, which is the difference between a rule and a hope.

67. **v4 · the CI-query diagnosis was WRONG, and the correction is the finding.** `actions/runs?head_sha=<branch
    sha>` returned `total_count: 0` while three green runs sat in the Actions tab, because
    a run triggered by `pull_request` records the MERGE commit as `head_sha`, not the branch
    commit. `main` worked only because `main` receives push events. Fixed by falling back to
    `?branch=<ref>` and matching `pull_requests[].head.sha`; a test drives a PR-triggered
    run, and another asserts the widened query did not widen what COUNTS as covered.
    **This is "a check aimed at the wrong thing" for the fourth time** (the circular guard,
    the StaticPool lock, the memory audit's blind spot, and now the CI gate reporting a
    false NOT CHECKED on exactly the branch it exists to guard). The pattern is now explicit
    enough to look for by name.

68. **v4 · merge condition 5 was reported ENFORCED for two passes AND WAS NOT. CORRECTED
    2026-08-04.** The claim below is left standing verbatim because the fact that it was
    written, believed, and repeated is the finding — deleting it would erase the evidence.

    > ~~The repo is public and `main` requires the `ci` status check with "require branches
    > to be up to date before merging" on — so a stale `v4-stage-2b` cannot merge and
    > silently revert the cherry-pick that landed on `main` after the branch was cut.~~

    **What was actually true.** A ruleset named `main` existed, and its *contents* matched
    the claim almost exactly — `required_status_checks` with context `ci`, and
    `strict_required_status_checks_policy: true`. Reading the ruleset therefore CONFIRMED
    the claim from every angle anyone had checked. It was inert for three independent
    reasons, any one of which alone would have been enough:

    | Probe | Result |
    |---|---|
    | `GET /branches/main/protection` | `404 Branch not protected` |
    | `GET /rulesets` → the `main` ruleset | `"enforcement": "disabled"` |
    | its `conditions.ref_name.include` | `[]` — targeted NO branches |
    | required context vs check actually published | required `ci`; the job published `test` |
    | **`GET /rules/branches/main`** | **`[]` — zero rules applied to `main`** |

    The last row is the only probe that answers the question that was actually being asked.
    A ruleset that EXISTS is not a ruleset that APPLIES, and every other probe conflates the
    two. **`GET /rules/branches/main` is the probe; everything else is a description.**

    **Armed 2026-08-04**, in this order and deliberately not the other: publish `ci` first,
    observe it published, THEN arm. Enabling a rule that requires a check nobody publishes
    would have locked the repository against every future merge — the failure mode of
    fixing this in the obvious order is worse than the defect. Verified after arming by
    listing, not by claim: `GET /rules/branches/main` returns three rules — `deletion`,
    `non_fast_forward`, and `required_status_checks` carrying context `ci` and
    `strict_required_status_checks_policy: true`.

69. **v4 · full-history credential audit COMPLETED 2026-08-04 — clean.** A full-history scan
    for added credential files returned only `.env.example` and the three `railway.*.json`
    build configs. A full-history diff scan for credential-shaped strings matched only the
    scanner's own patterns: the CI `grep -rInE` step, the equivalent Python patterns in
    acceptance test 2j, a `whsec_…` placeholder in docs, and the
    `whsec_test_secret_000000000` fixture. **No real credential ever reached a commit.**
    Consequence applied: the CI secret-scan step now excludes `ci.yml` AND
    `test_hygiene.py`. A scanner that matches its own definition eventually flags itself and
    gets muted, and a muted scanner is no scanner.

70. **v4 · price reconciliation is job 1's FIRST action, and never writes `config`.**
    Reconciling after the weekly snapshot would produce a report costed against rates
    already known to be wrong, so it runs before it. A delta is MATERIAL when it changes
    `calls_at_cap` — how many reference renders USD 2.50 affords — because that is what the
    cap actually means; material deltas are HELD for `acknowledge_price_table` rather than
    applied, and small ones apply and are reported. A failed pull does not fail job 1: the
    table is flagged stale and the digest says so. fal publishes no pricing API this project
    has confirmed, so `pull_rates` PROBES a candidate and reports exactly what came back —
    it never fabricates a rate, and an unexpected response shape says so rather than parsing
    to silence.

71. **v4 · `prove` may write `config.proofs` because it is NOT an agent tool.** The
    invariant is that no agent TOOL writes `config` — the capability boundary is the
    security model, and it is about what the MODEL can reach. `prove` is CLI/HTTPS,
    operator-driven, registered with no plugin and present in no schema; the seam grep still
    returns zero. Stated so the apparent contradiction is not rediscovered later and
    resolved by weakening the rule, which is the direction these things always erode.

72. **v4 · three defects found by RUNNING the proofs, not by reading them.**
    (a) `sunday-cron` crashed with `TypeError: NoneType is not iterable` — `hermes cron
    list` prints box-drawing characters, the platform decoder raised inside `subprocess`,
    and `stdout` came back None. (b) A prover that raised aborted the whole run and left the
    rest of the matrix unexamined; an error is now recorded as a FAILURE, while
    `NotProvable` stays a third state. (c) `audit-memory` and `sunday-cron` both reported
    confidently about the WRONG HOST — on a development machine `HERMES_HOME` points at a
    personal hermes install, and the audit dutifully found 568 metric-shaped hits in
    somebody else's notes. Both now require the artec plugin to be present under
    `HERMES_HOME` before they will judge anything.
    (d) And `publish-by-slot` — an S1 — reported PROVEN over an empty board. "The pass ran"
    is not "the pass selects correctly"; proving the first unattended action this system
    takes against nothing is exactly the comparison-with-a-missing-side pattern. It now
    fails with that sentence until there is something to select.

73. **v4 · the permissive CI fallback was PROPOSED, BUILT, and REJECTED ON EVIDENCE.**
    Recorded with its reason because a rejected approach is worth more than silence — the
    next reader will otherwise propose it again. The proposal (from the operator) was that
    `pull_request` runs record the merge commit as `head_sha`, so the gate should fall back
    to `pull_requests[].head.sha`. Probed against the real API: PR runs DO carry the branch
    commit as `head_sha`, and `total_count: 0` meant CI had not run for that commit —
    **the original blocking note was correct.** Worse, `pull_requests[0].head.sha` reports
    the PR's CURRENT head, so every historical run claimed the newest commit; matching on it
    would have reported a green run for a commit CI never saw. The branch query is retained
    for ENUMERATION only: a run counts iff its own `head_sha` is the commit.

74. **v4 · THE REVIEW QUESTION, promoted from five instances to a standing check.**
    *For every guard, name what supplies each side of the comparison. If the thing under
    test supplies either side, or one side can be absent while the check still passes, it is
    not a guard.* The five: the circular transcription hook (agent supplied both sides); the
    StaticPool advisory-lock test (the fixture removed the second session); the memory audit
    (patterns aimed at numbers while the danger was capability claims); the CI gate (a false
    NOT CHECKED on the branch it guards); and the permissive fallback above. Now a `FALSE_PASS`
    map in `app/stages/prove.py` — every prover names what would make it report success
    without demonstrating the thing, ENFORCED as a precondition rather than left as a
    comment, because `publish-by-slot` reported PROVEN over an empty board and nothing in
    the code showed it.

75. **v4 · THERE IS NO FAL PRICING API — probed 2026-08-04, option (c) holds.**
    `https://fal.ai/api/pricing` returns an Astro HTML marketing page; `api.fal.ai/pricing`,
    `fal.run/pricing` and `rest.alpha.fal.ai/billing/user_spending` all 404. §7·C5's "pull it
    from the fal API" did not survive contact — the same shape as `web_search` supporting
    Brave, which cost a cycle. Neither a pricing endpoint nor a billing/usage endpoint could
    be confirmed, so: the **seeded invoice rates are authoritative**, staleness is reported
    **by age** (30 days) every night in the digest, and `acknowledge_price_table` becomes the
    operator's **periodic confirmation** rather than an exception path. `pull_rates` is
    retained but OPT-IN, gated on `PRICING_API_CONFIRMED = False`: leaving a reconciler
    probing an endpoint nobody has confirmed exists, reporting "unreachable" forever, is an
    absent check wearing a status message. `endpoint_prices` is never written from a
    fabricated rate; `config` is never written.

76. **v4 · the numbering is CONFIRMED and the caveat retired.** Job 1 is the weekly report
    snapshot (SUN 06:00, operator-confirmed) with price reconciliation as its first action;
    publish-by-slot moved to 7 and is the one artec job with no fixed time, because it is
    slot-driven. The tenth artec-owned job is the bespoke half of learn-ideate. "RECONSTRUCTED,
    NOT RECOVERED" is removed from the registry docstring and a test asserts its absence: a
    caveat that outlives its reason becomes noise, and noise is what people learn to skip.

77. **v4 · THE REGISTERED SET WAS NOT §3's TWELVE — reconciled, with every deviation named.**
    The reconstruction diverged in seven places and four of them were not numbering
    preferences:
    * **The 20:30 → 20:40 → 20:55 → 21:05 chain is a designed sequence.** Assets sync so
      tonight's wishlist reflects last night's drop; doctor so its RED lines exist to be
      carried; digest preparation so the payload is written before the brain reads it.
      Preparation had been set to **21:00 — the same minute as delivery** — which races
      `read_digest` against the row it needs, and the failure mode is an EMPTY DIGEST on a
      night something needed the operator. Restored to 20:55.
    * **`measure-reminder` is RETIRED, not rescheduled.** The digest replaces it, and it was
      the only thing on a bespoke service that sent to Telegram. D1 removes
      `TELEGRAM_BOT_TOKEN` from artec api and artec-scheduler at merge so the brain is
      STRUCTURALLY the sole owner; keeping the job would mean either a job that crashes on a
      missing token or a token that has to stay and a policy that is no longer structural.
    * **`review-expiry-sweep` is FOLDED INTO job 11**, not run beside it. Job 11 already runs
      daily and already reads exactly those posts, and a separate sweep at 20:00 could park a
      review the operator was about to answer at 21:05.
    * **Render fires SUN 10:00 + MON 10:00 retry, not daily.** There is no weekly fal cap:
      the weekly bound IS this job firing twice against the USD 2.50 per-run cap (~USD 5
      worst case). Daily would make it ~USD 17.50 and the digest's `fal · week to date` line
      would report against a bound nobody set — the flat-rate price table again.
    * **`plan-diff` (job 4, SUN 08:00) was ABSENT.** In shadow mode it is the whole point:
      both planners produce a plan and the diff is the evidence for the I16 cutover. Without
      it the Sunday gate has nothing to compare and shadow mode proves nothing.
    * The brain owns **three** cron jobs (3, 5, 12), not two.
    A repo test now asserts the set is EXACTLY twelve, numbered 1–12, with the three brain
    jobs identified and the retired names unable to reappear.

78. **v4 · jobs 2 and 7 are DECLARED UNRECOVERED rather than guessed.** §3's entries for
    those two numbers were never quoted to this build. `publish-by-slot` must exist and sits
    at 2; slot 7 carries `owner=UNKNOWN` and a note saying so. Inventing a twelfth job to
    make the count reach twelve is how a schedule comes to contain something nobody
    designed — the same instinct that made an empty-board `publish-by-slot` report PROVEN.
    **Job 7 must be filled from §3 before registration.**

79. **v4 · `tick()` now fires from the registry.** The registry knew the times and the loop
    did not read them, so six jobs had times nothing acted on — a schedule that existed only
    in a data structure. Dispatch is an explicit `if job.name ==` chain rather than
    `importlib` on `job.body`: a typo in a dotted string would fail at 20:40 on a Tuesday
    instead of in CI. One failing job is logged and the tick continues, because the rest of
    the night's chain still has to run and the digest is what reports the failure.

---

# CONSOLIDATED INDEX — the decisions a future reader would otherwise reconstruct wrongly

Entries 1–79 above are chronological and stay. This index names the ones where a wrong
premise is most likely to be rebuilt from scratch. Format: **decision · why · supersedes.**

**Amendment 1 · metrics** — SUPERSEDES gap-doc C3 ("no agent tool may write metrics").
The replacement rule in full: `metrics` is writable by TRANSCRIPTION ONLY. Every digit the
agent submits must appear in a message the OPERATOR sent in this session, read from
hermes-agent's own message store; `operator_message` must itself be one of those turns;
nothing is written without `confirm: true`; and no tool writes `orders`, `events` or
`config`. The agent may not compute, estimate, infer, round, average, interpolate or carry
forward — including "same as last week" and including arithmetic the operator asked for.
*(Entries 38, 39, 45, 58, 74.)*

**Amendment 2 · video holds every time** — `video_pipeline_proven` is **DELETED**, not set
false. The risk is per-render, not per-pipeline: a boolean would have let one good render
vouch for every later one. No configuration value and no code path can exempt a video.
*(Entries 42, 43.)*

**`endpoint_prices` left `config`** — moved to its own TABLE specifically so reconciliation
can write prices while no tool writes `config`. **The config rule was NOT weakened to make
this convenient**; that is the direction security properties erode. Supersedes
`endpoint_prices_cents` in OPERATOR_CONSTANTS. *(Entries 70, 71.)*

**`KILL_LINE_CAC_SGD_MINOR` / `KILL_LINE_CAC_MYR_MINOR`** — RETAINED but **INACTIVE**, with
`kill_lines_inactive_reason` recorded. Not deleted, so they exist if paid acquisition is ever
introduced; not silently evaluating to "pass", because `learn` reports them as inactive with
the reason. Organic-only means zero paid spend, which makes an absolute CAC line structurally
incapable of firing.

**Relative kill replaces absolute kill; CAC is health-only.** Levers are pruned on weighted-KPI
underperformance versus the cohort median across `min_lever_sample` posts over two consecutive
runs. CAC is reported as *production cost per attributed order (health only, never a kill
rule)*. Supersedes the absolute CAC kill line. *(Entry 32.)*

**Email below `email_min_recipients`** — excluded from lever scoring and reported as *below
measurement threshold*, never as a kill and never as a zero. A one-contact list cannot be
scored.

**The `learning:` block does not exist at the pinned tag.** The real keys are
`memory.memory_enabled`, `memory.write_approval`, `skills.write_approval`,
`skills.guard_agent_created`. There is NO gate-taste toggle. An invented key is silence with
extra steps — it parses, changes nothing, and reports green. Recorded rather than written.
*(Entry 49.)*

**Toolset identifiers verified at `v2026.7.30` (0.19.1).** `kanban` → `todo` drift between
0.18.x and 0.19.x is why a tag bump requires re-verification: a `disabled_toolsets` entry that
matches nothing disables nothing. Both names are listed for that reason, and
`artec agent-review` goes RED if an identifier disappears.

**Tool count six → fifteen.** `deliver_video` is the fifteenth. **The security property was
never the count — it is the absence of capability.** All fifteen remain unable to write
`orders`, `events` or `config`. *(Entry 40.)*

**Four scheduled jobs → twelve**, asserted by repo scan; a thirteenth fails the suite.
*(Entries 77, 78, 79.)*

**The bespoke gate's long-poller is deleted.** The gating logic survives as a library function
reachable over authenticated HTTPS and must never poll. Two pollers on one token is a 409 that
breaks the live gate.

**Accepted, recorded exposure.** `VERIFY.md` and `DECISIONS.md` contain the Telegram user id,
Drive folder and Shared Drive ids, and Railway internal hostnames. None is a credential;
together they are a map, and the repo is public. **Left in place deliberately** — the repo went
public after those commits and history already holds them, so redaction would be theatre.
Redaction is the operator's call, and it is recorded here rather than silently stripped.

---

80. **2026-08-04 · Checkpoint 1 is SPLIT into F1 (pre-deploy, static) and F2 (post-deploy,
    live).** Six of the twelve §14 items — (b) jobs listed back by each scheduler, (c) the
    tools as the agent itself lists them, (e) the digest with a live Brevo count and a real
    Telegram video delivery, (f) both doctors green, (g) the proof matrix, (j) the
    `web_search` probe — require a running deployed container. Producing any of them from a
    branch is a simulation, and simulated live evidence is the packaging/environment class
    that has already put defects into this production. **Supersedes** the single-halt
    checkpoint in §14.

81. **2026-08-04 · merge gate condition 2 was HELD at eleven pg tests, and the eleven were
    not chosen by accident but the other 459 were.** The eleven carry the `pg` marker because
    they exercise Postgres-ONLY semantics: advisory locks (4), sequence allocation including
    `nextval` surviving rollback (4), jsonb round-trip (1), concurrent writers (1), and
    NULL-vs-zero on a real dialect (1). None has a SQLite equivalent. **The other 459 had
    simply never run against Postgres at all** — a coverage decision nobody made on purpose.
    `ARTEC_TEST_SUBSTRATE=postgres` now switches the whole suite onto the real substrate, CI
    runs it as a separate step, and it refuses to fall back to SQLite if
    `TEST_DATABASE_URL` is unset. **Condition 2 is closed by that CI output, not by this
    entry** — see the honest line in the F1 halt block.

82. **2026-08-04 · `docs/RUNBOOK.md` rewritten whole, and every capability claim carries an
    evidence class (P/A/T/U).** A claim without one is a defect in that document. Most of the
    system is currently **T** or **U**: built and tested, never exercised in production. The
    class exists so the difference stays visible instead of being smoothed into prose.

83. **2026-08-04 · STANDING RULE — a blocking note about state OUTSIDE the code is settled
    by a probe, never by an argument.** When Claude Code raises a blocking note about CI,
    GitHub state, Railway, or a third-party contract, counter-reasoning from the navigation
    chat is *also* reasoning from inside a picture that may be wrong. Neither side holds the
    mechanism; both are inferring. **The resolution is a probe. If a blocking note cannot be
    settled by a probe in that pass, it stays blocking.**

    **Why:** a blocking note that CI was not running was raised, argued down on plausible
    reasoning, and withdrawn. It was correct. Seven commits then produced no CI run at all,
    and the absence was read as health. The actual mechanism was held by neither party:
    **GitHub does not run `pull_request` workflows on a PR whose `mergeable` is
    `CONFLICTING`.** No amount of reasoning from either side would have produced that; one
    `gh pr view --json mergeable` did, immediately.

    This is the standing review question in its purest form — one side of the comparison was
    absent, and the check still passed. It generalises: *the argument that a probe is
    unnecessary is never evidence about the thing the probe would measure.*

84. **2026-08-04 · The GUARDS are in scope for the standing review question, and they are
    where it pays best.** For most of this build the question — *for every guard, name what
    supplies each side of the comparison; if the thing under test supplies either side, or
    one side can be absent while the check still passes, it is not a guard* — was applied to
    application code. It had never been applied to CI, to branch protection, or to the test
    fixture. One pass of applying it there produced four defects, all in the verification
    layer and none in application code:

    | # | Defect | Which side was missing |
    |---|---|---|
    | 8 | ruleset required check `ci`; the job published `test` | the required side never existed |
    | 9 | no CI runs at all — PR was `CONFLICTING` | the *evidence* was absent; absence read as health |
    | 10 | secret scan exiting 127, scanning nothing | the scanner side was not running |
    | 11 | fixture built its schema with `create_all`, production uses migrations | the thing under test supplied its own schema |

    Defect 11 is the sharpest: **470 tests took the fixture's word for what the schema was.**
    Nothing asserted its provenance. Recorded as decision 85.

85. **2026-08-04 · The test fixture must take production's schema, not build its own —
    and a test now asserts that.** The first full run of the suite against real Postgres
    produced six failures, every one `relation "post_id_seq" does not exist`. No application
    code was wrong. `Base.metadata.create_all()` creates TABLES from model metadata;
    `post_id_seq` is a SEQUENCE created by migration 0004. Production's schema comes from
    `alembic upgrade head`, so **the fixture was testing a schema that never ships** — the
    packaging/environment failure class, arriving inside the test substrate itself.

    Fixed by running the migrations on the Postgres branch of the `engine` fixture. Guarded
    by `tests/unit/test_schema_provenance.py`, which asserts on Postgres that `post_id_seq`
    exists with `relkind='S'` and that `alembic_version` is non-empty — neither is in
    `Base.metadata`, so their presence is proof of WHICH path built the schema.

    **The asymmetry is stated, not faked.** On SQLite the schema deliberately does not come
    from migrations (decision 7), so that file asserts only that the allocator is reachable.
    A symmetric-looking assert meaning something weaker on one substrate would be the same
    failure the file exists to prevent.

    Two further defects fell out of the repair, both invisible until the fixture stopped
    supplying its own schema:
    - **`v_brief` was created twice** — the migrations create it, and the fixture created it
      again: `DuplicateTable`. Under `create_all` the explicit CREATE was *necessary*
      (create_all knows nothing about views), so the line was right for the old mechanism
      and wrong for the new one. Migrations own it.
    - **`test_advisory_lock_refuses_on_non_postgres` named a substrate it never pinned.** It
      took the generic `engine` fixture, which had been SQLite in every run that ever
      existed. The instant `engine` could be Postgres the test inverted, asserting that
      Postgres raises `NotPostgres`. Repaired by building its own SQLite engine — **not** by
      skipping it on Postgres, which would drop the non-Postgres refusal path from the exact
      run just made canonical.

86. **2026-08-04 · GAP S3 — `app/migrations/env.py` silently ignores an explicitly-passed
    `sqlalchemy.url`. Logged, deliberately NOT fixed this pass.** `env.py:18` reads
    `os.environ["DATABASE_URL"]` directly and never consults
    `config.get_main_option("sqlalchemy.url")`. A programmatic caller that sets the URL the
    documented Alembic way is silently migrated against a different database.

    **This cost real time and is worth the record:** the first repair attempt passed the
    Postgres URL via `cfg.set_main_option`, and `alembic upgrade head` migrated the
    `sqlite://` dummy instead. Six failures became **213 errors** (`relation "posts" does not
    exist`). The fixture now passes the URL by environment variable, which is what `env.py`
    actually reads.

    **Why not fixed now:** production's migration path is the single P-class-proven thing in
    this system — `migrations_current: true`, read from the running container. It does not
    get changed in the same pass as a merge, to repair a test. **Severity S3. Follow-up:
    make `env.py` prefer an explicitly-configured `sqlalchemy.url` and fall back to
    `DATABASE_URL`, with a test that a programmatic caller's URL is honoured. Post-merge,
    on its own branch, with the Postgres suite as the check.**

87. **2026-08-04 · The secret scan must cover HISTORY, not the tip — and "history audited
    clean" was a stale claim resting on a broken scanner.** Decision 69 recorded a clean
    full-history audit. That audit predated the scanner breaking at **exit 127** (comment
    lines written after a backslash continuation, so the shell executed them). The scan had
    not run since, on a **public** repo, across a window in which `HERMES_API_TOKEN` was
    rotated twice after shell exposure.

    A tip scan only ever sees the current tree: a secret committed and later removed stays
    in a public repo's history forever and the tip scan reports clean. CI now runs
    `git grep` over `$(git rev-list --all)` with `fetch-depth: 0`, so the claim is
    re-established on every run instead of once. **Result 2026-08-04: clean, 53 commits, on
    a scanner confirmed to be executing.**

88. **2026-08-04 · The claim "`main` at `a1fa8fd` — CI green" was CHECKED and is TRUE;
    the claim it was entangled with is not.** It was flagged as suspect on the reasoning that
    the workflow might trigger only on `pull_request`. Probed rather than assumed: the
    workflow has carried `on: push: branches: [main]` throughout, and run **30872795253**
    exists on `a1fa8fd`, `event=push`, `conclusion=success`. **The claim stands; no
    correction needed, and it is recorded here as verified rather than left inferred.**

    What was NOT true is the adjacent claim: that run **gated** nothing. The ruleset was
    disabled, so `main` was green *and* unprotected. Green and gated are different facts and
    had been read as one. See decision 68.

89. **2026-08-04 · THE FIRST PRODUCTION DEPLOY OF `main` CRASHED, AND EVERY GREEN SIGNAL WE
    HAD SAID IT WOULD NOT.** Merge commit `3fb7437` passed CI on its own sha — lint, the tip
    and full-history secret scans, migrations, the SQLite suite, and **474 tests on real
    Postgres**. `artec-brain` then went `CRASHED` on first boot, and `artec-scheduler`
    reported `SUCCESS` while every tick failed for 76 seconds.

    **Nothing in the test suite and nothing in CI looks at what ships.** CI builds no image
    and runs no entrypoint; the suite reads application code. The gap between *the code is
    correct* and *the correct code is in the container* had no guard at all — the
    packaging/environment class, the first of the three this build names.

    **B-1 · Three files the entrypoint reads were never `COPY`ed into the image.**
    `deploy/hermes-brain/entrypoint.sh` reads nine `/bootstrap/…` paths; the Dockerfile
    COPYed six. All three missing files existed in the repo and were correct.

    | missing file | guarded how | what the operator saw |
    |---|---|---|
    | `cron-nightly-digest.txt` | none | `cat: No such file or directory` → empty prompt → `hermes cron create` answered *"create requires either prompt or at least one skill"* **and exited 0** → job 12 unregistered → boot guard FATAL → **CRASHED** |
    | `probe_scouting.py` | `\|\| echo WARN` | **silence.** The digest's `scouting: NOT YET PROBED` line reports on a script absent from the image |
    | `audit_memory_report.py` | `\|\| echo WARN` | **silence.** The digest's `agent memory audit` line likewise |

    The two silent ones are the more dangerous. **"NOT YET PROBED" reads as a pending task,
    not as a missing file** — the failure disguised itself as the system working correctly
    and honestly reporting unfinished work. Only the unguarded one crashed, and only because
    the boot check verifies cron by LISTING rather than trusting an exit code that is 0 on
    failure (VERIFY.md V2, probed twice). **The guard that worked was the one built on the
    assumption that the tool lies.**

    Guarded by `tests/unit/test_deploy_packaging.py`, asserting the structural property from
    OUTSIDE both files: *every `/bootstrap/…` path the entrypoint reads must be COPYed.*
    Neither file satisfies it alone and neither can drift without the other noticing. Run
    against the shipped Dockerfile it names all three and fails — **verified, because a
    guard that has never failed is not known to be a guard.** Same shape as the test that
    caught job 2's missing dispatch.

90. **2026-08-04 · The scheduler ticked against a schema that did not exist yet, and a
    missed publish slot is SILENT. Fixed with `wait_for_schema()`.** Railway starts all
    services together; migrations run in the `artec api` release step. The scheduler booted
    at 08:00:49; alembic finished at 08:02:06. For 76 seconds every tick died on

        ProgrammingError: (psycopg.errors.UndefinedColumn) column config.set_by does not exist

    `set_by` is added by migration 0006 — **no code and no migration was wrong.** The
    scheduler was reading a schema that had not landed. Railway reported `SUCCESS`
    throughout, correctly: the process was alive, and the tick loop swallows its own
    exceptions by design so one bad job cannot take the night down.

    **The noise is not the defect. This is:** `tick()` reads `slot_times` *after* the
    registry-job loop and *outside* its per-job `try/except`, so a failed config read skips
    `run_publish_job` and `run_measure_job` entirely. Slot matching is minute-exact
    (`hhmm == at`), and the tick that would have matched is the tick that raised — so **a
    slot falling inside the window is missed for the day, with no retry.** Publishing is the
    one job whose omission is invisible: the digest shows posts still `RENDERED`, which is
    indistinguishable from nothing having been due.

    Fixed by blocking on the actual condition before the loop starts — `SELECT set_by FROM
    config`, the newest migration's column — rather than sleeping a guessed interval. It
    announces what it waits for and raises after 300 s instead of ticking against a schema it
    cannot read. Reading a migration-only column is the same reasoning as
    `test_schema_provenance.py`: assert an object only the migration path can produce.

    **Corrected mid-diagnosis, recorded because the wrong version is the tempting one:** I
    first read this as a double-execution bug, on the grounds that `fired = tick(now, fired)`
    loses the assignment when `tick` raises. It does not — `fired.add()` mutates the caller's
    set in place, so state survives the exception. The real consequence is narrower and
    worse: not a job running twice, but a slot never running at all.

91. **2026-08-04 · GAP S2 — `artec tools registered: 0 / 15`, AND THE EVIDENCE NEEDED TO
    DIAGNOSE IT WAS DESTROYED BY THE LOG RATE LIMIT.** The boot proof counts registered
    artec tools by grepping `hermes tools`; it reported **0 of 15** with the plugin
    confirmed `ENABLED`. Immediately after, twice:

        Railway rate limit of 500 logs/sec reached for replica … Messages dropped: 945
        Railway rate limit of 500 logs/sec reached for replica … Messages dropped: 463

    **The raw `hermes tools` output — the only thing that separates the three hypotheses —
    is inside those 1,408 dropped messages.** The candidates are a genuinely empty
    `register(ctx)`; the plugin's own `✓ Takes effect on next session` meaning a same-boot
    count is structurally premature; or a grep that cannot match the rendered table. **I am
    not picking one from the logs I have, and I am not calling it a proof artifact to make it
    go away** — that would be reclassifying rather than diagnosing.

    Recorded **S2, open**, with the deeper finding stated plainly: **a boot loud enough to be
    rate-limited cannot be relied on for boot diagnostics.** Follow-up: print raw output on
    shortfall and cut boot log volume below the 500/s ceiling so the next occurrence is
    diagnosable. Until then, no claim about artec tool registration in the brain is
    evidence-backed.

    **Not a defect, stated so it is not miscounted:** `⚠ Gateway is not running` appears in
    the same boot. It is emitted by `hermes cron list` at step 9/10; the gateway starts at
    step 10 (`exec hermes gateway run`). It is a snapshot from before the gateway exists, not
    a fault. The gateway genuinely never started — but as a *consequence* of the B-1 FATAL,
    not as an independent problem.

92. **2026-08-04 · The corrected record did not reach `main` with the merge, and was
    reported as delivered.** Decisions 83–88 and the RUNBOOK corrections were committed to
    `v4-stage-2b` as `1ed03bb` **after** the merge commit `3fb7437` was created. The merge
    therefore carried the code and not the corrections, so `main` continued to state
    "condition 5 is ENFORCED" — the exact false claim that pass existed to retract — while
    the checkpoint reported it corrected.

    Caught only because a later edit failed to find its anchor text in a file that had been
    checked out from `main`. **A document correction is not delivered when it is written; it
    is delivered when it is on the branch that ships.** Merged forward here.

93. **2026-08-05 · THE DISCONNECTED GUARD — a named failure class, and a mechanical check
    for it.** Three defects now share a shape that is NOT the wrong-comparison pattern. Each
    guard was well written, correct in isolation, and **connected to nothing**:

    | # | guard | why it was inert |
    |---|---|---|
    | 1 | branch-protection ruleset | enforcement disabled, `include: []` — targeted no branches |
    | 2 | `audit_memory_report` | correct logic, pointed at paths where memory does not live |
    | 3 | `validate_required_config` | correct logic, called by nothing outside `tests/` |

    The standing review question does not catch these. It asks *what supplies each side of
    the comparison* — and in all three the comparison was fine. Nothing ran it. The question
    that catches them is different: **is this guard reachable from production code at all?**

    Each of the three was found by accident, one pass at a time, after it had already failed
    to protect something. `tests/unit/test_no_disconnected_guards.py` finds the fourth before
    it ships: every named guard must have at least one reference outside `tests/`, and a
    guard that does not is named in the failure.

94. **2026-08-05 · A DEFAULT IS A FALLBACK, NOT A SETTING — and from outside the two are
    indistinguishable.** Wiring `validate_required_config` into both boot paths took the
    scheduler down and was right to. Verbatim:

        app.config.RequiredConfigMissing: required config missing for role 'scheduler':
        email_review_expiry_days (absent), facebook_page_id (absent),
        linkedin_organization_urn (absent), max_output_megapixels (absent),
        render_run_cap_cents (absent), video_review_expiry_days (absent)

    Six keys; two were new. **The other four had been absent from production the whole
    time**, including `render_run_cap_cents` — the render spend cap — and both review expiry
    windows. Nothing ever failed, because every read passes a default:
    `get_config(session, "render_run_cap_cents", 250)`. The system reported a cap it was not
    reading, and no external observation could have distinguished that from a configured one.

    **Root cause:** a key added to `OPERATOR_CONSTANTS` never reaches a database that was
    seeded before that key existed. `artec config seed` had to be run by hand and nothing
    ever said when. Fixed by calling the non-destructive `seed_config` at boot BEFORE
    validation, so a new constant arrives with the deploy that introduces it — the one moment
    anyone knows it is needed. Ordering matters and I got it wrong first: validate-then-seed
    is an outage, seed-then-validate is a fix.

95. **2026-08-05 · A WARNING IN A COMMENT IS NOT A GUARD.** Twice in two passes a correct
    diagnosis sat directly above the code that ignored it — the always-wrong FATAL, then the
    log flood that the flooding script's own docstring warned against. A comment is read by a
    human after the fact; a guard is read by the machine before. **If the code must not do X,
    assert it.** This is the standing review question applied to the FORM of a warning: a
    comment supplies no side of any comparison.

    Corollary, earned separately: **a check that is always wrong must be fixed or removed,
    never made louder.** Escalating severity is only valid once the check's correctness has
    been established independently of the thing it checks. Severity is not evidence, and a
    loud wrong check converts a cosmetic defect into an outage.

96. **2026-08-05 · The memory audit reported `clean — 0 file(s) scanned`, and a test asserted
    that it should.** Its first ever production run, the same day the agent acted on a stale
    capability claim for an entire session. Two independent defects:

    1. **Wrong paths.** It scanned `$HERMES_HOME/{MEMORY.md,memories,skills}`. hermes-agent
       stores memory under the ACTIVE PROFILE — the real path is
       `/data/hermes/profiles/artec-brain/memories/MEMORY.md`. This is the identical
       decoy-path trap already documented for the transcript store in VERIFY.md.
    2. **`clean: not hits` was true for zero files.** A scan of nothing reported health.

    The second was **encoded in its own test**: `test_the_audit_is_clean_on_empty_memory`
    supplied an empty directory and demanded the word "clean". The guard's test asserted the
    bug the guard existed to catch. Zero files is now its own state — `nothing_scanned`, NOT
    clean, reporting every path it searched.

    **The stale claim was gone before the purge ran.** The agent overwrote it itself after
    its live self-correction; memory now names `read_draft_posts` as the designed way to run
    the weekly gate. The tooling VERIFIED the state; it did not remediate it. Recording this
    because the opposite is the tempting version.

    Scope correction: the first fixed run scanned 401 files because both scripts recursed
    `skills/`, which holds shipped skill packages — documentation and templates, not
    autonomously written memory. Narrowed to `MEMORY.md`, `memories/`, `memory/` → 2 files.

97. **2026-08-05 · The live catalog reaches the agent as DATA on the read it makes first.**
    `read_brief` now emits the tool list generated from `HANDLERS` — the dispatch table
    `register(ctx)` populates and every tool call resolves through. Not a manifest, not a
    document: it cannot name a tool that is not callable, nor omit one that is. Same pattern
    as `_spend_posture_lines`.

    **The limit, stated rather than blurred:** this makes the truth present and authoritative
    on the first read. It cannot reach inside the host's memory injection — nothing in a
    plugin can. What it removes is the excuse, not the possibility. Separately, the boot-time
    audit compares against the plugin MANIFEST, which is what *should* register; only a
    session observes what *did*. `test_the_manifest_is_not_the_live_catalog` keeps that
    weakness written down.

98. **2026-08-05 · PAGE TARGETING — and the negative claim is the one that needed proving.**
    Facebook and LinkedIn are OAuth'd through a personal account that administers the Artec
    page. Nothing targeted a page, so both would have published to the personal profile —
    silently, and SUCCESSFULLY, because Upload-Post returns success either way and nothing in
    the response distinguishes the two outcomes.

    It breaks MEASUREMENT, not only branding. LinkedIn exposes no member-level analytics at
    all; Facebook analytics requires `page_id`. Published personally, `metrics` stays NULL
    forever while `learn` scores those channels as though it had measured them — stale
    reading as zero, arriving through the publisher.

        facebook_page_id          = 574903736241765             "Artec Malaysia"
        linkedin_organization_urn = urn:li:organization:97212204 "Artec Malaysia"

    **The same account administers `urn:li:organization:74925843` — "Tech Up Advance l
    GoTechUp", a different business.** Configuring the right URN proves the right URN works;
    it does NOT prove the wrong one is unreachable. Those are different claims, and
    `publish-by-slot` already reported PROVEN over an empty board on exactly that confusion.
    `ForeignPageTarget` refuses it by name.

    Publish REFUSES rather than defaults: there is no code path that publishes facebook or
    linkedin without an explicit target — the guarantee is the absence of a fall-through.
    Doctor compares config against Upload-Post's LIVE listing and prints the other
    administered organisation, so the operator sees what the system is choosing between
    rather than trusting that it chose.

    **The fake runs the real refusal.** `FakeUploadPost` delegates to the real `_page_fields`
    rather than accepting `page_targets` and ignoring it — a permissive fake would let the
    dry run publish with no page configured and report success, which is the production
    failure reproduced inside the harness built to catch it.

99. **2026-08-05 · `digest_date` is the DELIVERY date, computed in ONE place.** Job 11 wrote
    `now(UTC).date() - 1 day` (the data window it covers); job 12 read `date.today()` (the
    container's UTC date). Both correct in isolation; nothing compared them. On 2026-08-04
    the first digest this system ever produced was written under `2026-08-03`, looked for
    under `2026-08-04`, and the operator was told job 11 had not run — about a job that had
    run on time, unattended, for the first time.

    `app/digest_dates.py::digest_date_for` is now the only place either side computes it.
    Keying by the data window would orphan Sunday, since job 12 does not run that day.
    Job 12 has THREE states and never collapses them: deliver · a digest exists under a
    different key (named as `digest_date_mismatch`, both dates printed) · nothing at all.

100. **2026-08-05 · TWO CHECKS ABOUT ONE TELEGRAM TOKEN, AND ONE HAD TO BE RED WHATEVER THE
     OPERATOR DID.** D1 made the brain the sole Telegram owner *structurally*: on a bespoke
     service the token must NOT exist, because two pollers on one token is a 409 that breaks
     the live gate. `telegram ownership` encoded that correctly. `telegram bot`, forty lines
     later, called `get_me()` unconditionally.

     So the moment the D1 post-deploy step deleted the token from `artec api` and
     `artec-scheduler` — doing exactly what the first check demands — the second began
     failing forever:

         🚨 DOCTOR RED — telegram bot: TelegramError: telegram getMe failed: Not Found

     **A pair of checks that cannot both pass is not two guards. It is one guard and one
     false alarm**, and a permanent false RED is how a real RED stops being read. Fixed:
     `getMe` runs only where `HERMES_HOME` is set — the brain, the one service that is
     supposed to hold the token.

     **Both readings were real, and they are not the same finding.** The RED dated
     2026-08-03 PREDATES the deletion, so at that time it meant something else entirely: the
     token on that service was genuinely wrong. Collapsing "was true then for reason A" into
     "is true now for reason B" would have hidden one of them.

101. **2026-08-05 · WITHDRAWN POSTS — a flag, not a status.** `post_1488` (facebook) and
     `post_1489` (linkedin) published on 2026-08-04 at 01:00, before page targeting existed,
     to the personal profile of the OAuth account. The operator removed both at source.

     They *were* published — that is a true fact about the ledger, and `external_post_id`
     stays set, which is what makes the never-republish guard keep refusing them. Withdrawal
     is an ADDITIONAL fact, not a replacement. **`posts` is the ledger of record; a
     withdrawal is an event on the ledger, not an erasure of it.** Rewriting history to make
     the present tidy is how a system starts lying about what it did.

     `learn` excludes them and reports `withdrawn` — never zero. Left alone those rows say
     facebook and linkedin published and earned nothing, marking down two channels for a
     TARGETING defect that has nothing to do with the creative. That is the
     `email_min_recipients` trap arriving on two other channels.

     Stamped in migration 0008 rather than by hand, so a restore from backup marks them too;
     a hand-run would be forgotten and the false negative would silently return.

102. **2026-08-05 · THE FOURTH NEVER-ARRIVED-DATA INSTANCE, proven by timestamp.** The
     digest carried `🚨 DOCTOR RED — endpoint price table: unpriced: ['fal-ai/clarity-upscaler',
     'fal-ai/qwen-image-2512/lora']`. Unpriced endpoints are uncallable by design, and
     `clarity-upscaler` is the only live model path — so ENHANCE could not run and Sunday's
     render would have parked everything needing it.

     Three possibilities needed separating: rows absent, rows present but unreadable, or rows
     present with a shape doctor rejects. **The rows were absent.** The proof is the `as_of`
     on the rows that exist now:

         fal-ai/clarity-upscaler  30000 micros  per_megapixel  as_of=2026-08-04 15:53:11

     15:53:11 is the exact minute `seed_config` first ran at boot, from decision 94's fix.
     `seed_prices` is called *inside* `seed_config`, so the same defect that hid four config
     keys was hiding the entire price table.

     | # | what never arrived | why nothing noticed |
     |---|---|---|
     | 1 | `post_id_seq` | fixture used `create_all`; production uses migrations |
     | 2 | `v_brief`'s PARKED union | created by 0001 only; alembic never re-runs it |
     | 3 | four config keys incl. the render cap | every read passes a default |
     | 4 | the whole `endpoint_prices` table | seeded by a hand-run nothing scheduled |

     **The class is now named: data that is correct in the source and never reaches
     production, because arrival depends on something nobody scheduled.** The counter in
     every case is the same — make arrival structural: a migration, or a boot-seed.

103. **2026-08-05 · A FILTER APPLIED TO A GUARD'S OUTPUT IS PART OF THE GUARD.** CI Lint
     failed on an unused variable that my local check had been reporting the whole time. I
     was running `ruff check . | tail -1`, which prints

         No fixes available (1 hidden fix can be enabled with the --unsafe-fixes option)

     while the line ABOVE it said `Found 1 error`. I read the last line and called it clean.

     The guard was correct and the *reading* of it was not — which makes the pipeline, not
     the tool, the thing that failed. Same shape as everything else here: looking at the
     wrong part of the output and treating absence-of-visible-error as a pass. Grep for the
     verdict line, never trust position.

104. **2026-08-05 · `learn` SCORED SIX LEVERS AT ZERO FROM A WEEK NOBODY MEASURED —
     invariant 2, breached at the one place it matters most.** Probed against week
     2026-08-03's real shape (five PUBLISHED, zero `metrics`, two withdrawn):

         lever=angle    value=build     score=0E-10 n=3 verdict=test
         lever=channel  value=instagram score=0E-10 n=2 verdict=test
         lever=channel  value=tiktok    score=0E-10 n=1 verdict=test
         lever=cta_type value=shop      score=0E-10 n=3 verdict=test
         lever=hook     value=time      score=0E-10 n=3 verdict=test
         lever=slot     value=lunch     score=0E-10 n=3 verdict=test

     Six real verdicts from no measurement at all. **IDEATE reads these**, so Sunday's plan
     would have been shaped by the claim that `hook=time` underperformed — when nobody
     looked.

     Three defaults produced it, each reasonable in isolation: `eng_n.get(value, 0.0)` for
     absent engagement, `traffic[value] = 0.0` for no events, `sales_score = 0.0` for no
     orders. Together they assert "these levers performed at the bottom of the range".

     **The honest pattern already existed one function away**: `channel_cac` reports
     `unmeasurable (no spend configured)` rather than scoring a zero CAC. It had simply never
     been applied to the weighted levers.

     THE RULE, which generalises beyond this file: **a post is MEASURED if it carries any
     evidence — a metrics row, an event, or an order. The lanes are separate for COMBINATION,
     not for EXISTENCE.** Unmeasured posts are excluded from scoring and NAMED, exactly as
     withdrawn posts are. Never imputed, never defaulted.

     Testable without an LLM, which is what made it closable: the agent does interpretation,
     **SQL does arithmetic**. The scoring is reachable directly.

105. **2026-08-05 · THREE TARGET STATES, AND `None` MEANT TWO OF THEM.**
     `verify_publish_target` returned `None` both when the platform confirmed the right page
     and when the platform named no owner at all. **"Verified correct" and "could not be
     checked" are different facts**, and merging them is how an unperformed check reads as a
     pass — the identical conflation as job 12 announcing that job 11 had not run.

     | state | meaning | surfaced |
     |---|---|---|
     | `verified` | platform named an owner and it matches | no |
     | `mismatch` | platform named an owner and it is WRONG | 🚩 WRONG SURFACE |
     | `unverified` | platform named no owner — check could not run | ❓ TARGET UNVERIFIED |

     Sunday publishes unattended by operator decision, so prevention is not available and
     **surfacing is the entire safety net.** Both non-verified states reach NEEDS YOU the
     same evening, so a wrong-surface post is deletable in hours rather than days —
     `post_1488` and `post_1489` sat on a personal timeline because this outcome existed only
     in a run log.

     **`target_alerts` had to be added to the NEEDS YOU emptiness tuple.** That section
     renders one line and stops when `empty` is true, so a section omitted from the tuple can
     never be seen — which would have reproduced the very failure being closed, one layer
     deeper.

106. **2026-08-05 · AN ABSENT ROW CAN ONLY MEAN "DID NOT RUN" IF A RUN THAT HAPPENS ALWAYS
     LEAVES ONE.** `agent_runs` held two rows and both were operator conversations. Jobs 3, 5
     and 12 had never written one. On 2026-08-09 jobs 3 (07:00) and 5 (09:00, THE GATE) fire
     for the first time — and if either failed there would be no record it ran at all, only
     its absence, indistinguishable from the cron never firing.

     Migration 0009 adds `agent_runs.trigger ∈ 'cron' | 'manual'`; without it a job the
     operator started by hand is indistinguishable from Sunday's firing, and "the Sunday gate
     ran" would be true of a Tuesday run-now. Existing rows backfill to `manual` — both are
     operator conversations, and leaving them NULL would make the column's first real use
     ambiguous. A brain-side stdout line prints at execution, matching the scheduler's
     pattern, so a failure BEFORE the database write is still visible.

     Same reasoning as verifying cron registration by listing rather than by exit code: the
     absence of a signal is only evidence if the signal is guaranteed when the thing happens.

107. **2026-08-05 · D-vii — `next_week_start` IS WRONG ON SUNDAYS, AND IS DELIBERATELY NOT
     FIXED BEFORE 2026-08-09.**

         next_week_start(2026-08-09) == 2026-08-03

     It computes `today - timedelta(days=today.weekday())`. Sunday's `weekday()` is 6, so it
     returns **the Monday that already passed** — despite its name. Sunday's `ideate`
     therefore targets week 2026-08-03: the same week as the nine existing DRAFTs, which also
     holds five PUBLISHED posts. And `ideate` tops up to cadence counting existing
     non-REJECTED posts, so with nine DRAFTs already present it will likely add **nothing**.

     **The failure mode is a Sunday that looks like it worked.** Not a crash — a plan that
     was never made, and a gate presenting stale drafts as the new week.

     **OPERATOR DECISION (D-vii): it stays as it is through 2026-08-09**, for three reasons
     recorded so this is a decision and not an accident:
     - `learn` now correctly returns `cold_start` / `unmeasured` with `score=None`, so there
       is no signal to shape a fresh plan with this week regardless
     - the nine drafts give a real gate → render → publish → measure exercise, which is what
       a first run is for
     - changing a Sunday job's date arithmetic under 72 hours before it first fires is the
       class of change that has taken this system down three times

     **What converts the deferral into a decision:** the gate states, in its opening summary,
     which `week_start` it is gating, how many drafts it found, and the age of the oldest. A
     wrong week must be visible on the screen, never inferred. Fix deferred to after Sunday.

108. **2026-08-05 · post_1487's HTTP 400 — a constant defined twice and read nowhere.
     Instance #5 of the disconnected guard.**

         🔁 RETRY — post_1487 · youtube · publish:
            UploadPostError: upload-post /upload failed: HTTP 400

     `PLATFORM_RULES["youtube"]["max_title"] = 100` has existed since the client was written.
     `grep -rn max_title` returned **two definitions and zero reads** —
     `app/integrations/upload_post_client.py` and `app/config.py`. `validate_for_platform`
     checks `max_caption` and video duration and never checked the title. So publish sent
     `f"{caption}\n{tracked_url}"` into a 100-character field whose caption is bounded only
     by `max_caption: 5000`.

     **A 400 is a contract answer, not a transient.** The platform said the field was too
     long; `retry_post` would have failed identically every time, which is how third-party
     contract drift gets mistaken for flakiness. Diagnosed by reading the contract, not by
     retrying — and `retry_post` refuses posts holding an `external_post_id` anyway, so a
     retry would have been refused or failed the same way.

     **It is systematic for the channel, not a one-off.** `post_1496` in Sunday's draft set
     is also youtube and carried the identical defect, which is why the fix is a rule rather
     than an edit to one row.

     `description` is a documented shared field (decision 1, verified against openapi.json)
     and had **never been sent**. Sending it is what makes bounding the title lossless: the
     full caption and tracked URL still travel, in the field built for them, instead of being
     truncated away. Platforms with no `max_title` are unchanged — a fix for a broken surface
     must not alter four working ones.

     Guarded twice: `test_every_declared_max_title_is_actually_enforced` (adding a limit to
     another platform fails unless it is honoured) and
     `test_declared_platform_limits_are_read_somewhere` in the disconnected-guard suite —
     **a limit that is declared and never consulted is not a rule, it is a comment that looks
     like enforcement.**

109. **2026-08-05 · D-6 — fifteen tools, derived and not declared.** v4 §4 specifies
     fourteen; the seam ships fifteen. The extra is `deliver_video`, and it is a legitimate
     implementation of §5.1's native Telegram delivery rather than scope creep: delivering a
     video AND recording the delivery receipt cannot both live inside a tool the spec
     declares READ ONLY, so the operator elected a fifteenth tool rather than weakening
     `read_digest`. Read stays read; delivery is explicit.

     `EXPECTED_TOOL_COUNT = len(HANDLERS)` — the dispatch table itself, the object every tool
     call resolves through. **A literal `15` written anywhere else is a second source that
     can disagree with the first**, and this build has spent whole passes on exactly that: a
     config manifest nothing validated, a view whose SQL drifted from its migration, a price
     table seeded by a hand-run nobody scheduled. `SPEC_TOOL_COUNT = 14` sits beside it so
     the deviation is stated rather than implied.

110. **2026-08-05 · C.5 — the publish gate states its own state, either way.**
     `confirm_first_publish` was ambiguous to everyone for a week: inferred once from the
     existence of published posts, contradicted once, and settled only by reading the row.
     The inference was reasonable and wrong — `post_1483`/`post_1484` published in the v3
     era, before the gate was wired into job 7 at all.

     The digest now states it nightly in SPEND & HEALTH. **An unstated OPEN is exactly as
     dangerous as an unstated CLOSED, in opposite directions**: closed means nothing publishes
     and the digest looks like a quiet week; open means job 7 publishes unattended the moment
     anything is RENDERED. When closed it also names how many posts it is holding and what
     clears it.

111. **2026-08-05 · C.7 — memory utilisation is reported, never auto-pruned.** Agent memory
     sat at 82% of its 2200-char cap. At the cap a new durable fact evicts an old one **with
     no signal of any kind** — the store simply forgets something and nothing says which.

     YELLOW above 80%, RED above 95%, in the digest every night. Severity is explicit rather
     than one threshold because "approaching the cap" and "actively losing things" are
     different operator decisions.

     **Report, do not prune.** An automatic deleter would be a write tool nobody reviewed,
     operating on the one store the agent owns, whose failure mode — deleting the wrong note
     — is silent and permanent. The RED line says "prune by hand" and says that nothing
     prunes automatically, so the operator never assumes it was handled.

112. **2026-08-05 · A HIDDEN CLOCK IS AN UNNAMED SIDE OF A COMPARISON.** The gate summary's
     first version tested `age >= 7` and read the clock internally. Week 2026-08-03 runs Mon
     03 → Sun 09, so on Sunday it is day **6**: the warning would never have fired on the one
     day it was written for. It was caught only because the test pinned a date instead of
     reading `date.today()`, and fixed by taking `today` as a parameter.

     A function that reads the clock supplies one side of its own comparison, and no test can
     pin it from outside. The same reasoning as every other instance in this file — name both
     sides, or the check is not a check.

113. **2026-08-05 · BOTH HELD SURFACES PROBED AND CONFIRMED HELD — and a contradiction in my
     own reporting corrected.** I had described `post_1499` as reaching "EMAIL REVIEW +
     review_email" and, in the same report, called it "the only irreversible surface into its
     first **unattended** run". Those cannot both be true. They are not: **the email lane is
     held**, and the second statement was wrong.

     The predicate, verbatim from `app/stages/publish.py::skip_reason`:

         if post.channel == "email":
             if post.status != "APPROVED_TO_SEND":
                 return ("email never auto-publishes: it is the only irreversible surface, "
                         f"so it requires an email review approval (status is {post.status})")
             decision = (post.email_review or {}).get("decision")
             if decision != "approve":
                 return (f"email review decision is {decision!r}, not 'approve' — no code "
                         "path sends an email without a recorded approval")

     **CONNECTED, not merely declared** — the thing five previous guards were not.
     `skip_reason` is called at `publish.py:195`, inside `publish()`'s own loop, before any
     post reaches the publishable list. Job 7 calls `publish()`. So the scheduler's path and
     the CLI path are the same path.

     **Two independent conditions, and the second is the load-bearing one.** A status is
     reachable by routes the review never took; a recorded decision is not. And the gate
     cannot shortcut either: `record_gate_decision` sets `APPROVED` or `REJECTED` and
     **never `APPROVED_TO_SEND`** — only `review_email` and `review_video` write that. So
     Sunday's gate approval puts `post_1499` at RENDERED → EMAIL REVIEW in the digest → a
     second, separate approval. **Two humans-in-the-loop, not one.**

     The video lane is held by the same predicate with a third condition, and the third is
     the one that cannot be forged: `telegram_message_id` is read from **Telegram's own API
     response** inside `deliver_video` (`body["result"]["message_id"]`). `review_video` only
     READS it. An approval for a video nobody was shown is therefore not constructible,
     rather than merely disallowed — the receipt is supplied by a party that is neither the
     approver nor the agent.

     A clean probe is a result. Recorded so the next reader does not re-derive it.

114. **2026-08-05 · A8 WAS NOT WIRED: plan-diff reported a one-sided comparison as
     disagreement. Instance #6.** With `plans_shadow` empty for the week, `build_diff`
     returned `agreement[field] = 0.0` for every field, and `print_diff` fell through to

         == OVERLAP — none: the planners filled disjoint slots ==

     **Both statements are false when one planner produced nothing.** "Disjoint slots" is a
     claim about two planners that both spoke, and `0.0` reads as "they disagreed about
     everything" when the truth is that there was nothing to compare. Same defect as `learn`
     scoring an unmeasured week at zero, as job 12 announcing that job 11 had not run, and as
     the memory audit reporting `clean — 0 files`: **an absence reported as a value.**

     A8 exists precisely to prevent plan-diff degrading into a one-sided comparison that
     reports agreement with itself — the failure job 2's missing dispatch would have caused
     from the other direction. The rule was written and the code did not implement it.

     **This is not an edge case on 2026-08-09.** Job 3 — the agent's LEARN→IDEATE at 07:00 —
     has never completed a session in production, so an empty `plans_shadow` at 08:00 is the
     LIKELY state, and plan-diff runs between ideate and the gate.

     Fixed: `agreement` is `None` when nothing was compared and renders as
     `— (nothing to compare)`, never as a percentage; the payload carries `one_sided`,
     `bespoke_count` and `agent_count` as named facts rather than leaving them to be inferred
     from an empty list; and `print_diff` opens with a banner naming which planner was silent
     and pointing at job 3's `agent_runs` row. The "disjoint slots" line is now unreachable
     when one side is empty.

     Three states, never collapsed: nothing planned at all · one planner silent · both spoke.

115. **2026-08-05 · A `run-now` MIRROR FOR JOB 12 CANNOT EXIST ON `artec api`, and that is
     D1 working rather than a gap.** Job 12 delivers through Telegram. D1 makes the brain the
     SOLE Telegram owner *structurally* — `artec api` has no token, deliberately, because two
     pollers on one token is a 409 that breaks the live gate. Building a delivery route on
     the API would mean giving that service a token, which is the one thing D1 exists to
     prevent.

     So the pair splits, and only one half was ever mirrorable:
     - **job 11 (prepare) ALREADY had a route** — `POST /commands/digest-prepare`. Nobody had
       checked; the enumeration in A.1 is what established it.
     - **job 12 (deliver) cannot have one here.** Delivery stays on the brain.

     What was missing and is now built: `POST /commands/digest-preview` returns **the exact
     message job 12 would send**, at any hour, without sending it and **without marking the
     digest delivered**. A preview that consumed the digest would make the real 21:00
     delivery a no-op — a preview with a side effect nobody asked for.

     It reads through `_read_digest_impl`, the same function job 12 calls, with the same
     `digest_date_for()` and the same three-state logic. **A manual run must not be able to
     produce a result the cron could not**; sharing the function is what makes that true
     rather than merely intended.

     Jobs 3 and 5 remain brain-only for the same reason job 12 does: both run as
     hermes-agent sessions, and a session cannot be started from the API without the token.

116. **2026-08-05 · A DRAFT'S SERVICABILITY WAS UNANSWERABLE UNTIL AFTER THE SPEND.**
     `wishlist.match()` inspects **PARKED** posts only, and a DRAFT has no wishlist — a
     wishlist is written when a post PARKS at render. So the sync's nightly
     *"wishlist match: no parked post can be serviced yet"* said nothing whatsoever about the
     nine drafts, and the question had never been asked of them.

     The consequence if they cannot be serviced: job 6 parks everything the operator approved
     at 09:00, and the week produces nothing. **No crash, no error line — another Sunday that
     looks like it worked**, which is the failure shape this build keeps finding.

     `app/toolbox/match_probe.py::probe_drafts` calls the SAME `find_candidates` the render
     path calls (`render.py:174`) with the SAME arguments derived from `channel_media`. No
     LLM, no fal, no ffmpeg, no writes. Exposed at `POST /commands/match-probe` and run at
     scheduler boot so the answer arrives without anyone holding a bearer token.

     **What it cannot answer, stated so it is not over-read:** whether the ffmpeg pipeline
     will succeed on a real file. It answers whether an asset EXISTS for render to try. No
     read can answer the other question, and `video-pipeline` stays unproven until real
     footage passes through it.

117. **2026-08-05 · LIST 3'S SINGLE CONTACT IS A REAL CUSTOMER — the first send is not a
     rehearsal.** Not the operator's own address. So `post_1499` is a real marketing email to
     a real person, written by a model, on a path that has never run, to the one surface in
     this system with no remedy.

     The EMAIL REVIEW block now states, beside the live recipient count, that recipients are
     **real subscribers, not test addresses**, and **whether a test send has been performed
     for this copy** — naming the count and date if so, and saying plainly that none has if
     not. An absent line reads as "nothing to report", which is the same family as every
     other defect here: an absence that must be noticed rather than one that announces
     itself.

     **REPORTED, NOT GATED.** `send_test` is not mandatory and `approve` is not blocked on
     it — a test asserts that explicitly, so a later reader does not "tighten" it into a
     gate. The operator decides; the system surfaces. What this removes is having to remember
     the question, not the freedom to answer it either way.

     `learn` still marks email `insufficient_sample` below `email_min_recipients`: sending
     proves the path, it does not make one recipient a signal.

118. **2026-08-08 · A CONNECT WITH NO TIMEOUT IS NOT A CONNECT THAT FAILS — IT IS ONE THAT
     NEVER ANSWERS.** `artec-scheduler` logged `Starting Container` at 09:05:14Z and nothing
     for the next nineteen minutes, against a baseline where the boot banner lands **0.3
     seconds** after start. Not crashed: 66 MB resident, 0% CPU, ~0 bytes transmitted. It was
     blocked on its first connection inside `wait_for_schema`, with nine jobs behind it.

     `wait_for_schema` catches `Exception`, announces the wait and polls. It could never run.
     libpq's `connect_timeout` defaults to `0`, meaning wait forever, so the call it was
     written to retry never returned and never raised. **The retry loop was a guard connected
     to nothing** — and the sentence that names the fault was already in this repo, written
     by me about the boot-time match probe: *"`try/except Exception` does not catch a hang,
     and I reached for it as though it did."* The same words applied to the function whose
     entire job was making boot safe.

     `app/db.py` now bounds the connect (`DB_CONNECT_TIMEOUT_S`, default 10s) plus TCP
     keepalives and `pool_recycle`, which matter for this service specifically: the scheduler
     holds a pooled connection across hours of sleeping, and a silently dropped socket is not
     closed, merely never answered again — `pool_pre_ping`'s `SELECT 1` would hang on it
     exactly as the connect did. Bounding it turns a hang into the exception the existing
     loop already handles.

     **The test has teeth, checked rather than assumed.** Against RFC 5737 TEST-NET-1, the
     unfixed engine hung past 25s and was killed (exit 124); the fixed one raises inside the
     bound. If `connect_args` ever stops reaching libpq the test hangs rather than passing.

     **I ALSO GOT THE CAUSE WRONG FIRST, AND THAT IS THE MORE USEFUL HALF.** I had removed
     the boot proof sweep the deploy before and blamed it, in a comment committed to
     `scheduler.py`. The deploy logs exonerate it: deployment `46e96205`, the commit that
     ADDED the sweep, booted normally — banner 08:49:45, full proof matrix 08:50:35, fifty
     seconds, nine jobs unharmed. The service that hung was the NEXT deploy, whose only diff
     was six lines inside `prove_brevo_send`. I had a suspect I had recently touched, a
     symptom that fit, and I stopped looking; the log that cleared it was one query away.
     The comment is corrected in place rather than deleted. The sweep stays out on its own
     merits, which never depended on that story.

119. **2026-08-08 · THE SCOPE OF A GUARD IS PART OF THE GUARD.** The AST guard that forbids
     importing `app` on the brain scanned `plugins/` and stopped, while seven scripts under
     `deploy/hermes-brain/` are COPYed into the same image and run by the entrypoint. It was
     correct about half the code it needed to cover, and passing.

     Worse there than where it was caught. `read_digest` failed loudly in front of the
     operator; every brain bootstrap script runs as `python /bootstrap/<name>.py || echo
     WARN`, so a `ModuleNotFoundError` would not raise, would not crash, and would not read
     as an error — the digest would go on reporting a memory audit for a script that died on
     its import line. Guard extended to both trees.

120. **2026-08-08 · THE THREE BRAIN PROOFS, AND THE FALSE PASS THAT TURNED UP UNINVITED.**
     `agent-session`, `sunday-cron` and `audit-memory` reported NOT PROVABLE forever, and
     correctly: `prove.py` runs on artec-scheduler and the evidence — the hermes message
     store, the cron registry, the memory files — is on the brain's volume. A prover pointed
     at a machine that cannot hold the evidence never proves anything, however right it is.
     `deploy/hermes-brain/prove_brain.py` runs them where the evidence is and MERGES into
     `config.proofs`, never replaces it: overwriting would silently un-prove the other six.

     Smoke-run on a developer machine, `sunday-cron` read a perfectly healthy `hermes cron
     list` — well-formed, resolving to +08:00 — belonging to somebody's **trading bot**:
     `['hermes-trading-daily-review', 'hermes-trading-weekly-tactical']`. That is the
     registered false pass verbatim ("a cron listing from any hermes install, not artec's"),
     arriving by accident on the first run. `hermes cron list` answers about whichever
     install is on PATH, so "does the listing have jobs in it" is a question about the
     machine, not about artec. The prover refused it, and that listing is now a test.

     Also fixed there: `text=True` decodes with the platform codec, and `hermes` prints
     em-dashes — on cp1252 the reader thread died, stdout came back empty, and the proof
     reported "cron jobs missing", a FALSE FAILURE naming the exact defect the entrypoint
     hard-fails on. It would have passed on the UTF-8 brain and lied anywhere else. A prover
     whose verdict depends on the locale it runs in is not a prover.

121. **2026-08-08 · A PROOF THAT ONLY HAPPENS WHEN SOMEBODY REMEMBERS REPORTS THE STATE OF
     THEIR ATTENTION.** `POST /commands/prove-all` existed and worked for days while the
     matrix stayed stale, because running it needed an authenticated call a human had to
     make. The capability was built; the *habit* was the missing part, and a system that
     depends on a habit has an unmonitored dependency on a person.

     The sweep now runs itself, on artec api, at boot, in a daemon thread that is started
     and never joined.

     **THIS IS NOT A REVERSAL OF 118.** I removed exactly this from the scheduler, and that
     reasoning stands unchanged: the scheduler's boot path gates nine jobs, so anything
     added to it can cost all nine. What differs is the blast radius, not the principle.
     This service owns HTTP serving; uvicorn is already accepting requests before the thread
     starts, nothing queues behind it, and a hung sweep leaves a hung thread rather than a
     missed slot. "Nothing that proves the scheduler may run inside the scheduler" was always
     a statement about the scheduler.

     **THE GATES ARE THE DESIGN**, and each has both sides named:
     * *Staleness* — `now` is a PARAMETER, never read inside the function (DECISIONS 112: a
       hidden clock is an unnamed side). The other side is the `at` stamp the last sweep
       wrote. An absent or unparseable stamp reads as DUE, never as fresh: treating a corrupt
       value as "now" is how a sweep stops running and nothing says so, which is the memory
       audit's `clean — 0 files scanned` in a new costume.
     * *`restore` on a separate, slower clock* — it is the ONLY proof that mutates the
       server (CREATE DATABASE / pg_restore / DROP DATABASE) and it rides job 8 monthly.
       Excluded unless genuinely due, and its freshness is read from ITS OWN entry: reading
       the whole-matrix stamp would keep it permanently fresh and it would never run again.
     * *Advisory lock* — a second replica no-ops, the same mechanism the scheduler uses
       against double-firing. Two concurrent sweeps would make `prove_stripe_attribution`
       report a false FAILURE, since it refuses when a probe order already exists.

     **A SKIP IS NOT A VERDICT.** `include_restore=False` records NOTHING: `run()` re-raises
     `NotProvable` before `record`, so the last real restore proof survives. Had a skip
     written through, an unattended sweep would have erased the monthly evidence twice a day
     and left a permanent "never".

     Also fixed in passing: `blocked_s1` indexed `results[c]` and would `KeyError` on a
     capability absent from the pass — the S1 summary line crashing the whole matrix at the
     exact moment it was reporting that something had not run. `.get` now reads absence as
     unproven, which is what absence means.

122. **2026-08-08 - FOUR OF THE NINE PROVERS COULD REPORT PROVEN WITHOUT THE CAPABILITY
     WORKING** - found by turning the standing review question on the proof harness itself.
     It matters more since 121: the sweep now runs unattended every twelve hours, and
     `proof_status` drops a "proven" capability off the digest's unproven list and out of
     doctor's YELLOW. **A false PROVEN is not a stale fact; it removes the surface that
     would have reported the truth.**

     * **`publish-by-slot`** - `evaluated = would_publish + held` defended only the
       registered false pass ("zero posts to select"), so an all-held board made `evaluated`
       large, `would_publish` EMPTY, and it returned ok=True with the detail "0 would
       publish". All-held is the ORDINARY end-of-week state: `select_due_posts` filters on
       `external_post_id IS NULL`, so photo posts leave the board as they publish and what
       remains is exactly the email and video posts held pending an approval receipt. The
       green row would have landed on a normal Friday. Now NOT_PROVABLE - the gates working
       is not a defect, but it is not proof of publishing either.
     * **`stripe-attribution`** - the prover built the Checkout event ITSELF, including the
       `client_reference_id`, then asserted the webhook copied it. The one untested link in
       the I19 chain - does artec.my's Payment Link populate that field from
       `utm_campaign=post_XXXX` at all? - was precisely the link being fabricated.
       `run_all`'s own docstring already said it "needs a real card purchase that no code
       can manufacture ... reports what is missing instead", and it reported PROVEN while
       gap B6 stayed open. The join is still verified; end-to-end needs one real order.
     * **`video-pipeline`** - pre-flighted with `aspect_ratio="16:9"` and
       `duration_bounds=(1.0, 10.0)`, neither of which occurs in production. The live spec
       comes from `channel_media`: tiktok 9:16/12s (6-24s) and youtube 9:16/15s (7.5-30s).
       The check ran in a configuration THAT CANNOT OCCUR, and the bytes making it green are
       bytes `run_preflight` would park twice over - the fixture is 1920x1080 at 3.0s, while
       a real 12s vertical render would fail the prover's own 1-10s bound. Half of S1, green
       on a check whose two configured sides had been replaced by constants.
     * **`brevo-send`** - `ok` was the literal `True` and the measured `deleted` reached only
       the detail string. A MEASURED OUTCOME THAT NEVER ENTERS A COMPARISON IS NOT A CHECK.
       The campaign is created on the production account against the live consumer list, so
       a refused DELETE left a send-ready campaign aimed at every subscriber and still
       recorded PROVEN - accumulating at up to one per api boot, while `run_all` claimed
       NOTHING IRREVERSIBLE HAPPENS HERE.

     Also closed: **`sweep_orphaned_slots` matched the literal `"RENDERED"`** while
     `select_due_posts` two functions above uses `PUBLISHABLE_STATUSES`, which also holds
     APPROVED_TO_SEND. A post approved onto a slot matching no `slot_times` key was invisible
     to the only A7 guard - never selected, never reported, shown nightly as queued for
     delivery. Approval is exactly when a post is most likely to be orphaned, because it is
     when the operator is most likely to have just edited `slot_times`.

123. **2026-08-08 - RETIRED WAS A COMMENT, AND THE JOB WENT ON FIRING.** From
     artec-scheduler's own log, 2026-08-07, two consecutive lines:

         22:30:18  measure reminder: telegram send failed (TelegramError)
         22:30:18  measure 2026-08-07: 5 unmeasured post(s), reminder sent

     The send failed inside a try/except and the next statement announced success
     unconditionally. It could never have succeeded: D1 removed TELEGRAM_BOT_TOKEN from
     artec api AND artec-scheduler so the brain is structurally the sole Telegram owner -
     verified against both services' variable lists. A daily call to a service holding no
     credentials for it, reporting delivery of a message nobody received.

     `measure-reminder` had sat in `jobs.RETIRED` the whole time, and the module docstring
     still advertised it as one of "EXACTLY TWO jobs" while the registry had grown to twelve.
     Every document that might have contradicted the behaviour agreed with it instead.

     The body is DELETED rather than unscheduled, and the route with it - the route had its
     expiry written down ("kept invocable only until D1 removes TELEGRAM_BOT_TOKEN") and that
     condition was met. `measure_reminder_time` is out of OPERATOR_CONSTANTS and out of the
     scheduler REQUIRED keys, where it could have made a service refuse to boot over a
     setting that set nothing.

     THE GUARD: `tests/unit/test_retired_jobs_do_not_fire.py` drives all 1440 minutes of a
     day through `tick` and compares the firings to the registry. It asserts on the loop's
     OWN `fired` set rather than on spied functions - the first draft spied on
     `run_registry_job` and `run_publish_job` and WOULD NOT HAVE CAUGHT THIS, because the
     reminder fired through a third function nobody had thought to spy on. A guard that only
     sees the firings you remembered to enumerate cannot find the one you forgot. Verified by
     re-injecting the defect: both assertions fail.
