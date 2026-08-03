"""v3 acceptance 2 (volume), 3 (exactly four jobs), 17 (plan-diff), 20 (audit-memory),
21 (scheduler never republishes)."""

import re
from datetime import date
from pathlib import Path

from app.models import PlanShadow, Post
from app.scheduler import JOBS, select_due_posts
from app.stages.agent_review import audit_memory
from app.stages.doctor import check_hermes_home
from app.stages.plan_diff import build_diff

WEEK = date(2026, 8, 10)


# ---- acceptance 2: the hermes-brain volume ---------------------------------------------

def test_volume_check_red_when_missing(tmp_path):
    check = check_hermes_home(str(tmp_path / "does-not-exist"))
    assert not check.ok and not check.warn


def test_volume_check_red_when_not_writable(tmp_path):
    not_a_dir = tmp_path / "file-not-dir"
    not_a_dir.write_text("x", encoding="utf-8")
    check = check_hermes_home(str(not_a_dir))
    assert not check.ok


def test_volume_check_marker_survives(tmp_path):
    first = check_hermes_home(str(tmp_path))
    assert first.ok and "WRITTEN this run" in first.detail
    second = check_hermes_home(str(tmp_path))   # simulates the post-redeploy re-run
    assert second.ok and "survived redeploy" in second.detail


def test_volume_check_yellow_when_unset():
    check = check_hermes_home("")
    assert check.ok and check.warn  # not the hermes-brain service


# ---- acceptance 3: exactly four scheduled jobs across both codebases -------------------

def test_exactly_four_scheduled_jobs(repo_root):
    assert len(JOBS) == 2, "artec-scheduler owns exactly two jobs (publish, measure)"
    # hermes-agent cron jobs are registered by the entrypoint via `hermes cron create`
    # (jobs live in $HERMES_HOME/cron/jobs.json, not config.yaml — per the CLI docs).
    entrypoint = (repo_root / "deploy" / "hermes-brain" / "entrypoint.sh").read_text(encoding="utf-8")
    cron_creates = re.findall(r"hermes cron create\s+\"([^\"]+)\"", entrypoint)
    assert len(cron_creates) == 2, "hermes-agent owns exactly two cron jobs"
    assert set(cron_creates) == {"0 7 * * SUN", "0 9 * * SUN"}
    config = (repo_root / "deploy" / "hermes-brain" / "config.yaml").read_text(encoding="utf-8")
    assert "cron:" not in config, "cron jobs are CLI-registered, never declared in config.yaml"
    assert len(JOBS) + len(cron_creates) == 4


def test_no_other_timed_execution_in_app(repo_root):
    # The v2 import ban stands (cron/apscheduler/celery/rq/schedule) — test_hygiene keeps
    # it. Here: the only while-True-sleep loop in app/ is the scheduler's own main loop.
    offenders = []
    for path in (repo_root / "app").rglob("*.py"):
        if path.name == "scheduler.py":
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"while True:.{0,200}time\.sleep", content, re.DOTALL):
            offenders.append(str(path))
    assert not offenders, f"timed loops outside the scheduler: {offenders}"


# ---- acceptance 21: the publish scheduler never republishes ----------------------------

def test_scheduler_selection_excludes_published_posts(session):
    session.add(Post(post_id="post_7001", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening"))
    session.add(Post(post_id="post_7002", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening", external_post_id="up_99"))
    session.add(Post(post_id="post_7003", week_start=WEEK, channel="instagram",
                     status="PUBLISHED", slot="evening", external_post_id="up_98"))
    session.add(Post(post_id="post_7004", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="morning"))
    session.flush()
    due = select_due_posts(session, "evening")
    assert [p.post_id for p in due] == ["post_7001"]  # holder of an external id never fires


# ---- acceptance 17: plan-diff ----------------------------------------------------------

def test_plan_diff_field_agreement_rate(session):
    session.add(Post(post_id="post_7101", week_start=WEEK, channel="instagram",
                     status="DRAFT", angle="focus", hook="Same hook", cta_type="discount",
                     slot="evening", plan_source="bespoke"))
    session.add(Post(post_id="post_7102", week_start=WEEK, channel="tiktok",
                     status="DRAFT", angle="speed", hook="B hook", cta_type="discount",
                     slot="lunch", plan_source="bespoke"))
    session.add(PlanShadow(week_start=WEEK, channel="instagram", angle="focus",
                           hook="Same hook", cta_type="learn_more", slot="evening",
                           source="agent"))
    session.add(PlanShadow(week_start=WEEK, channel="linkedin", angle="authority",
                           hook="A only", cta_type="story", slot="morning", source="agent"))
    session.flush()

    diff = build_diff(session, WEEK)
    assert len(diff["pairs"]) == 1                       # instagram@evening paired
    assert diff["agreement"]["angle"] == 1.0
    assert diff["agreement"]["hook"] == 1.0
    assert diff["agreement"]["cta_type"] == 0.0          # discount vs learn_more
    assert [r["channel"] for r in diff["unique_bespoke"]] == ["tiktok"]
    assert [r["channel"] for r in diff["unique_agent"]] == ["linkedin"]


def test_plan_diff_rejected_bespoke_rows_excluded(session):
    session.add(Post(post_id="post_7103", week_start=WEEK, channel="facebook",
                     status="REJECTED", hook="rejected", slot="evening", plan_source="bespoke"))
    session.flush()
    diff = build_diff(session, WEEK)
    assert all(r["channel"] != "facebook" for r in diff["unique_bespoke"])


# ---- acceptance 20: audit-memory -------------------------------------------------------

MEMORY_FIXTURE = """# Creative playbook
Question-form hooks outperform statement hooks on TikTok for our audience.
Last week's CAC was S$18.40 on tiktok which is under the line.
Evening slots feel stronger for parent audiences.
2026-07-27 we got 4,200 impressions on the crane post.
Conversion improved 12% after the hook change.
"""


def test_audit_memory_flags_metric_shaped_content(tmp_path: Path):
    memory = tmp_path / "MEMORY.md"
    memory.write_text(MEMORY_FIXTURE, encoding="utf-8")
    hits = audit_memory([memory], log=lambda *_: None)
    kinds = {h["kind"] for h in hits}
    assert "currency amount" in kinds        # S$18.40 — the CAC figure
    assert "percentage" in kinds             # 12%
    assert "date-stamped count" in kinds     # 2026-07-27 … 4,200
    flagged_lines = {h["line"] for h in hits}
    assert 2 not in flagged_lines            # the pure-taste hypothesis is NOT flagged


def test_audit_memory_clean_playbook_passes(tmp_path: Path):
    memory = tmp_path / "MEMORY.md"
    memory.write_text("Hooks phrased as questions outperform statements.\n"
                      "Parents respond to calm, competence-forward framing.\n",
                      encoding="utf-8")
    assert audit_memory([memory], log=lambda *_: None) == []
