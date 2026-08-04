"""`artec` — the bespoke Typer CLI; each command writes a `runs` row.

The name `hermes` belongs exclusively to the NousResearch hermes-agent CLI (`hermes
doctor`, `hermes update`, `hermes gateway`) — the bespoke CLI renamed to avoid the
collision. The only timed execution anywhere is the four v3 scheduled jobs (§9);
every other stage remains manually invoked.
"""

from __future__ import annotations

import json as json_lib
import sys
from datetime import datetime

import typer

from app.db import record_run
from app.settings import MissingEnvVarError, get_settings, install_redaction

cli = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
config_app = typer.Typer(no_args_is_help=True)
assets_app = typer.Typer(no_args_is_help=True)
wishlist_app = typer.Typer(no_args_is_help=True)
post_app = typer.Typer(no_args_is_help=True)
cli.add_typer(config_app, name="config")
cli.add_typer(assets_app, name="assets")
cli.add_typer(wishlist_app, name="wishlist")
cli.add_typer(post_app, name="post")


def _boot():
    try:
        settings = get_settings()
    except MissingEnvVarError as e:
        typer.echo(f"boot failed: {e}", err=True)
        raise typer.Exit(code=2) from None
    install_redaction(settings)
    return settings


def _parse_week(week: str | None):
    return datetime.strptime(week, "%Y-%m-%d").date() if week else None


@cli.command()
def doctor():
    """Verify every dependency. Green/red table; non-zero exit on any red (CHECKPOINT 3)."""
    settings = _boot()
    from app.db import get_session_factory
    from app.stages.doctor import print_checks, run_doctor

    session = None
    try:
        session = get_session_factory()()
    except Exception:
        pass
    checks = run_doctor(settings, session=session)
    ok = print_checks(checks, log=typer.echo)
    if session is not None:
        session.close()
    if not ok:
        typer.echo("\ndoctor: RED lines above must be fixed before the first cycle.")
        raise typer.Exit(code=1)
    typer.echo("\ndoctor: all green — HERMES is ready.")


@config_app.command("seed")
def config_seed(
    file: str = typer.Option(None, "--file", help="optional JSON overrides (explicitly overwrite those keys)"),
    force: bool = typer.Option(False, "--force", help="overwrite operator-set values with shipped defaults"),
):
    """Load §0 operator constants. NON-DESTRUCTIVE: adds missing keys, keeps existing
    values, and prints anything it left alone. --force overwrites (runtime state never)."""
    _boot()
    from app.config import seed_config

    overrides = None
    if file:
        raw = open(file, encoding="utf-8").read()
        overrides = json_lib.loads(raw)
    with record_run("config seed", {"file": file, "force": force}) as (session, rec):
        result = seed_config(session, overrides, force=force)
        rec.log(f"config seed: added {len(result['added'])} keys")
        if result["kept"]:
            rec.log("kept operator values (differ from shipped defaults, NOT overwritten): "
                    + ", ".join(result["kept"]) + "  — use --force to reset them")
        for line in result.get("needs_decision", []):
            rec.log("NEEDS YOUR DECISION: " + line)
        if result.get("upgraded"):
            rec.log("upgraded superseded defaults (stored value was the old shipped default, "
                    "so nobody had chosen it): " + ", ".join(result["upgraded"]))
        if result["overwritten"]:
            rec.log("OVERWROTE: " + ", ".join(result["overwritten"]))


@config_app.command("set")
def config_set(key: str, value: str):
    """Set one config key. VALUE is JSON (strings need quotes: '\"abc\"')."""
    _boot()
    from app.config import set_config

    try:
        parsed = json_lib.loads(value)
    except json_lib.JSONDecodeError:
        parsed = value
    with record_run("config set", {"key": key}) as (session, rec):
        set_config(session, key, parsed, set_by="operator")
        rec.log(f"config set: {key}")


@config_app.command("get")
def config_get(key: str):
    _boot()
    from app.config import get_config
    from app.db import session_scope

    with session_scope() as session:
        typer.echo(json_lib.dumps(get_config(session, key), indent=2, default=str))


@assets_app.command("sync")
def assets_sync(full: bool = typer.Option(False, "--full", help="force a complete rescan")):
    """Walk the bank (or replay Drive changes) into the assets table."""
    settings = _boot()
    from app.integrations.drive_client import DriveClient
    from app.stages.assets_sync import sync

    with record_run("assets sync", {"full": full}) as (session, rec):
        sync(session, DriveClient(settings), full=full, log=rec.log)


@cli.command()
def learn(week: str = typer.Option(None, "--week", help="YYYY-MM-DD (Monday)")):
    settings = _boot()
    from app.integrations.anthropic_client import LLM
    from app.stages.learn import learn as learn_stage

    with record_run("learn", {"week": week}) as (session, rec):
        learn_stage(session, LLM(settings), week_start=_parse_week(week), log=rec.log)


@cli.command()
def ideate(week: str = typer.Option(None, "--week", help="YYYY-MM-DD (Monday)")):
    settings = _boot()
    from app.integrations.anthropic_client import LLM
    from app.stages.ideate import ideate as ideate_stage

    with record_run("ideate", {"week": week}) as (session, rec):
        ideate_stage(session, LLM(settings), week_start=_parse_week(week), log=rec.log)


@cli.command()
def gate(
    timeout: int = typer.Option(3600, "--timeout"),
    wishlist: bool = typer.Option(False, "--wishlist"),
):
    """Interactive Telegram gate: Approve / Edit / Reject per draft; '+' injects; /done ends."""
    settings = _boot()
    from app.integrations.telegram_client import Telegram
    from app.stages.gate import gate as gate_stage

    with record_run("gate", {"timeout": timeout, "wishlist": wishlist}) as (session, rec):
        gate_stage(session, Telegram(settings), timeout=timeout, wishlist=wishlist, log=rec.log)


@cli.command()
def render(
    post_id: str = typer.Option(None, "--post-id"),
    all_approved: bool = typer.Option(False, "--all-approved"),
):
    settings = _boot()
    if not post_id and not all_approved:
        typer.echo("pass --post-id X or --all-approved", err=True)
        raise typer.Exit(code=2)
    from app.integrations.anthropic_client import LLM
    from app.integrations.drive_client import DriveClient
    from app.integrations.fal_client import Fal
    from app.stages.render import render as render_stage

    with record_run("render", {"post_id": post_id, "all_approved": all_approved}) as (session, rec):
        render_stage(session, LLM(settings), DriveClient(settings), Fal(settings),
                     post_ids=[post_id] if post_id else None, all_approved=all_approved,
                     log=rec.log)


@cli.command()
def publish(
    post_id: str = typer.Option(None, "--post-id"),
    all_rendered: bool = typer.Option(False, "--all-rendered"),
):
    """Publish RENDERED posts. The first ever publish halts for confirmation (CHECKPOINT 4)."""
    settings = _boot()
    if not post_id and not all_rendered:
        typer.echo("pass --post-id X or --all-rendered", err=True)
        raise typer.Exit(code=2)
    from app.integrations.brevo_client import Brevo
    from app.integrations.drive_client import DriveClient
    from app.integrations.fal_client import Fal
    from app.integrations.upload_post_client import UploadPost
    from app.stages.publish import publish as publish_stage

    with record_run("publish", {"post_id": post_id, "all_rendered": all_rendered}) as (session, rec):
        publish_stage(session, DriveClient(settings), Fal(settings), UploadPost(settings),
                      Brevo(settings), post_ids=[post_id] if post_id else None,
                      all_rendered=all_rendered, confirm=True, log=rec.log)


@cli.command()
def measure(
    since: str = typer.Option(None, "--since", help="YYYY-MM-DD (default: yesterday)"),
    json: str = typer.Option(None, "--json", help="inline JSON payload {\"rows\": [...]}"),
):
    """Direct metric entry — interactive field-by-field, or --json. Blank = NULL, never 0."""
    _boot()
    from app.stages.measure import measure_interactive, measure_json

    with record_run("measure", {"since": since, "json": bool(json)}) as (session, rec):
        if json:
            measure_json(session, json_lib.loads(json), log=rec.log)
        else:
            measure_interactive(session, since=_parse_week(since), log=rec.log)


@cli.command()
def report(week: str = typer.Option(None, "--week")):
    """Two blocks, never combined: REVENUE (orders) and ENGAGEMENT (events + metrics)."""
    _boot()
    from app.stages.report import build_report, print_report

    with record_run("report", {"week": week}) as (session, rec):
        print_report(build_report(session, week_start=_parse_week(week)), log=rec.log)


@wishlist_app.command("show")
def wishlist_show():
    _boot()
    from app.stages.wishlist import show

    with record_run("wishlist show", {}) as (session, rec):
        show(session, log=rec.log)


@wishlist_app.command("match")
def wishlist_match():
    """Return PARKED posts to APPROVED once newly synced assets can service them."""
    _boot()
    from app.stages.wishlist import match

    with record_run("wishlist match", {}) as (session, rec):
        match(session, log=rec.log)


@wishlist_app.command("fulfil")
def wishlist_fulfil(
    post_id: str = typer.Option(..., "--post-id"),
    drive_file_id: str = typer.Option(..., "--drive-file-id"),
):
    _boot()
    from app.stages.wishlist import fulfil

    with record_run("wishlist fulfil", {"post_id": post_id}) as (session, rec):
        fulfil(session, post_id, drive_file_id, log=rec.log)


@post_app.command("retry")
def post_retry(post_id: str = typer.Option(..., "--post-id")):
    """Return a FAILED post to RENDERED so `artec publish` can retry it.

    Refuses if the post ever received an external_post_id — that means the surface
    accepted it and a retry would double-publish (the most expensive possible bug)."""
    _boot()
    from app.models import Post

    with record_run("post retry", {"post_id": post_id}) as (session, rec):
        post = session.get(Post, post_id)
        if post is None:
            rec.log(f"{post_id}: not found")
            raise typer.Exit(code=2)
        if post.external_post_id:
            rec.log(f"{post_id}: REFUSED — external_post_id={post.external_post_id} means the "
                    "platform accepted it; retrying would double-publish")
            raise typer.Exit(code=2)
        if post.status != "FAILED":
            rec.log(f"{post_id}: status is {post.status}, nothing to retry")
            raise typer.Exit(code=2)
        rec.log(f"{post_id}: last failure was: {post.park_reason or '<none recorded>'}")
        post.status = "RENDERED"
        post.park_reason = None
        session.flush()
        rec.log(f"{post_id}: FAILED → RENDERED — re-run `artec publish --post-id {post_id}`")


@post_app.command("show")
def post_show(post_id: str = typer.Option(..., "--post-id")):
    """Print one post's full state (status, genome, media ids, spine, failure reason)."""
    _boot()
    from app.db import session_scope
    from app.models import Post

    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            typer.echo(f"{post_id}: not found", err=True)
            raise typer.Exit(code=2)
        for field in ("post_id", "week_start", "channel", "status", "angle", "hook", "cta_type",
                      "cta_placement", "slot", "keywords", "visual_tools", "source_asset_ids",
                      "media_drive_file_id", "media_url", "tracked_url", "external_post_id",
                      "posted_at", "park_reason"):
            typer.echo(f"{field:20} {getattr(post, field)}")
        typer.echo(f"{'caption':20} {post.caption}")


@cli.command("plan-diff")
def plan_diff(week: str = typer.Option(..., "--week", help="YYYY-MM-DD (Monday)")):
    """Shadow mode: bespoke vs agent plans side by side, with per-field agreement.
    Read this for 2–3 Sundays before flipping plan_source."""
    _boot()
    from app.stages.plan_diff import build_diff, print_diff

    with record_run("plan-diff", {"week": week}) as (session, rec):
        print_diff(build_diff(session, _parse_week(week)), log=rec.log)


@cli.command("digest-prepare")
def digest_prepare(
    date_str: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: yesterday)"),
    show: bool = typer.Option(False, "--show", help="print the operator-facing text"),
):
    """Job 11 body: prepare the 21:00 digest into the digests table. Idempotent per date.
    Registers with no cron — the twelve jobs are wired in Stage 2c."""
    settings = _boot()
    from app.integrations.brevo_client import Brevo
    from app.stages.digest import prepare_digest, render_digest_text

    with record_run("digest-prepare", {"date": date_str}) as (session, rec):
        try:
            brevo = Brevo(settings)
        except Exception:
            brevo = None   # count reported UNAVAILABLE, never 0
        payload = prepare_digest(session, brevo=brevo, target=_parse_week(date_str),
                                 log=rec.log)
        if show:
            typer.echo("\n" + render_digest_text(payload))


@cli.command("sweep-reviews")
def sweep_reviews():
    """v4 §E: park every email/video review nobody answered inside its window. There is no
    auto-approve and no expire-to-send — stale email is worse than no email."""
    _boot()
    from app.scheduler import sweep_expired_reviews

    with record_run("sweep reviews", {}) as (session, rec):
        expired = sweep_expired_reviews(session, log=rec.log)
        rec.log(f"sweep: {len(expired)} review(s) expired and PARKED")


@cli.command("backup")
def backup_cmd():
    """Job 8: pg_dump the live database to Drive _backups/. On the first of the month it
    also proves the dump restores — still ONE job."""
    settings = _boot()
    from app.integrations.drive_client import DriveClient
    from app.stages.backup import BackupError, restore_check, rides_today, run_backup

    with record_run("backup", {}) as (session, rec):
        try:
            drive = DriveClient(settings)
        except Exception as e:
            rec.log(f"backup: no Drive client ({type(e).__name__}) — dumping locally only")
            drive = None
        try:
            result = run_backup(settings.DATABASE_URL, drive=drive, log=rec.log)
        except BackupError as e:
            typer.echo(f"backup FAILED: {e}", err=True)
            raise typer.Exit(code=1) from None
        if rides_today():
            proof = restore_check(session, settings.DATABASE_URL, result["local_path"],
                                  log=rec.log)
            typer.echo(("restore-check: OK — " if proof.ok else "restore-check: RED — ")
                       + proof.detail)
            if not proof.ok:
                typer.echo(proof.remedy, err=True)
                raise typer.Exit(code=1)


@cli.command("restore-check")
def restore_check_cmd(
    dump: str = typer.Option(..., "--dump", help="path to the .dump produced by `artec backup`"),
):
    """Prove the dump restores: scratch DATABASE, row counts, drop. RED with a reason
    beats a green line — a backup you cannot prove is a backup you do not have."""
    settings = _boot()
    from app.stages.backup import restore_check

    with record_run("restore-check", {"dump": dump}) as (session, rec):
        proof = restore_check(session, settings.DATABASE_URL, dump, log=rec.log)
        typer.echo(("OK — " if proof.ok else "RED — ") + proof.detail)
        if not proof.ok:
            typer.echo(proof.remedy, err=True)
            raise typer.Exit(code=1)


@cli.command("prove")
def prove_cli(
    capability: str = typer.Argument(..., help="one of the nine capabilities"),
    dump: str = typer.Option(None, "--dump", help="for `restore`: the .dump to verify"),
    live: bool = typer.Option(False, "--live", help="brevo-send only; refused in 2c-iii"),
):
    """Exercise a capability against production and record a dated proof."""
    settings = _boot()
    from app.stages import prove as prove_mod

    if capability not in prove_mod.PROVERS:
        typer.echo(f"unknown capability. one of: {', '.join(sorted(prove_mod.PROVERS))}",
                   err=True)
        raise typer.Exit(code=2)
    with record_run("prove", {"capability": capability}) as (session, rec):
        try:
            proof = prove_mod.run(session, capability, settings=settings, log=rec.log,
                                  dump_path=dump, live=live)
        except prove_mod.NotProvable as e:
            typer.echo(f"NOT PROVABLE HERE: {e}", err=True)
            raise typer.Exit(code=3) from None
        typer.echo(("PROVEN — " if proof.ok else "FAILED — ") + proof.detail)
        if not proof.ok:
            raise typer.Exit(code=1)


@cli.command("proofs")
def proofs_cli():
    """The proof matrix: which capabilities are proven, stale, failed, or never run."""
    _boot()
    from app.db import session_scope
    from app.stages.prove import proof_status

    with session_scope() as session:
        for row in proof_status(session):
            mark = {"proven": "OK  ", "stale": "OLD ", "failed": "FAIL", "never": "----"}[row["state"]]
            s1 = " [S1]" if row["s1"] else ""
            typer.echo(f"{mark} {row['capability']:<20}{s1:<6} {row.get('at') or 'never run'}"
                       f"  {row.get('detail', '')[:60]}")


@cli.command("jobs")
def jobs_cmd():
    """The twelve jobs, their owners, and how a human invokes each one by hand."""
    from app.jobs import JOBS

    for job in JOBS:
        mirror = job.mirror or "(brain — hermes cron, no HTTPS mirror)"
        typer.echo(f"{job.number:>2}. {job.name:<22} {job.schedule:<38} {job.owner:<6} {mirror}")
        if job.notes:
            typer.echo(f"    {job.notes}")


@cli.command("audit-memory")
def audit_memory_cmd(
    path: list[str] = typer.Option(None, "--path", help="file/dir to scan (default: $HERMES_HOME)"),  # noqa: B008
):
    """Scan agent memory + skills for metric-shaped content. Numbers live in Postgres only."""
    _boot()
    from pathlib import Path

    from app.stages.agent_review import audit_memory

    paths = [Path(p) for p in path] if path else None
    hits = audit_memory(paths, log=typer.echo)
    if hits:
        raise typer.Exit(code=1)


@cli.command("agent-review")
def agent_review_cmd():
    """Monthly first-Sunday session: print the agent's skill list and MEMORY.md."""
    _boot()
    from app.stages.agent_review import (
        agent_review,
        config_drift_check,
        main_ci_gate_check,
        toolset_drift_check,
    )

    agent_review(log=typer.echo)
    drift = toolset_drift_check(log=typer.echo)
    config_drift = config_drift_check(log=typer.echo)
    gate = main_ci_gate_check(log=typer.echo)
    if drift["missing"] or config_drift["drifted"] or (gate["checked"] and not gate["green"]):
        raise typer.Exit(code=1)


@cli.command()
def cycle(dry_run: bool = typer.Option(False, "--dry-run")):
    """--dry-run: full cycle against mocked externals (CI). Live cycles are run stage by stage."""
    if not dry_run:
        typer.echo("cycle runs stage by stage in production — use --dry-run for the mocked pass", err=True)
        raise typer.Exit(code=2)
    from app.stages.cycle import cycle_dry_run

    cycle_dry_run(log=typer.echo)


def main() -> None:
    try:
        cli()
    except MissingEnvVarError as e:
        typer.echo(f"boot failed: {e}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
