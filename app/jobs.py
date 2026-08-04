"""THE JOB REGISTRY — one place that says what runs, when, who owns it, and how a human
invokes it by hand.

Why a registry rather than a list in a doc: three separate things have to agree, and until
now they agreed only by attention.

  * the twelve scheduled jobs (registered with cron in 2c-iv, verified BY LISTING)
  * the HTTPS mirrors — every artec-owned job body must be invocable over authenticated
    HTTPS, because "it only runs on a clock" is how a body goes unexercised until the night
    it matters
  * the CLI

A test asserts the registry and the live route table agree in BOTH directions: a job whose
mirror does not exist, and a `/commands` route that belongs to no job, are both findings.

RECONSTRUCTED, NOT RECOVERED — read this before trusting the numbering. The canonical
twelve-job table from the v4 prompt was not available when this file was written; the rows
below are reconstructed from the bodies that exist and the times already in config. The
NUMBERS in particular are the least trustworthy part. Confirm the mapping at 2c-iv, where
registration makes the numbers load-bearing; nothing before then depends on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARTEC = "artec"          # runs on artec-scheduler / artec api — has an HTTPS mirror
BRAIN = "brain"          # a hermes-agent cron job on artec-brain — no HTTPS mirror


@dataclass(frozen=True)
class Job:
    number: int
    name: str
    schedule: str                    # human-readable, Asia/Singapore
    owner: str
    body: str                        # dotted path to the function a human would call
    mirror: str = ""                 # authenticated HTTPS path, "" for brain-owned jobs
    cli: str = ""
    notes: str = ""
    registered: bool = False         # cron registration lands in 2c-iv, verified by listing
    tags: tuple[str, ...] = field(default_factory=tuple)


JOBS: tuple[Job, ...] = (
    Job(1, "report", "Sunday 06:00", ARTEC,
        "app.stages.report.build_report", "/commands/report", "artec report",
        "OPERATOR-CONFIRMED (2c-iii): job 1 is the weekly report snapshot at SUN 06:00, "
        "which CONTRADICTS the first reconstruction (report was 7, publish-by-slot was 1). "
        "Price reconciliation is this job's FIRST action, before the snapshot, so the "
        "report, the gate and every later render see reconciled rates. One anchor is not a "
        "table: everything else here is still reconstructed."),
    Job(2, "assets-sync", "daily 05:00", ARTEC,
        "app.stages.assets_sync.sync", "/commands/assets-sync", "artec assets sync"),
    Job(3, "render", "daily 06:00", ARTEC,
        "app.stages.render.render", "/commands/render", "artec render"),
    Job(4, "measure-reminder", "daily 06:30", ARTEC,
        "app.scheduler.run_measure_job", "/commands/measure-reminder", "artec measure",
        "RETIRED AT MERGE (D1): the brain becomes the sole Telegram owner and the digest "
        "carries the unmeasured list"),
    Job(5, "learn-ideate", "Sunday 07:00", ARTEC,
        "app.stages.ideate.ideate", "/commands/ideate", "artec learn && artec ideate",
        "THE BESPOKE HALF. `plan_source` is `shadow`, which means BOTH planners run every "
        "Sunday: bespoke learn→ideate writes the DRAFT rows, and the agent's own "
        "learn-ideate cron (deploy/hermes-brain/cron-learn-ideate.txt) writes to "
        "plans_shadow for the plan-diff. One logical job, two halves — and the bespoke "
        "half is the reason ten mirrors exist rather than nine"),
    Job(6, "weekly-gate", "Sunday 09:00", BRAIN,
        "deploy/hermes-brain/cron-weekly-gate.txt", "", "",
        "Touch 1 of the 7-touch contract — the one thing that never degrades"),
    Job(7, "publish-by-slot", "daily at each config slot_times entry", ARTEC,
        "app.scheduler.run_publish_job", "/commands/publish-slot", "artec publish",
        "APPROVED_TO_SEND enters here and nowhere earlier — an approval waits for the next "
        "occurrence of its slot. NUMBER RECONSTRUCTED: it moved off 1 when the operator "
        "confirmed job 1 is the report."),
    Job(8, "pg-dump", "daily 03:00", ARTEC,
        "app.stages.backup.run_backup", "/commands/backup", "artec backup",
        "restore-check rides this job on day_of_month == 1 — still twelve jobs"),
    Job(9, "review-expiry-sweep", "daily 20:00", ARTEC,
        "app.scheduler.sweep_expired_reviews", "/commands/sweep-reviews",
        "artec sweep-reviews",
        "no auto-approve and no expire-to-send exists to be requested"),
    Job(10, "doctor-sweep", "weekly Sunday 06:00", ARTEC,
        "app.stages.doctor.run_doctor", "/commands/doctor", "artec doctor",
        "the memory audit rides this job weekly"),
    Job(11, "digest-prepare", "daily 21:00", ARTEC,
        "app.stages.digest.prepare_digest", "/commands/digest-prepare",
        "artec digest-prepare"),
    Job(12, "digest-delivery", "21:05 Monday–Saturday", BRAIN,
        "deploy/hermes-brain/cron-nightly-digest.txt", "", "",
        "does NOT run on Sunday — asserted in the body, not only in the cron expression"),
)


def artec_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == ARTEC)


def mirror_paths() -> set[str]:
    """Every HTTPS mirror the registry claims exists."""
    return {j.mirror for j in artec_jobs() if j.mirror}


# Authenticated /commands routes that are NOT job mirrors, each with its reason. Anything
# else appearing under /commands is a route nobody registered a job for — which is either a
# missing registry row or a surface nobody meant to expose.
NON_JOB_ROUTES: dict[str, str] = {
    "/commands/measure": "operator posts figures directly; the reminder is job 4",
    "/commands/learn": "runs immediately before job 5's mirror; kept separately invocable "
                       "because learn is worth re-reading without re-planning",
    "/commands/publish": "manual publish, including the CHECKPOINT 4 first publish",
    "/commands/plan-diff": "shadow-mode artefact, read by the operator between Sundays",
    "/commands/wishlist-match": "runs inside job 2; exposed for the monthly wishlist review",
    "/commands/media/{post_id}": "not a job — deliver_video reads the publish bytes here",
    "/commands/gate": "409 by design: the gate is an interactive Telegram session",
    "/commands/prove": "not a job — operator-driven capability proofs (§9). CLI/HTTPS "
                       "only, never an agent tool, which is why it may write config.proofs",
    "/commands/restore-check": "rides job 8 monthly; exposed so a restore can be proven "
                               "on demand, which is the only time anyone wants it",
}
