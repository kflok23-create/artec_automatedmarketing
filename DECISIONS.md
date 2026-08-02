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

22. **`artec measure --csv` from the smoke-run example is not implemented** — the locked
    decision table says "No channel APIs, no CSV files"; measure is interactive, `--json`,
    or `POST /commands/measure`. The RUNBOOK shows the correct invocation.
