# HERMES v3 MIGRATION — Sunday brain, daily body

Additive and reversible. Nothing that currently works is deleted: bespoke LEARN, IDEATE and
GATE stay on disk and stay tested — dormant, not removed. Rollback is one config row
(`plan_source = "bespoke"`), never a code change or a redeploy.

## The sentence that governs every ambiguity

**The agent does interpretation and generation; SQL does arithmetic; Python does pixels.**

## What changes

### A. Toolbox lockdown (§4 of the v3 spec)

Driven by three production failures in one cycle: a diffusion model rendered
"Robotics class 1 termı $15" and "One box of blucks $9%"; a text-to-video call cost USD 8
and returned blocks that are not our product; a generated video would not play.

| Rule | Enforcement |
|---|---|
| 0 — no model renders text, ever | `app/toolbox/text_guard.py` raises on any prompt with a quoted string, price pattern, or text/caption/title/label/write/says — applied inside `Fal.run`, so no call site can forget it |
| 1 — no text-to-video | Seedance + `video_family` deleted from code and config (seed removes the stale config row) |
| 2 — no generated video at all | video = ffmpeg pipeline over `raw-video/` footage: trim · crop · speed · concat · drawtext (textfile=) · thumbnail · `-movflags +faststart` on every output |
| 3 — bank-only for the product | selector has no generate tool; product idea with no bank match → PARK + wishlist |
| 4 — USD 1.00 per render run | `app/toolbox/budget.py`: config price table (cents), per-call ceiling + per-run cap, spend printed after every call, unknown endpoint = uncallable |
| 5 — one LoRA per call | asserted at the (dormant) call site in addition to the trigger-collision guard |
| 6 — Python first | Pillow overlays (`app/toolbox/overlay.py`) + the ffmpeg pipeline are the primary path; zero model calls for the common cases |

Survives: image enhancement of real bank photographs only, via an explicit whitelist
(`upscale` = fal clarity; `color_correct`/`autocontrast` = Pillow, zero model calls).
GENERATE via the artec LoRAs: **disabled** (`generate_enabled=false`), config rows retained
so the operator can overturn the decision without an archaeology dig.

### B. The agent seam (§6–§10)

NousResearch hermes-agent (pinned tag `v2026.7.30`, never `main`) takes LEARN→IDEATE and the
WEEKLY GATE through exactly six plugin tools in `plugins/artec_hermes.py` — four reads, two
narrow writes (`write_plan`, `record_gate_decision`). There is no tool that writes orders,
events, metrics or config: "the model never edits money rows" is enforced by the absence of
a capability.

### Topology (four services, one Postgres, one volume)

| Service | Command | Change |
|---|---|---|
| artec-api | `uvicorn app.main:app …` | unchanged |
| artec-scheduler | `python -m app.scheduler` | NEW — daily publish-by-slot + daily 06:30 measure, nothing else |
| hermes-brain | `hermes gateway` | NEW — `deploy/hermes-brain/`, volume at `/data/hermes` (`HERMES_HOME`) |
| Postgres | plugin | unchanged |

The four scheduled jobs (Asia/Singapore) — the narrow, deliberate lift of v2's no-scheduler
rule: SUN 07:00 agent LEARN→IDEATE · SUN 09:00 agent gate · DAILY publish by slot · DAILY
06:30 measure. A repo test asserts exactly four exist across both codebases.

### Shadow mode (§11)

`config.plan_source`: `shadow` (default) | `agent` | `bespoke`. In shadow, both sides plan —
agent output lands only in `plans_shadow`; the gate presents the bespoke plan; nothing the
agent produces goes live. `artec plan-diff --week` is the artefact the operator reads for
two to three Sundays before flipping. Flipping back to `bespoke` disables the agent path
with no redeploy.

## Cutover sequence (operator)

1. Deploy this commit (artec-api redeploys; behaviour additive).
2. Railway: new service **artec-scheduler** from this repo, start command
   `python -m app.scheduler`, same env vars as artec-api.
3. Railway: new service **hermes-brain** from `deploy/hermes-brain/Dockerfile`, volume
   mounted at `/data/hermes`, env per `deploy/hermes-brain/README.md`.
4. Run `artec doctor` and `hermes doctor`; both must be green (incl. the volume
   survives-redeploy marker).
5. Watch `artec plan-diff` for 2–3 Sundays. Flip `plan_source` only then.

## Rollback

`artec config set plan_source '"bespoke"'` — agent cron output is ignored, bespoke IDEATE
writes DRAFT rows as in v2. No redeploy. The toolbox lockdown is independent and stays.
