"""THE JOB REGISTRY - §3, verbatim.

| # | job | when | runtime |
|---|---|---|---|
| 1 | weekly report snapshot | SUN 06:00 | scheduler |
| 2 | bespoke learn + ideate | SUN 06:30 | scheduler |
| 3 | agent LEARN -> IDEATE | SUN 07:00 | brain cron |
| 4 | plan-diff snapshot | SUN 08:00 | scheduler |
| 5 | WEEKLY GATE | SUN 09:00 | brain cron |
| 6 | render all approved | SUN 10:00, retry MON 10:00 | scheduler |
| 7 | publish by slot | daily, per `slot_times` | scheduler |
| 8 | nightly pg_dump -> Drive _backups/ | daily 03:00 | scheduler |
| 9 | assets sync + wishlist match | daily 20:30 | scheduler |
| 10 | doctor sweep | daily 20:40 | scheduler |
| 11 | digest preparation | daily 20:55 | scheduler |
| 12 | DIGEST DELIVERY | MON-SAT 21:00 | brain cron |

ORDERING IS LOAD-BEARING, not presentation:
  * job 2 before job 3, so plan-diff has BOTH sides. In shadow mode a diff with one side
    missing reports agreement with itself - gap A8, closed by the schedule itself.
  * job 4 before the gate, or the gate has nothing to compare.
  * 20:30 -> 20:40 -> 20:55 -> 21:00 is a chain: assets sync so tonight's wishlist reflects
    last night's drop; doctor so its RED lines exist to be carried; preparation so the
    payload is written before the brain reads it. Preparing at 21:00, the same minute as
    delivery, races `read_digest` against the row it needs.
  * jobs 1-6 weekly, 7-12 daily. Job 12 does not run on Sunday - the gate is that day's
    touch, asserted in the body and not only in the cron expression.

`cron create` has been observed REJECTING `SUN` while EXITING 0. Register, then VERIFY BY
LISTING. Exit code is not evidence. §2: the twelve in §3, and only those - a repo test fails
on a thirteenth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARTEC = "artec"          # artec-scheduler / artec api - has an HTTPS mirror
BRAIN = "brain"          # hermes-agent cron on artec-brain - no HTTPS mirror


@dataclass(frozen=True)
class Job:
    number: int
    name: str
    schedule: str                    # human-readable, Asia/Singapore
    owner: str
    body: str = ""
    mirror: str = ""
    cli: str = ""
    at: str = ""                     # "HH:MM" | "DOW HH:MM" (0=Sunday) | "" = slot-driven
    also_at: tuple[str, ...] = ()
    cron: str = ""                   # five-field expression for the brain's three
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


JOBS: tuple[Job, ...] = (
    Job(1, "report", "SUN 06:00", ARTEC, "app.stages.report.build_report",
        mirror="/commands/report", cli="artec report", at="0 06:00",
        notes="Weekly snapshot. PRICE RECONCILIATION IS THIS JOB'S FIRST ACTION, before the "
              "snapshot, so the report, the gate and every later render see reconciled "
              "rates rather than last week's."),
    Job(2, "bespoke-learn-ideate", "SUN 06:30", ARTEC, "app.stages.ideate.ideate",
        mirror="/commands/ideate", cli="artec learn && artec ideate", at="0 06:30",
        notes="MUST RUN BEFORE JOB 3. plan_source is `shadow`, so both planners produce a "
              "plan: this half writes the DRAFT rows, the agent's writes plans_shadow, and "
              "job 4 diffs them. A diff with one side missing reports agreement with "
              "itself - gap A8."),
    Job(3, "agent-learn-ideate", "SUN 07:00", BRAIN,
        "deploy/hermes-brain/cron-learn-ideate.txt", cron="0 7 * * 0",
        notes="The AGENT half. Writes plans_shadow in shadow mode."),
    Job(4, "plan-diff", "SUN 08:00", ARTEC, "app.stages.plan_diff.build_diff",
        mirror="/commands/plan-diff", cli="artec plan-diff", at="0 08:00",
        notes="MUST COMPLETE BEFORE THE GATE. The diff is the evidence for the I16 cutover "
              "decision; without it shadow mode proves nothing."),
    Job(5, "weekly-gate", "SUN 09:00", BRAIN,
        "deploy/hermes-brain/cron-weekly-gate.txt", cron="0 9 * * 0",
        notes="Touch 1 of the 7-touch contract - the one thing that never degrades."),
    Job(6, "render", "SUN 10:00, retry MON 10:00", ARTEC, "app.stages.render.render",
        mirror="/commands/render", cli="artec render", at="0 10:00", also_at=("1 10:00",),
        notes="TWICE A WEEK, NOT DAILY. There is no weekly fal cap: the weekly bound IS this "
              "job firing twice against the USD 2.50 per-run cap, so ~USD 5 worst case. "
              "Daily would make it ~USD 17.50 against a bound nobody set."),
    Job(7, "publish-by-slot", "daily, per slot_times entry", ARTEC,
        "app.scheduler.run_publish_job", mirror="/commands/publish-slot", cli="artec publish",
        notes="THE ONLY JOB WITH NO FIXED TIME - it fires per `slot_times` entry, which is "
              "why it resisted a time-ordered registry. APPROVED_TO_SEND enters here and "
              "nowhere earlier."),
    Job(8, "pg-dump", "daily 03:00", ARTEC, "app.stages.backup.run_backup",
        mirror="/commands/backup", cli="artec backup", at="03:00",
        notes="restore-check rides this job on day_of_month == 1 - still twelve jobs."),
    Job(9, "assets-sync", "daily 20:30", ARTEC, "app.stages.assets_sync.sync",
        mirror="/commands/assets-sync", cli="artec assets sync", at="20:30",
        notes="Assets sync AND wishlist match. First link of the nightly chain: tonight's "
              "wishlist must reflect last night's drop."),
    Job(10, "doctor-sweep", "daily 20:40", ARTEC, "app.stages.doctor.run_doctor",
        mirror="/commands/doctor", cli="artec doctor", at="20:40",
        notes="DAILY - its RED lines must exist before the digest is prepared fifteen "
              "minutes later. The memory audit rides this job."),
    Job(11, "digest-prepare", "daily 20:55", ARTEC, "app.stages.digest.prepare_digest",
        mirror="/commands/digest-prepare", cli="artec digest-prepare", at="20:55",
        notes="THE REVIEW EXPIRY SWEEP RUNS INSIDE THIS JOB, not beside it: job 11 already "
              "runs daily and reads exactly those posts, and a separate sweep could park a "
              "review the operator was about to answer. Five minutes before delivery."),
    Job(12, "digest-delivery", "MON-SAT 21:00", BRAIN,
        "deploy/hermes-brain/cron-nightly-digest.txt", cron="0 21 * * 1-6",
        notes="Does NOT run on Sunday - asserted in the body (read_digest returns "
              "deliver:false), not only in the cron expression."),
)

# RETIRED, named so neither can quietly return:
#   measure-reminder (daily 06:30) - the digest replaces it, and it was the only thing on a
#   bespoke service that sent to Telegram. D1 removes TELEGRAM_BOT_TOKEN from artec api and
#   artec-scheduler AFTER deploy, so the brain is STRUCTURALLY the sole Telegram owner.
#   review-expiry-sweep (daily 20:00) - folded into job 11; see its note.
RETIRED = ("measure-reminder", "review-expiry-sweep")


def artec_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == ARTEC)


def brain_jobs() -> tuple[Job, ...]:
    return tuple(j for j in JOBS if j.owner == BRAIN)


def mirror_paths() -> set[str]:
    return {j.mirror for j in artec_jobs() if j.mirror}


def firings() -> list[tuple[Job, str]]:
    """(job, "HH:MM" | "DOW HH:MM") for everything the scheduler fires on a clock. Job 7 is
    slot-driven and appears nowhere here - its times come from `slot_times`."""
    return [(job, when) for job in artec_jobs()
            for when in (job.at, *job.also_at) if when]


NON_JOB_ROUTES: dict[str, str] = {
    "/commands/learn": "runs immediately before job 2; separately invocable because "
                       "learn is worth re-reading without re-planning",
    "/commands/measure": "operator posts figures directly — the reminder job is RETIRED",
    "/commands/publish": "manual publish, including the CHECKPOINT 4 first publish",
    "/commands/sweep-reviews": "runs INSIDE job 11; exposed so a sweep can be forced",
    "/commands/wishlist-match": "runs inside job 9; exposed for the monthly wishlist review",
    "/commands/media/{post_id}": "not a job — deliver_video reads the publish bytes here",
    "/commands/gate": "409 by design: the gate is an interactive Telegram session",
    "/commands/match-probe": "not a job — READ-ONLY: can each DRAFT be serviced from the "
                             "bank? wishlist.match() only inspects PARKED posts, so a "
                             "draft's servicability was unanswerable until render parked "
                             "it, which is after the spend and after the gate",
    "/commands/digest-preview": "not a job — the half of job 12 that CAN be mirrored. "
                                "Delivery cannot: D1 makes the brain the sole Telegram "
                                "owner and this service has no token, deliberately. This "
                                "returns the exact message job 12 would send, at any hour, "
                                "WITHOUT marking it delivered",
    "/commands/prove": "not a job — operator-driven capability proofs (§9). CLI/HTTPS only, "
                       "never an agent tool, which is why it may write config.proofs",
    "/commands/restore-check": "rides job 8 monthly; exposed so a restore can be proven on "
                               "demand, which is the only time anyone wants it",
    "/commands/measure-reminder": "the RETIRED job's body, kept invocable only until D1 "
                                  "removes TELEGRAM_BOT_TOKEN from this service at merge",
}
