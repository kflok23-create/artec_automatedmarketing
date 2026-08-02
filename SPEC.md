# HERMES — SPEC

One agent, one store, one key. A single Railway service (FastAPI + Typer CLI, one process)
that runs a manually-invoked marketing loop for artec.my:

```
doctor → config seed → assets sync → learn → ideate → gate → render → publish → measure → report
```

Every stage is a CLI command mirrored by an authenticated POST route; both call the same
function. Nothing fires on a clock. All state lives in Railway Postgres. Media bytes live in
a Google Drive Shared Drive ("Artec Assets Bank"); Postgres mirrors its index. `post_id` is
the single join key across posts, orders, events, and metrics.

## Governing constraints (§2 of the build prompt)

1. NO SCHEDULER — no cron/APScheduler/celery/rq/schedule imports anywhere (CI-enforced).
2. ONE SERVICE — one process type; long work runs inline in the invoked command.
3. ONE MODEL — a single Anthropic model for learn, ideate, copy, toolbox routing.
4. ONE STORE — Postgres for state; Drive for media bytes only, mirrored into `assets`.
5. ONE KEY — post_id joins everything.
6. LANE RULE — revenue only from `orders`; engagement only from `events`+`metrics`; never blended.
7. STALE ≠ ZERO — unmeasured metrics are NULL, reported as "unmeasured".
8. IDEMPOTENT — every command safe to re-run; writes are upserts on natural keys.
9. DRIVE TAXONOMY READ-ONLY — HERMES writes only inside `_generated/`.

## File manifest

| Path | Purpose |
|---|---|
| `pyproject.toml` | uv project; deps; `hermes` console script; ruff + pytest config |
| `alembic.ini` | Alembic config (URL read from env at runtime) |
| `railway.json` | NIXPACKS build; uvicorn start; pre-deploy `alembic upgrade head`; /healthz |
| `nixpacks.toml` | installs ffmpeg |
| `.env.example` | every §11 env var, values blank |
| `.gitignore` | .env, *.pem, *service-account*.json, .env.* (except .env.example) |
| `.github/workflows/ci.yml` | ruff + pytest + alembic upgrade against throwaway Postgres + secret scan; never deploys |
| `app/settings.py` | pydantic Settings; fail-fast boot validation (names only); secret redaction filter |
| `app/config.py` | operator constants; config-table seed/get/set; post-id counter; net-CM + kill-line economics |
| `app/db.py` | engine/session factories; `record_run` (runs table); URL normalization |
| `app/models.py` | SQLAlchemy models: posts, assets, orders, events, metrics, learnings, config, runs; `V_BRIEF_SQL` |
| `app/schemas.py` | pydantic payloads: /event beacon, measure rows, toolbox plan, wishlist entry |
| `app/taxonomy.py` | pure path→tags derivation (§4.1); valid wishlist folders |
| `app/spine.py` | pure `build_tracked_url` (§8) |
| `app/integrations/anthropic_client.py` | one-model LLM wrapper; prompt-file loading; JSON extraction |
| `app/integrations/drive_client.py` | service-account Drive client; shared-drive kwargs; walk/download/upload-to-_generated/changes; write probe |
| `app/integrations/fal_client.py` | queue submit + bounded inline poll; public file upload |
| `app/integrations/upload_post_client.py` | /api/upload (video) + /api/upload_photos (photos); pre-render platform validation |
| `app/integrations/brevo_client.py` | template fetch, six-variable substitution contract, campaign create + sendNow, 402 handling |
| `app/integrations/telegram_client.py` | sendMessage + inline keyboards; long-poll getUpdates |
| `app/integrations/stripe_webhook.py` | manual signature verify; checkout.session.completed → orders (client_reference_id only) |
| `app/integrations/billplz_webhook.py` | X-Signature verify; bill fetch for reference_1; → orders |
| `app/integrations/fakes.py` | in-process fakes for `artec cycle --dry-run` and tests |
| `app/toolbox/asset_match.py` | bank-first candidate query; LRU preference; times_used accounting |
| `app/toolbox/selector.py` | model-driven tool routing; pure `validate_plan` + deterministic fallback |
| `app/toolbox/edit_combine.py` | kontext 0/1/2+ routing; video family routing; ffmpeg trim/frame; Pillow aspect fit |
| `app/toolbox/generate.py` | qwen-2512 LoRA generation; word-boundary trigger checks; one-LoRA rule; NSFW retry/park |
| `app/toolbox/enhance.py` | clarity-upscaler ×1.5, low creativity; image-only guard |
| `app/toolbox/text_card.py` | Pillow text card; three locked colour pairings; rotation |
| `app/toolbox/park.py` | PARK transition; structured wishlist validation against taxonomy |
| `app/stages/assets_sync.py` | full walk + changes-API incremental sync; missing marking; summary |
| `app/stages/learn.py` | lever scoring under lane rule; net-CM; CAC + kill lines; cold-start safe |
| `app/stages/ideate.py` | 7-day DRAFT plan sized by cadence; monotonic post ids; spine persisted |
| `app/stages/gate.py` | Telegram inline-button gate; edit/reject/inject; no regeneration |
| `app/stages/render.py` | toolbox execution; caption; `_generated/` upload; park on failure |
| `app/stages/publish.py` | Upload-Post + Brevo publish; double-publish guard; first-publish gate |
| `app/stages/measure.py` | interactive/JSON metric entry; NULL semantics |
| `app/stages/report.py` | REVENUE and ENGAGEMENT blocks, never combined; unattributed + unmeasured + parked |
| `app/stages/wishlist.py` | show / match / fulfil |
| `app/stages/doctor.py` | green/red environment verification incl. live LoRA probe + Drive write probe |
| `app/stages/cycle.py` | `--dry-run` full cycle against fakes (CI) |
| `app/api/deps.py` | bearer-token auth dependency |
| `app/api/routes_capture.py` | /webhooks/stripe, /webhooks/billplz, /event (CORS + rate limit) |
| `app/api/routes_commands.py` | authenticated POST mirrors of the CLI stages |
| `app/main.py` | FastAPI app; boot validation; /healthz |
| `app/cli.py` | Typer CLI `hermes` |
| `app/prompts/learn_v1.md` | qualitative learn pass (in-package: ships in the wheel) |
| `app/prompts/ideate_v1.md` | 7-day plan generation |
| `app/prompts/toolbox_route_v1.md` | tool/asset routing |
| `app/prompts/caption_v1.md` | captions + email variables |
| `app/prompts/wishlist_v1.md` | structured wishlist authoring |
| `app/migrations/…` | Alembic env + `0001_initial` (in-package so runtime head checks work when installed) |
| `app/assets/fonts/README.md` | the three static .ttf families live inside the package so the wheel ships them (doctor checks) |
| `tests/…` | acceptance tests §15 (unit + integration, offline via fakes) |
| `docs/RUNBOOK.md` | the exact manual cycle, copy-pasteable |
| `docs/RAILWAY_SETUP.md` | dashboard steps, reference variables, pre-deploy migration |
| `docs/ASSET_BANK.md` | folder tree, path→tag table, Description convention, never-rename rule |

## Data model

See `app/models.py`. `posts` is the board, log, and ledger of record. `v_brief` is a SQL
view (≤40 rows, LIMIT-enforced and test-asserted) rendered to plain text before every LEARN
and IDEATE run. Money is integer minor units everywhere; the loop scores NET contribution
margin only (SGD 7400 / MYR 21200 by default config).

## Checkpoints

The build halts at four human gates (§16): env-var table before deploy; webhook URLs after
first deploy; `artec doctor` all-green; first-publish confirmation (once per install,
persisted in `config`).
