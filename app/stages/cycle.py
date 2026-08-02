"""`hermes cycle --dry-run` — learn→ideate→gate(auto)→render→publish→measure→report against
mocked externals, asserting the §13 invariants. Used in CI. Touches no real service and no
real database: it builds its own in-memory SQLite store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import seed_config, set_config
from app.integrations.fakes import (
    FakeBrevo,
    FakeDrive,
    FakeFal,
    FakeLLM,
    FakeUploadPost,
)
from app.models import V_BRIEF_SQL, Base, Metric, Post
from app.stages.ideate import ideate
from app.stages.learn import learn
from app.stages.measure import measure_json
from app.stages.publish import publish
from app.stages.render import render
from app.stages.report import build_report, print_report


class DryRunAssertion(AssertionError):
    pass


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise DryRunAssertion(message)


def cycle_dry_run(log=print) -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text(V_BRIEF_SQL))
        conn.commit()

    llm, fal, drive = FakeLLM(), FakeFal(), FakeDrive()
    uploader, brevo = FakeUploadPost(), FakeBrevo()

    with Session(engine) as session:
        seed_config(session)
        set_config(session, "seo_seeds", ["stem toys singapore", "artec blocks", "kids logic puzzle",
                                          "educational toys my", "building blocks classroom"])
        # Text cards need committed fonts; the dry run must not depend on operator files,
        # so route everything through the fake-served asset path instead: seed one bank asset.
        from app.models import Asset

        session.add(Asset(drive_file_id="asset_1", drive_path="raw-photo/assembled/a.jpg",
                          layer1="raw-photo", layer2="assembled", medium="photo",
                          subject="assembled_blocks", has_person=False, aspect="square",
                          status="active"))
        session.add(Asset(drive_file_id="asset_2", drive_path="raw-video/assembled/b.mp4",
                          layer1="raw-video", layer2="assembled", medium="video",
                          subject="assembled_blocks", has_person=False, aspect="vertical",
                          duration_s=8, status="active"))
        session.commit()

        week = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())

        # LEARN must not crash on a cold start (§ acceptance 20).
        out = learn(session, llm, week_start=week - timedelta(days=7))
        _require(out["cold_start"] is True, "cold-start learn should report insufficient data")

        ideate(session, llm, week_start=week)
        drafts = list(session.execute(select(Post).where(Post.status == "DRAFT")).scalars())
        _require(len(drafts) > 0, "ideate created no drafts")

        # Auto-gate: approve one photo post, reject the rest (rejected slots = fewer posts).
        approved = None
        for p in drafts:
            if approved is None and p.channel in ("instagram", "facebook", "linkedin"):
                p.status = "APPROVED"
                approved = p
            else:
                p.status = "REJECTED"
        session.commit()
        _require(approved is not None, "no draft could be approved")

        r = render(session, llm, drive, fal, all_approved=True)
        _require(r["rendered"] == 1, f"expected 1 rendered, got {r}")
        session.refresh(approved)
        _require(approved.media_drive_file_id is not None, "render did not persist media_drive_file_id")
        _require(drive.uploaded, "render did not upload into _generated/")

        set_config(session, "confirm_first_publish", False)  # CHECKPOINT 4 is a live-only gate
        session.commit()
        p = publish(session, drive, fal, uploader, brevo, all_rendered=True)
        _require(p["published"] == 1, f"expected 1 published, got {p}")
        session.refresh(approved)
        _require(approved.external_post_id is not None, "publish did not persist external_post_id")

        measure_json(session, {"rows": [{
            "post_id": approved.post_id, "channel": approved.channel,
            "metric_date": str(datetime.now(UTC).date()), "impressions": 1200, "saves": 4,
        }]})
        m = session.execute(select(Metric)).scalars().first()
        _require(m is not None and m.clicks is None, "skipped metric field must stay NULL, not 0")

        report = build_report(session, week_start=week)
        _require("revenue" in report and "engagement" in report, "report lost a lane")
        _require(not set(report["revenue"]) & set(report["engagement"]),
                 "revenue and engagement blocks must not share keys")
        print_report(report, log=log)

    log("cycle --dry-run: all assertions green")
    return {"ok": True}
