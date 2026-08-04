"""THE JOB REGISTRY — reconciled against §3.

Three things have to agree, and until this file existed they agreed only by attention: the
twelve scheduled jobs, the HTTPS mirrors, and the CLI. A test asserts the registry against
the live route table in BOTH directions, and another asserts the set is EXACTLY twelve —
§2: *"the twelve in §3, and only those. Any job not on that list is a defect."*

THE 20:30 → 20:40 → 20:55 CHAIN IS A DESIGNED SEQUENCE, NOT FOUR ARBITRARY TIMES.
Assets sync at 20:30 so tonight's wishlist reflects last night's drop; the doctor sweep at
20:40 so its RED lines exist to be carried; digest preparation at 20:55 so the payload is
written before the brain reads it at 21:05. Preparing at 21:00 — which an earlier
reconstruction did — races delivery: the brain can call `read_digest` before job 11 has
written the row, and the failure mode is an EMPTY DIGEST on a night something needed the
operator.

TWO NUMBERS ARE STILL UNRECOVERED. §3's entries for jobs 2 and 7 were never quoted to this
build. `publish-by-slot` must exist and is placed at 2; slot 7 is a declared UNKNOWN rather
than a guess, because inventing a twelfth job to make the count work is how a schedule comes
to contain something nobody designed. Fill it from §3 before registering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARTEC = "artec"          # artec-scheduler / artec api — has an HTTPS mirror
BRAIN = "brain"          # hermes-agent cron on artec-brain — no HTTPS mirror
UNKNOWN = "unknown"      # a §3 slot this build has not been given


@dataclass(frozen=True)
class Job:
    number: int
    name: str
    schedule: str                    # human-readable, Asia/Singapore
    owner: str
    body: str = ""                   # dotted path to the function a human would call
    mirror: str = ""                 # authenticated HTTPS path; "" for brain-owned
    cli: str = ""
    at: str = ""                     # "HH:MM" | "DOW HH:MM" (0=Sunday) | "" = slot-driven
    also_at: tuple[str, ...] = ()    # additional firings (job 6's Monday retry)
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


JOBS: tuple[Job, ...] = (
    Job(1, "report", "Sunday 06:00", ARTEC, "app.stages.report.build_report",
        mirror="/commands/report", cli="artec report", at="0 06:00",
        notes="Weekly snapshot. PRICE RECONCILIATION IS THIS JOB'S FIRST ACTION, before the "
              "snapshot, so the report, the gate and every later render see reconciled "
              "rates rather than last week's."),
    Job(2, "publish-by-slot", "daily at each config slot_times entry", ARTEC,
        "app.scheduler.run_publish_job", mirror="/commands/publish-slot",
        cli="artec publish",
        notes="Slot-driven, so no fixed time. APPROVED_TO_SEND enters here and nowhere "
              "earlier, and waits for the next occurrence of its slot. NUMBER UNCONFIRMED: "
              "§3's entry for job 2 was never quoted to this build."),
    Job(3, "agent-learn-ideate", "Sunday 07:00", BRAIN,
        "deploy/hermes-brain/cron-learn-ideate.txt",
        notes="The AGENT half. Writes plans_shadow in shadow mode."),
    Job(4, "plan-diff", "Sunday 08:00", ARTEC, "app.stages.plan_diff.build_diff",
        mirror="/commands/plan-diff", cli="artec plan-diff", at="0 08:00",
        notes="MUST COMPLETE BEFORE THE GATE. In shadow mode this is the whole point: both "
              "planners produce a plan and the diff is the evidence for the I16 cutover "
              "decision. Without it the Sunday gate has nothing to compare and shadow mode "
              "proves nothing."),
    Job(5, "weekly-gate", "Sunday 09:00", BRAIN,
        "deploy/hermes-brain/cron-weekly-gate.txt",
        notes="Touch 1 of the 7-touch contract — the one thing that never degrades."),
    Job(6, "render", "Sunday 10:00, Monday 10:00 retry", ARTEC,
        "app.stages.render.render", mirror="/commands/render", cli="artec render",
        at="0 10:00", also_at=("1 10:00",),
        notes="TWICE A WEEK, NOT DAILY. There is no weekly fal cap: the weekly bound IS "
              "this job firing twice against the USD 2.50 per-run cap, so ~USD 5 worst "
              "case. A daily render would make it ~USD 17.50 and the digest's "
              "`fal · week to date` line would report against a bound nobody set — the "
              "flat-rate price table again."),
    Job(7, "UNKNOWN", "unknown", UNKNOWN,
        notes="§3's entry for job 7 was never quoted to this build. Declared rather than "
              "guessed: inventing a job to make the count reach twelve is how a schedule "
              "comes to contain something nobody designed. Fill from §3 before registering."),
    Job(8, "pg-dump", "daily 03:00", ARTEC, "app.stages.backup.run_backup",
        mirror="/commands/backup", cli="artec backup", at="03:00",
        notes="restore-check rides this job on day_of_month == 1 — still twelve jobs."),
    Job(9, "assets-sync", "daily 20:30", ARTEC, "app.stages.assets_sync.sync",
        mirror="/commands/assets-sync", cli="artec assets sync", at="20:30",
        notes="Assets sync AND wishlist match. First link of the nightly chain: tonight's "
              "wishlist must reflect last night's drop."),
    Job(10, "doctor-sweep", "daily 20:40", ARTEC, "app.stages.doctor.run_doctor",
        mirror="/commands/doctor", cli="artec doctor", at="20:40",
        notes="DAILY, not weekly — its RED lines must exist before the digest is prepared "
              "fifteen minutes later. The memory audit rides this job."),
    Job(11, "digest-prepare", "daily 20:55", ARTEC, "app.stages.digest.prepare_digest",
        mirror="/commands/digest-prepare", cli="artec digest-prepare", at="20:55",
        notes="THE REVIEW EXPIRY SWEEP RUNS INSIDE THIS JOB, not beside it: job 11 already "
              "runs daily and already reads exactly the posts involved, and a separate "
              "sweep at 20:00 could park a review the operator was about to answer at "
              "21:05. Five minutes before delivery, never the same minute."),
    Job(12, "digest-delivery", "21:05 Monday–Saturday", BRAIN,
        "deploy/hermes-brain/cron-nightly-digest.txt",
        notes="Does NOT run on Sunday — asserted in the body, not only in the cron."),
)

# RETIRED, deliberately, and named so neither can quietly return:
#   measure-reminder (daily 06:30) — the digest replaces it, and it was the only thing on a
#   bespoke service that sent to Telegram. D1 removes TELEGRAM_BOT_TOKEN from artec api and
#   artec-scheduler at merge so the brain is STRUCTURALLY the sole Telegram owner. Keeping
#   the job would mean either a job that crashes on a missing token, or a token that has to
#   stay and a policy that is no longer structural.
#   review-expiry-sweep (daily 20:00) — folded into job 11; see its note.
RETIRED = ("measure-reminder", "review-expiry-sweep")


def artec_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == ARTEC)


def brain_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == BRAIN)


def unknown_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == UNKNOWN)


def mirror_paths() -> set[str]:
    return {j.mirror for j in artec_jobs() if j.mirror}


def firings() -> list[tuple[Job, str]]:
    """(job, "HH:MM" | "DOW HH:MM") for everything artec fires on a clock. Job 2 is
    slot-driven and appears nowhere here — its times come from `slot_times`."""
    out = []
    for job in artec_jobs():
        for when in (job.at, *job.also_at):
            if when:
                out.append((job, when))
    return out


NON_JOB_ROUTES: dict[str, str] = {
    "/commands/measure": "operator posts figures directly — the reminder job is RETIRED",
    "/commands/learn": "runs immediately before job 4 needs two plans; separately invocable "
                       "because learn is worth re-reading without re-planning",
    "/commands/ideate": "the bespoke planner — half of what job 4 diffs",
    "/commands/publish": "manual publish, including the CHECKPOINT 4 first publish",
    "/commands/sweep-reviews": "runs INSIDE job 11; exposed so a sweep can be forced",
    "/commands/wishlist-match": "runs inside job 9; exposed for the monthly wishlist review",
    "/commands/media/{post_id}": "not a job — deliver_video reads the publish bytes here",
    "/commands/gate": "409 by design: the gate is an interactive Telegram session",
    "/commands/prove": "not a job — operator-driven capability proofs (§9). CLI/HTTPS only, "
                       "never an agent tool, which is why it may write config.proofs",
    "/commands/restore-check": "rides job 8 monthly; exposed so a restore can be proven on "
                               "demand, which is the only time anyone wants it",
    "/commands/measure-reminder": "the RETIRED job's body, kept invocable only until D1 "
                                  "removes TELEGRAM_BOT_TOKEN from this service at merge",
}
