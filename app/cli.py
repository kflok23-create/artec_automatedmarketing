"""`hermes` — the Typer CLI. Every stage is manually invoked; each writes a `runs` row.
No step fires on a clock, ever.
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
cli.add_typer(config_app, name="config")
cli.add_typer(assets_app, name="assets")
cli.add_typer(wishlist_app, name="wishlist")


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
def config_seed(file: str = typer.Option(None, "--file", help="optional YAML/JSON overrides")):
    """Load §0 operator constants into the config table. Idempotent upsert."""
    _boot()
    from app.config import seed_config

    overrides = None
    if file:
        raw = open(file, encoding="utf-8").read()
        overrides = json_lib.loads(raw)
    with record_run("config seed", {"file": file}) as (session, rec):
        n = seed_config(session, overrides)
        rec.log(f"config seed: {n} keys written/updated")


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
        set_config(session, key, parsed)
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
