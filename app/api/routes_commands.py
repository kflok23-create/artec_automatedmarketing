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


@router.post("/digest-prepare")
def digest_prepare_cmd(body: CommandRequest):
    """Job 11 body over HTTPS. `week` doubles as the target date (default: yesterday).
    Returns both the payload and the operator-facing text so a dry run is judgeable."""
    from app.integrations.brevo_client import Brevo
    from app.stages.digest import prepare_digest, render_digest_text

    settings = get_settings()
    with record_run("digest-prepare", {"date": str(body.week) if body.week else None}) as (session, rec):
        try:
            brevo = Brevo(settings)
        except Exception:
            brevo = None
        payload = prepare_digest(session, brevo=brevo, target=body.week, log=rec.log)
        return {"payload": payload, "text": render_digest_text(payload)}


@router.get("/media/{post_id}")
def media_cmd(post_id: str):
    """THE bytes publish() will stream, served to the one caller that must show the
    operator the real artefact: `deliver_video` on the brain.

    The brain has no Drive credentials and must not grow any, so it reads through here —
    and because both sides call `publish_media_path`, "the operator approved one file and a
    different one went live" is not expressible. The sha256 is returned so the caller can
    prove byte-identity rather than assume it. A missing Drive file is a 404, never a
    fal-URL fallback: a silent fallback is exactly the divergence this closes.
    """
    from app.db import get_session_factory
    from app.integrations.drive_client import DriveClient

    session = get_session_factory()()
    try:
        return serve_post_media(session, DriveClient(get_settings()), post_id)
    finally:
        session.close()


def serve_post_media(session, drive, post_id: str):
    """The whole of the media route's logic, separated so it is directly testable.

    This is the only thing standing between a bearer token and the asset bank, so the
    boundary is narrow BY CONSTRUCTION rather than by validation: the caller names a POST,
    never a path and never a file id; the file id comes from that post's row; and the
    resolved Drive path must sit under `_generated/`. There is no listing, and no parameter
    that a traversal string could reach — `post_id` is only ever a database key.
    """
    import hashlib

    from fastapi import Response

    from app.config import get_config
    from app.models import Post
    from app.stages.publish import MediaNotInDrive, publish_media_path

    post = session.get(Post, post_id)
    if post is None:
        # Also the answer for any traversal-shaped id: it is a key that matches no row.
        raise HTTPException(status_code=404, detail=f"no such post: {post_id}")

    generated = str(get_config(session, "drive_generated_folder", "_generated"))
    try:
        drive_path = drive.resolve_path(drive.get_file(post.media_drive_file_id))
    except MediaNotInDrive:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"media-not-in-drive: cannot resolve {post_id} in Drive "
                   f"({type(e).__name__})") from None
    first = str(drive_path).replace("\\", "/").lstrip("/").split("/")[0]
    if first != generated:
        # A render output lives in _generated/ by §7.9. Anything else is either the raw
        # bank or a mistake, and neither is this route's to serve.
        raise HTTPException(
            status_code=403,
            detail=f"refused: {post_id} resolves to {drive_path!r}, outside {generated}/ — "
                   "this route serves render outputs only")

    try:
        local = publish_media_path(session, drive, post)
    except MediaNotInDrive as e:
        raise HTTPException(status_code=404, detail=f"media-not-in-drive: {e}") from None
    with open(local, "rb") as fh:
        data = fh.read()
    kind = "video/mp4" if local.endswith(".mp4") else "image/jpeg"
    return Response(
        content=data, media_type=kind,
        headers={"X-Artec-Media-SHA256": hashlib.sha256(data).hexdigest(),
                 "X-Artec-Media-Filename": f"{post_id}{local[local.rfind('.'):]}"},
    )


@router.post("/sweep-reviews")
def sweep_reviews_cmd():
    """v4 §E — park every review nobody answered inside its window. No auto-approve and no
    expire-to-send exists to be requested."""
    from app.scheduler import sweep_expired_reviews

    with record_run("sweep reviews", {}) as (session, rec):
        expired = sweep_expired_reviews(session, log=rec.log)
        return {"expired": expired, "count": len(expired)}


@router.post("/plan-diff")
def plan_diff_cmd(body: CommandRequest):
    """Shadow-mode artefact: bespoke vs agent plans with per-field agreement and learning
    cross-references. `week` is required."""
    from app.stages.plan_diff import build_diff

    if body.week is None:
        raise HTTPException(status_code=422, detail="week (YYYY-MM-DD Monday) is required")
    with record_run("plan-diff", {"week": str(body.week)}) as (session, _rec):
        return build_diff(session, body.week)


@router.post("/wishlist-match")
def wishlist_match_cmd():
    from app.stages.wishlist import match

    with record_run("wishlist match", {}) as (session, rec):
        return match(session, log=rec.log)


@router.post("/doctor")
def doctor_cmd():
    """CHECKPOINT 3 mirror: full green/red verification, incl. the live LoRA probes and
    the Drive write probe. Returns the table as JSON; `ok` is the overall verdict."""
    from app.db import get_session_factory
    from app.stages.doctor import run_doctor

    session = None
    try:
        session = get_session_factory()()
    except Exception:
        pass
    try:
        checks = run_doctor(get_settings(), session=session)
    finally:
        if session is not None:
            session.close()
    return {
        "ok": all(c.ok or c.warn for c in checks),
        "checks": [
            {"name": c.name, "status": "GREEN" if c.ok else ("YELLOW" if c.warn else "RED"),
             "detail": c.detail, "remedy": c.remedy if not c.ok else ""}
            for c in checks
        ],
    }


@router.post("/gate")
def gate_cmd():
    raise HTTPException(
        status_code=409,
        detail="gate is an interactive Telegram session — run `artec gate` from the CLI",
    )
