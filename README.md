# HERMES — one agent, one store, one key

Automated marketing loop for [artec.my](https://artec.my): one Railway service, one
Anthropic model, one Postgres database, one join key (`post_id`). It learns from last
week's results, drafts a 7-day plan, takes one human gate over Telegram, shops a
human-curated Google Drive asset bank before reaching for any generator, publishes to five
social surfaces + email, and measures what it published.

```
LEARN → IDEATE → GATE (Telegram) → RENDER (visual toolbox) → PUBLISH → MEASURE → REPORT
   ↑                                                                              │
   └────────────── last week's results write next week's 7-day plan ──────────────┘
```

**There is no scheduler.** Every stage is a manually invoked `hermes` command (mirrored by
an authenticated POST route). Cadence numbers are planning inputs, never timers.

## The manual cycle

```bash
hermes doctor              # green/red table; every line must be green
hermes config seed         # §0 operator constants → config table
hermes assets sync --full  # index the Drive bank into Postgres
hermes learn               # score last week (cold start → "insufficient data")
hermes ideate              # 7-day DRAFT plan sized by channel cadence
hermes gate                # Telegram: approve / edit / reject / inject
hermes render --all-approved
hermes publish --all-rendered   # first ever publish halts for confirmation
hermes measure             # hand the figures to the service directly
hermes report              # REVENUE and ENGAGEMENT — separate blocks, never blended
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for the copy-pasteable operator guide,
[docs/RAILWAY_SETUP.md](docs/RAILWAY_SETUP.md) for deployment, and
[docs/ASSET_BANK.md](docs/ASSET_BANK.md) for the Drive taxonomy rules.
[SPEC.md](SPEC.md) is the file-level spec; [DECISIONS.md](DECISIONS.md) records every
judgment call made during the build.

## Hard rules (violating any of these is a bug)

1. NO SCHEDULER — nothing fires on a clock, anywhere.
2. ONE SERVICE — no worker, no queue; long work runs inline with progress on stdout.
3. ONE MODEL — a single Anthropic model for learn, ideate, copy, and toolbox routing.
4. ONE STORE — Postgres. Drive holds media bytes only and is mirrored into `assets`.
5. ONE KEY — `post_id` joins posts, orders, events, metrics.
6. LANE RULE — revenue only from `orders`; engagement only from `events` + `metrics`.
7. STALE ≠ ZERO — unmeasured is NULL and reported as "unmeasured".
8. IDEMPOTENT — every command is safe to re-run.
9. DRIVE TAXONOMY IS READ-ONLY — HERMES writes only inside `_generated/`.

## Development

```bash
uv sync --group dev
uv run pytest              # acceptance tests (offline, SQLite + fakes)
uv run ruff check .
uv run hermes cycle --dry-run   # full mocked cycle, same as CI
```

Secrets live in the Railway dashboard (or a local `.env` you write by hand) — never in the
repo, never in chat. `.env.example` lists every variable with blank values.
