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

3. **Billplz attribution (verified against Billplz API docs).** Bill callbacks do NOT carry
   `reference_1`; it exists only on the Bill object. The webhook therefore verifies
   X-Signature (HMAC-SHA256 over pipe-joined, case-insensitively sorted `key+value` pairs,
   excluding `x_signature`), then fetches `GET /api/v3/bills/{id}` with BILLPLZ_API_KEY and
   reads `reference_1` as the post_id. Missing/non-`post_` values → order stored with
   post_id NULL (UNATTRIBUTED). **Operator action:** the Billplz catalog link must set
   `reference_1` to the post_id (artec.my appends it, mirroring the Stripe
   `client_reference_id` flow); confirm your catalog link supports it — if it does not, MY
   revenue reports as UNATTRIBUTED, never guessed.

4. **Seedance endpoint slugs are config, not code — and unverified.** fal.ai rate-limited
   the model page during the build, so the operator-supplied slugs
   (`bytedance/seedance-2.0/text-to-video`, `bytedance/seedance-2.0/reference-to-video`)
   are seeded into `config.video_family` together with `reference_syntax: "bracket"`
   (`[Image1] [Video1] [Audio1]`), max 3 ref videos, 4–15 s, 480p/720p. `hermes doctor`
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
   families go into `assets/fonts/` by hand; `hermes doctor` is RED and TEXT CARD raises a
   named error until they exist. No font bytes are fetched or committed by the build.

9. **`app/stages/doctor.py` and `app/stages/cycle.py`** are added beyond the §12 layout —
   `doctor` and `cycle --dry-run` are mandated commands and deserve their own stage modules.
   Likewise `app/integrations/fakes.py` holds the dry-run/test fakes.

10. **SEO_SEEDS ships empty.** The prompt left it `<<STILL BLANK>>`. It is seeded as `[]`;
    `hermes doctor` warns and `hermes ideate` refuses to run with a named operator error
    until 5–15 seeds are set (`hermes config seed --file config.yaml` or
    `hermes config set seo_seeds '["…"]'`).

11. **Stripe signature verification is hand-rolled** (HMAC-SHA256 over `t.payload`,
    constant-time compare, 5-minute tolerance) because the stripe SDK is not in the locked
    stack. The webhook reads only `client_reference_id`, `amount_total`, `currency`,
    `customer_details.email` (hashed), `created`, `id`. The module contains no reference to
    UTM anywhere (test-asserted).

12. **post_id allocation** is a `config` row (`post_id_counter`, starts at
    POST_ID_START=1482) incremented inside the insert transaction with a row lock on
    Postgres; monotonic, gap-tolerant.

13. **Idempotent ideate**: re-running `hermes ideate` for the same week only tops up each
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
    `hermes cycle --dry-run` and the test suite; production code paths always construct real
    clients.

19. **Aspect derivation:** width/height ratio < 0.9 → vertical, ≤ 1.1 → square, else
    landscape; unknown dimensions → NULL aspect (matches any aspect filter).

20. **Person-asset policy follows the letter of the spec:** when `allow_person_assets` is
    false only `has_person = true` rows are excluded; UGC's unknown (`NULL`) stays eligible.
    Flip the config key when releases are settled.

21. **`hermes measure --csv` from the smoke-run example is not implemented** — the locked
    decision table says "No channel APIs, no CSV files"; measure is interactive, `--json`,
    or `POST /commands/measure`. The RUNBOOK shows the correct invocation.
