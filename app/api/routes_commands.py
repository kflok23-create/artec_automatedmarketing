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
    """Job 1. Price reconciliation runs FIRST, before the snapshot — reconciling afterwards
    would produce a report costed against rates already known to be wrong."""
    from app.config import get_config
    from app.stages.report import build_report
    from app.toolbox.reconcile import reconcile_prices

    with record_run("report", {"week": str(body.week) if body.week else None}) as (session, rec):
        cap = int(get_config(session, "render_run_cap_cents", 250))
        recon = reconcile_prices(session, cap, log=rec.log)
        report = build_report(session, week_start=body.week)
        report["reconciliation"] = {
            "pulled": recon.pulled, "source": recon.source,
            "applied": recon.applied, "needs_acknowledgement": recon.needs_acknowledgement,
            "stale": recon.stale}
        return report


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


@router.post("/backup")
def backup_cmd():
    """Job 8 body over HTTPS. On the first of the month it also runs restore-check —
    still ONE job, so the schedule stays twelve."""
    from app.integrations.drive_client import DriveClient
    from app.stages.backup import restore_check, rides_today, run_backup

    settings = get_settings()
    with record_run("backup", {}) as (session, rec):
        try:
            drive = DriveClient(settings)
        except Exception:
            drive = None
        result = run_backup(settings.DATABASE_URL, drive=drive, log=rec.log)
        if rides_today():
            proof = restore_check(session, settings.DATABASE_URL, result["local_path"],
                                  log=rec.log)
            result["restore_check"] = {"ok": proof.ok, "detail": proof.detail,
                                       "remedy": proof.remedy}
        return result


@router.post("/restore-check")
def restore_check_cmd(body: CommandRequest):
    """Prove the latest dump restores. RED with a reason beats a green line — a backup you
    cannot prove is a backup you do not have."""
    from app.stages.backup import restore_check

    settings = get_settings()
    with record_run("restore-check", {}) as (session, rec):
        path = body.post_id or ""      # reuse the free-text field for the dump path
        if not path:
            raise HTTPException(status_code=422,
                                detail="pass the dump path (post_id field) — run "
                                       "POST /commands/backup first")
        proof = restore_check(session, settings.DATABASE_URL, path, log=rec.log)
        return {"ok": proof.ok, "detail": proof.detail, "remedy": proof.remedy,
                "counts": proof.counts, "scratch_database": proof.scratch_database}


@router.post("/publish-slot")
def publish_slot_cmd(body: CommandRequest):
    """Job 1 body over HTTPS — the slot pass, by hand. A job body that only ever runs on a
    clock is a body nobody has exercised until the night it matters."""
    from app.scheduler import run_publish_job

    slot = body.post_id or ""
    if not slot:
        raise HTTPException(status_code=422, detail="pass the slot name (post_id field)")
    with record_run("publish-slot", {"slot": slot}) as (session, rec):
        return run_publish_job(session, slot, log=rec.log)


@router.post("/measure-reminder")
def measure_reminder_cmd():
    """Job 4 body over HTTPS. RETIRED AT MERGE (D1) — the brain becomes the sole Telegram
    owner and the digest carries the unmeasured list."""
    from app.scheduler import run_measure_job

    with record_run("measure-reminder", {}) as (session, rec):
        return run_measure_job(session, log=rec.log)


@router.post("/prove")
def prove_cmd(body: CommandRequest):
    """Exercise one capability and record a dated proof. CLI/HTTPS only — never an agent
    tool, which is why it may write `config.proofs` without breaching the invariant."""
    from app.stages import prove as prove_mod

    capability = body.post_id or ""
    if capability not in prove_mod.PROVERS:
        raise HTTPException(status_code=422,
                            detail=f"pass a capability (post_id field): "
                                   f"{sorted(prove_mod.PROVERS)}")
    with record_run("prove", {"capability": capability}) as (session, rec):
        try:
            proof = prove_mod.run(session, capability, settings=get_settings(), log=rec.log)
        except prove_mod.NotProvable as e:
            # NOT a pass and NOT a failure. Recording a skip as a proof is how a capability
            # comes to be believed without ever having run.
            raise HTTPException(status_code=409,
                                detail=f"not provable here: {e}") from None
        return {"capability": capability, "ok": proof.ok, "detail": proof.detail,
                "at": proof.at, "evidence": proof.evidence}


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
