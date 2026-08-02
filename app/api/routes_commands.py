"""Authenticated POST mirrors of the CLI stages — same functions, same behaviour.
The interactive stages (gate, interactive measure) live in the CLI; /commands/measure IS
the non-interactive measure form (operator posts figures straight to the Railway service).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_token
from app.db import record_run
from app.schemas import CommandRequest, MeasurePayload
from app.settings import get_settings

router = APIRouter(prefix="/commands", dependencies=[Depends(require_token)])


@router.post("/measure")
def measure_cmd(payload: MeasurePayload):
    from app.stages.measure import measure_json

    with record_run("measure", {"rows": len(payload.rows)}) as (session, rec):
        return measure_json(session, payload.model_dump(mode="json"), log=rec.log)


@router.post("/learn")
def learn_cmd(body: CommandRequest):
    from app.integrations.anthropic_client import LLM
    from app.stages.learn import learn

    with record_run("learn", {"week": str(body.week) if body.week else None}) as (session, rec):
        return learn(session, LLM(get_settings()), week_start=body.week, log=rec.log)


@router.post("/ideate")
def ideate_cmd(body: CommandRequest):
    from app.integrations.anthropic_client import LLM
    from app.stages.ideate import ideate

    with record_run("ideate", {"week": str(body.week) if body.week else None}) as (session, rec):
        return ideate(session, LLM(get_settings()), week_start=body.week, log=rec.log)


@router.post("/assets-sync")
def assets_sync_cmd(body: CommandRequest):
    from app.integrations.drive_client import DriveClient
    from app.stages.assets_sync import sync

    with record_run("assets sync", {"full": body.full}) as (session, rec):
        return sync(session, DriveClient(get_settings()), full=body.full, log=rec.log)


@router.post("/render")
def render_cmd(body: CommandRequest):
    from app.integrations.anthropic_client import LLM
    from app.integrations.drive_client import DriveClient
    from app.integrations.fal_client import Fal
    from app.stages.render import render

    s = get_settings()
    with record_run("render", body.model_dump(mode="json")) as (session, rec):
        return render(session, LLM(s), DriveClient(s), Fal(s),
                      post_ids=[body.post_id] if body.post_id else None,
                      all_approved=body.all_approved, log=rec.log)


@router.post("/publish")
def publish_cmd(body: CommandRequest):
    from app.integrations.brevo_client import Brevo
    from app.integrations.drive_client import DriveClient
    from app.integrations.fal_client import Fal
    from app.integrations.upload_post_client import UploadPost
    from app.stages.publish import FirstPublishNotConfirmed, publish

    s = get_settings()
    with record_run("publish", body.model_dump(mode="json")) as (session, rec):
        try:
            # confirm=False: the API mirror cannot answer an interactive prompt; the first
            # publish (CHECKPOINT 4) must happen once via the CLI.
            return publish(session, DriveClient(s), Fal(s), UploadPost(s), Brevo(s),
                           post_ids=[body.post_id] if body.post_id else None,
                           all_rendered=body.all_rendered, confirm=False, log=rec.log)
        except FirstPublishNotConfirmed as e:
            raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/report")
def report_cmd(body: CommandRequest):
    from app.stages.report import build_report

    with record_run("report", {"week": str(body.week) if body.week else None}) as (session, _rec):
        return build_report(session, week_start=body.week)


@router.post("/wishlist-match")
def wishlist_match_cmd():
    from app.stages.wishlist import match

    with record_run("wishlist match", {}) as (session, rec):
        return match(session, log=rec.log)


@router.post("/gate")
def gate_cmd():
    raise HTTPException(
        status_code=409,
        detail="gate is an interactive Telegram session — run `hermes gate` from the CLI",
    )
