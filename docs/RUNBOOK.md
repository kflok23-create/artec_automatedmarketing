# HERMES RUNBOOK — the exact manual cycle

Every command below is run by a human. Nothing fires on a clock. All commands are
idempotent — re-running is always safe.

## 0. One-time setup (after deploy, secrets set, webhooks registered)

```bash
hermes doctor
```
Every line must be GREEN (YELLOW lines are warnings with instructions). Every RED line
names its remedy — fix and re-run. Do not proceed on red.

```bash
hermes config seed
hermes config set seo_seeds '["stem toys singapore", "artec blocks", "educational toys malaysia", "logic puzzle kids", "screen free play"]'
hermes assets sync --full
```
`assets sync` prints an inventory table — confirm the counts match what you see in Drive.

## 1. The weekly cycle (typically Sunday morning, but any time you choose)

```bash
hermes learn                      # scores LAST week; cold start prints "insufficient data"
hermes ideate                     # drafts THIS week's plan per channel cadence
hermes gate                       # Telegram session:
                                  #   ✅ Approve  ✏️ Edit ('field: value' lines)  ❌ Reject
                                  #   '+ channel: tiktok | hook: ...' injects your own idea
                                  #   /done ends the session
hermes gate --wishlist            # first session of the month: also reviews parked posts
hermes render --all-approved      # bank-first toolbox; failures PARK with a wishlist
hermes publish --all-rendered     # or --post-id post_1482 to go one at a time
```

The **first ever** publish prints exactly what is about to go live (post, caption, tracked
URL, Brevo recipient count) and waits for you to type `continue`. This fires once per
install, never again.

## 2. Measuring (daily or whenever you have figures)

No channel APIs, no CSVs — you hand figures to the service directly:

```bash
hermes measure                    # interactive: prompts per unmeasured post, blank = unmeasured
```

or non-interactively:

```bash
hermes measure --json '{"rows": [{"post_id": "post_1482", "channel": "tiktok", "metric_date": "2026-08-03", "impressions": 12000, "saves": 45, "clicks": 120}]}'
```

or straight to the service:

```bash
curl -X POST "$PUBLIC_BASE_URL/commands/measure" -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" -d '{"rows": [{"post_id": "post_1482", "channel": "tiktok", "metric_date": "2026-08-03", "impressions": 12000}]}'
```

Skipped fields stay NULL (unmeasured) — never enter 0 for "I don't know".

```bash
hermes report                     # REVENUE block, ENGAGEMENT block, unattributed,
                                  # unmeasured, parked — lanes never combined
```

## 3. The monthly asset loop

```bash
hermes wishlist show              # what to shoot, grouped by target Drive folder
# … you shoot and file into exactly those folders over the following days …
hermes assets sync                # incremental: indexes the new files from their paths
hermes wishlist match             # parked posts that can now be serviced → APPROVED
hermes render --all-approved
hermes wishlist fulfil --post-id post_1490 --drive-file-id <id>   # manual override
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `boot failed: missing … variable(s): X` | Set X in Railway (name printed, value never) |
| Drive listing comes back empty, no error | Shared-drive kwargs — should not happen (client always passes them); check the service account is a Content Manager on the **Shared Drive**, not just the folder |
| Brevo `402` on publish | Insufficient Brevo credits. Post stays RENDERED; top up and re-run publish |
| `template is missing in-body variable(s)` | Template was edited in Brevo — restore the six `{{variables}}` |
| Renders park with "no tool chain can hit the match" | The bank lacks the needed subject/medium — `hermes wishlist show` tells you what to shoot |
| `LoRA … not Qwen-Image-2512 compatible` in doctor | Retrain that LoRA on `fal-ai/qwen-image-2512-trainer` |
| MY orders all UNATTRIBUTED | checkout.php's `order_created` POST to `/event` is failing or missing — the paid callback found no pending row for that bill_id (see DECISIONS.md #3) |
| SG orders all UNATTRIBUTED | artec.my is not appending `?client_reference_id=post_XXXX` to the Stripe payment link |
| Double-publish error | Correct behaviour — that post already went live; it will never publish twice |
