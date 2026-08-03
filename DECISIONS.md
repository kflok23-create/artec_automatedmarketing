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
