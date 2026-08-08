"""`artec doctor` — green/red verification of every dependency, with a named remedy per
red line. Exits non-zero on any red. Includes the live LoRA base-model probe (§7.2) and the
Drive `_generated/` write probe.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

import httpx

from app.config import OPERATOR_CONSTANTS
from app.settings import Settings
from app.taxonomy import EXPECTED_FOLDERS, GENERATED_FOLDER
from app.toolbox.generate import probe_request
from app.toolbox.text_card import FONTS_DIR


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    remedy: str = ""
    warn: bool = False  # yellow: not fatal


def _check(name: str, fn, remedy: str) -> Check:
    try:
        detail = fn()
        return Check(name, True, detail if isinstance(detail, str) else "")
    except Exception as e:
        return Check(name, False, f"{type(e).__name__}: {e}", remedy)


def lora_probe_checks(settings: Settings) -> list[Check]:
    """The live LoRA probes validate the GENERATE path only (the enhance path uses no
    LoRA). With generate dormant they are SKIPPED — yellow, with the reason stated —
    never a spend, never a misleading RED. Re-enabling generate runs them live again."""
    names = ("fal LoRA probe: assembled", "fal LoRA probe: unassembled")
    if not OPERATOR_CONSTANTS["generate_enabled"]:
        return [Check(name, True,
                      "SKIPPED — generate path is dormant (generate_enabled=false); this "
                      "probe only validates the dormant GENERATE path, not enhance",
                      warn=True) for name in names]

    def _lora_probe(key: str):
        from app.integrations.fal_client import Fal
        fal = Fal(settings)
        endpoint, args = probe_request(OPERATOR_CONSTANTS["loras"][key],
                                       OPERATOR_CONSTANTS["model_endpoints"]["lora_generate"])
        try:
            fal.run(endpoint, args, timeout_s=180)
        except Exception as e:
            msg = str(e).lower()
            if any(w in msg for w in ("shape", "key", "state-dict", "state_dict", "mismatch")):
                raise RuntimeError(
                    f"LoRA '{key}' rejected by qwen-image-2512 — weights are not "
                    "Qwen-Image-2512 compatible (retrain on fal-ai/qwen-image-2512-trainer)"
                ) from e
            raise
        return "loaded"

    remedy = ("verify FAL_KEY; if a base-model mismatch is named, retrain the LoRA on "
              "fal-ai/qwen-image-2512-trainer")
    return [_check(names[0], lambda: _lora_probe("assembled"), remedy),
            _check(names[1], lambda: _lora_probe("unassembled"), remedy)]


def check_hermes_home(hermes_home: str, session=None) -> Check:
    """v3 §5: the mounted volume is a hard requirement ON HERMES-BRAIN. A marker file
    written on first check and read back on later ones is the survives-redeploy proof:
    if the marker predates this boot, the volume outlived at least one deploy."""
    name = "hermes-brain volume"
    remedy = ("mount a Railway volume at /data/hermes on hermes-brain, set "
              "HERMES_HOME=/data/hermes, and ensure it is writable")

    if hermes_home:
        # Local mode: we ARE on the box with the volume — probe it directly.
        from pathlib import Path

        root = Path(hermes_home)
        if not root.is_dir():
            return Check(name, False, f"{hermes_home} is missing or not a directory", remedy)
        marker = root / ".artec-volume-marker"
        try:
            if marker.exists():
                born = marker.read_text(encoding="utf-8").strip()
                return Check(name, True, f"writable; marker present since {born} — survived redeploy(s)")
            from datetime import UTC, datetime

            marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
            return Check(name, True,
                         "writable; marker WRITTEN this run — redeploy and re-run doctor to "
                         "prove the volume survives")
        except OSError as e:
            return Check(name, False, f"volume is not writable: {type(e).__name__}: {e}", remedy)

    # Remote mode (artec-api has no HERMES_HOME): verify via the Postgres row the
    # hermes-brain entrypoint writes at every boot. A check that passes by skipping is
    # worse than no check — no report is RED, not green.
    remote_remedy = ("deploy hermes-brain (its entrypoint writes the volume marker to the "
                     "config row 'hermes_brain_volume' on every boot) or read its boot log "
                     "to see why the report step failed")
    if session is None:
        return Check(name, False, "no DB session — cannot verify the hermes-brain volume", remote_remedy)
    from app.config import get_config

    data = get_config(session, "hermes_brain_volume", None)
    if not data:
        return Check(name, False, "hermes-brain has never reported its volume marker", remote_remedy)
    since = str(data.get("marker_since", ""))
    boot = str(data.get("boot_at", ""))
    home = data.get("hermes_home", "?")
    if since and boot and since < boot:
        return Check(name, True,
                     f"{home} writable; marker since {since}, last boot {boot} — SURVIVED redeploy(s)")
    return Check(name, True,
                 f"{home} writable; marker written at first boot {boot} — redeploy "
                 "hermes-brain and re-run doctor to prove the volume survives",
                 warn=True)


def run_doctor(settings: Settings, session=None, log=print) -> list[Check]:  # noqa: C901
    checks: list[Check] = []

    # --- database ----------------------------------------------------------------------
    from app.db import db_ok, migration_current

    checks.append(_check("postgres reachable", lambda: "ok" if db_ok() else (_ for _ in ()).throw(RuntimeError("connect failed")),
                         "check DATABASE_URL is the Railway reference variable ${{ Postgres.DATABASE_URL }}"))
    checks.append(_check("migrations current", lambda: "ok" if migration_current() else (_ for _ in ()).throw(RuntimeError("alembic_version != head")),
                         "run `alembic upgrade head` (Railway pre-deploy runs it automatically)"))

    # --- local tooling -----------------------------------------------------------------
    checks.append(_check("ffmpeg on PATH", lambda: shutil.which("ffmpeg") or (_ for _ in ()).throw(RuntimeError("not found")),
                         "nixpacks.toml installs ffmpeg on Railway; locally: install ffmpeg"))
    # ffprobe is a SEPARATE binary. The publish pre-flight cannot verify a single video
    # without it, and "ffmpeg is present" is an inference, not evidence. Packaging class.
    checks.append(_check("ffprobe on PATH", lambda: shutil.which("ffprobe") or (_ for _ in ()).throw(RuntimeError("not found")),
                         "the ffmpeg package bundles ffprobe — if ffmpeg is green and this "
                         "is red, the container has a partial install and video pre-flight "
                         "cannot run"))

    # THE DOCTOR WATCHED THE TWO BINARIES THE TOOLBOX NEEDS AND NOT THE ONE THE BACKUP DOES.
    #
    #   2026-08-07T19:00:09Z  job 8 pg-dump FAILED: BackupError: pg_dump is not on PATH in
    #                         this process — the nightly dump cannot run.
    #
    # Every night, and nothing here was RED, because nothing here looked. `nixpacks.toml`
    # installs ffmpeg and not postgresql, and the only component that noticed was the job
    # itself, in a log line nobody was reading. There has been no backup.
    #
    # PRESENCE IS NOT ENOUGH, and this is where the check earns the name. The server is
    # Postgres 18; pg_dump REFUSES a server newer than itself ("aborting because of server
    # version mismatch"). A pg_dump 16 on PATH would turn this green and still take no
    # backup. WHAT SUPPLIES EACH SIDE: the client major comes from `pg_dump --version`, the
    # server major from the live connection. Neither is derived from the other, and neither
    # can be absent while the check still passes.
    def _pg_dump():
        path = shutil.which("pg_dump")
        if not path:
            raise RuntimeError("not found — no backup has been taken since this became true")
        out = subprocess.run([path, "--version"], capture_output=True, encoding="utf-8",
                             errors="replace", timeout=30).stdout or ""
        found = re.search(r"(\d+)", out)
        if not found:
            raise RuntimeError(f"pg_dump --version unparseable: {out.strip()[:80]!r}")
        client = int(found.group(1))

        from sqlalchemy import text as _text

        from app.db import get_engine

        with get_engine().connect() as conn:
            server_raw = conn.execute(_text("SHOW server_version")).scalar() or ""
        server = int(re.match(r"(\d+)", str(server_raw)).group(1))
        if client < server:
            raise RuntimeError(
                f"pg_dump is {client} but the server is {server} — pg_dump refuses a server "
                "newer than itself, so this would abort every night while looking installed")
        return f"pg_dump {client} vs server {server}"
    checks.append(_check("pg_dump present and not older than the server", _pg_dump,
                         "add postgresql_18 to nixPkgs in nixpacks.toml — job 8 is the only "
                         "backup this system has, and a reachable database is not evidence "
                         "that pg_dump ships with it"))

    # v4: advisory locks, sequences and jsonb are Postgres-only. A deployed service on any
    # other dialect would silently lose those guarantees, so this is RED, never a warning.
    def _dialect():
        from app.db import get_engine

        name = get_engine().dialect.name
        if name != "postgresql":
            raise RuntimeError(f"connected database is {name}, not postgresql")
        return "postgresql"
    checks.append(_check("database is postgres", _dialect,
                         "advisory locks (job de-duplication), post_id_seq and jsonb all "
                         "require PostgreSQL — a non-Postgres database silently voids them"))
    fonts = OPERATOR_CONSTANTS["fonts"]
    missing_fonts = sorted(f for f in set(fonts.values()) if not (FONTS_DIR / f).exists())
    found_fonts = sorted(p.name for p in FONTS_DIR.iterdir()) if FONTS_DIR.is_dir() else []
    checks.append(Check(
        "brand fonts committed", not missing_fonts,
        f"ok ({FONTS_DIR})" if not missing_fonts
        else f"dir: {FONTS_DIR} · expected: {missing_fonts} · found: {found_fonts}",
        "commit the STATIC .ttf files to assets/fonts/ with exactly the config.fonts "
        "filenames (see docs/ASSET_BANK.md); compare expected vs found above",
    ))

    # --- Anthropic ---------------------------------------------------------------------
    def _anthropic():
        from app.integrations.anthropic_client import LLM
        LLM(settings).ping()
        return f"model {settings.ANTHROPIC_MODEL}"
    checks.append(_check("anthropic key + model", _anthropic,
                         "check ANTHROPIC_API_KEY and ANTHROPIC_MODEL in Railway"))

    # --- fal LoRA probes — only meaningful for the (dormant) generate path -------------
    checks.extend(lora_probe_checks(settings))
    checks.append(Check("generate path", True,
                        "DORMANT (generate_enabled=false, v3 Rule 3 — bank-only for the product)"
                        if not OPERATOR_CONSTANTS["generate_enabled"] else "ENABLED",
                        warn=False))

    # v3 Rule 4: every surviving model endpoint must be priced or it is uncallable.
    # v4 moved prices OUT of config into the `endpoint_prices` TABLE (§7·C5) so that
    # reconciliation can write them without any tool writing config. This check was left
    # reading the deleted config key and raised KeyError on every call — which took
    # `artec doctor` AND `POST /commands/doctor` down with it, on main. A doctor that
    # cannot run is worse than a missing check: it is a green light that never lights.
    endpoints = set(OPERATOR_CONSTANTS["model_endpoints"].values())
    if session is None:
        checks.append(Check("endpoint price table", True,
                            "not checked — no database session", warn=True))
    else:
        from app.toolbox.pricing import price_table

        try:
            priced = {row["endpoint"] for row in price_table(session)}
        except Exception as e:
            # A doctor that raises is worse than a doctor that reports RED: it takes every
            # OTHER check down with it and answers 500 to the endpoint whose whole job is
            # to say what is wrong.
            checks.append(Check("endpoint price table", False,
                                f"cannot read endpoint_prices: {type(e).__name__}",
                                "run `alembic upgrade head`, then `artec config seed`"))
        else:
            unpriced = sorted(endpoints - priced)
            checks.append(Check(
                "endpoint price table", not unpriced,
                f"every model endpoint priced ({len(priced)} rows)" if not unpriced
                else f"unpriced: {unpriced}",
                "run `artec config seed` — prices live in the endpoint_prices TABLE in v4, "
                "and an unpriced endpoint is uncallable by design"))

    # v4 §9 — a capability that has never run is not a capability, it is an intention with
    # code attached. Absent or >90d is YELLOW; a recorded FAILURE is RED.
    if session is not None:
        from app.stages.prove import proof_status

        rows = proof_status(session)
        failed = [r["capability"] for r in rows if r["state"] == "failed"]
        pending = [r["capability"] for r in rows if r["state"] in ("never", "stale")]
        if failed:
            checks.append(Check("capability proofs", False,
                                f"FAILED: {failed}" + (f"; unproven: {pending}" if pending else ""),
                                "run `artec prove <capability>` and fix what it reports"))
        elif pending:
            checks.append(Check("capability proofs", True,
                                f"{len(rows) - len(pending)}/{len(rows)} proven; "
                                f"unproven or stale: {pending}",
                                warn=True))
        else:
            checks.append(Check("capability proofs", True,
                                f"all {len(rows)} capabilities proven within 90 days"))

    # D1 — the assertion FLIPS. On a bespoke service a Telegram token must NOT exist: the
    # brain becomes the sole Telegram owner STRUCTURALLY rather than by policy, and two
    # pollers on one token is a 409 that breaks the live gate. On the brain it must exist.
    on_brain = bool(settings.HERMES_HOME)
    has_token = bool(settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_CHAT_ID)
    if on_brain:
        checks.append(Check("telegram ownership", has_token,
                            "brain holds the token, as it must" if has_token
                            else "the brain has NO Telegram token — the gateway cannot connect",
                            "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on artec-brain"))
    else:
        checks.append(Check(
            "telegram ownership", not has_token,
            "no Telegram credentials on this service, as required (D1)" if not has_token
            else "TELEGRAM_BOT_TOKEN/CHAT_ID are STILL SET on this bespoke service — the "
                 "brain is meant to be the only Telegram owner, and a second poller on one "
                 "token is a 409 that breaks the live gate",
            "delete TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from artec api and "
            "artec-scheduler (post-deploy step of D1)"))

    # v3 §5: the hermes-brain volume. Local probe when HERMES_HOME is set; otherwise
    # verified through the Postgres row the brain's entrypoint writes at every boot.
    checks.append(check_hermes_home(settings.HERMES_HOME, session))

    # --- Upload-Post -------------------------------------------------------------------
    def _upload_post():
        from app.integrations.upload_post_client import PLATFORMS, UploadPost
        body = UploadPost(settings).list_profiles()
        profiles = body.get("profiles") or body.get("users") or []
        connected: set[str] = set()
        for prof in profiles:
            accounts = prof.get("social_accounts") or prof.get("platforms") or {}
            if isinstance(accounts, dict):
                connected |= {k.lower() for k, v in accounts.items() if v}
            elif isinstance(accounts, list):
                connected |= {str(a).lower() for a in accounts}
        missing = [p for p in PLATFORMS if p not in connected]
        if missing:
            raise RuntimeError(f"platforms not connected: {missing}")
        return "all five connected"
    checks.append(_check("upload-post key + 5 platforms", _upload_post,
                         "connect the missing platforms in the Upload-Post dashboard (OAuth cannot be automated)"))

    # --- WHERE A POST WILL ACTUALLY LAND -------------------------------------------------
    # The check above proves CONNECTION. Connection is presence, not validity: both
    # platforms are connected through a PERSONAL account, so "connected" is equally true
    # whether posts land on the Artec page or on someone's personal timeline.
    #
    # WHAT SUPPLIES EACH SIDE: the configured id comes from `config`; the set of reachable
    # pages comes from Upload-Post's LIVE listing. The publisher supplies neither, so this
    # cannot pass by agreeing with itself.
    def _page_targets():
        from app.integrations.upload_post_client import (
            FOREIGN_ORGANISATIONS,
            PAGE_TARGETED_PLATFORMS,
            UploadPost,
            page_targets,
        )

        client = UploadPost(settings)
        configured = page_targets(session)
        lines, problems = [], []
        for platform in PAGE_TARGETED_PLATFORMS:
            want = str(configured.get(platform) or "").strip()
            pages = client.list_pages(platform)
            catalogue = {str(p.get("id") or p.get("urn") or ""): str(p.get("name") or "")
                         for p in pages}
            if not want:
                problems.append(f"{platform}: NO TARGET CONFIGURED — publish will refuse")
                continue
            if want not in catalogue:
                problems.append(
                    f"{platform}: configured target {want!r} is NOT in the live listing "
                    f"{sorted(catalogue)} — refuse and report; never fall back to personal")
                continue
            lines.append(f"{platform} → {catalogue[want]!r} ({want})")
            # Name what is being chosen BETWEEN, not merely what was chosen. The operator
            # cannot audit a decision whose alternatives are invisible.
            others = {k: v for k, v in catalogue.items() if k != want}
            for other_id, other_name in others.items():
                flag = " ** DIFFERENT BUSINESS **" if other_id in FOREIGN_ORGANISATIONS else ""
                lines.append(f"    also administered, NOT used: {other_name!r} "
                             f"({other_id}){flag}")
        if problems:
            raise RuntimeError("; ".join(problems))
        return " | ".join(lines)
    checks.append(_check("page targeting — where posts ACTUALLY land", _page_targets,
                         "seed config.facebook_page_id / config.linkedin_organization_urn "
                         "with ids from the live listing; publish REFUSES without them"))

    # --- Brevo -------------------------------------------------------------------------
    def _brevo():
        from app.integrations.brevo_client import _PLACEHOLDER, SIX_VARIABLES, Brevo
        b = Brevo(settings)
        html = b.get_template_html()
        present = {m.group(1) for m in _PLACEHOLDER.finditer(html)}
        missing = [v for v in SIX_VARIABLES if v not in present]
        if missing:
            raise RuntimeError(f"template {settings.BREVO_TEMPLATE_ID} missing variables {missing}")
        n = b.get_list_count()
        return f"template ok, list {settings.BREVO_LIST_ID} has {n} contacts"
    checks.append(_check("brevo key + list + template", _brevo,
                         "restore the six {{variables}} in the Brevo template / check BREVO_* env vars"))

    # --- Telegram ----------------------------------------------------------------------
    # TWO CHECKS ABOUT ONE TOKEN, AND THEY CONTRADICTED EACH OTHER.
    #
    # `telegram ownership` above encodes D1: on a bespoke service the token must NOT exist,
    # because the brain is the sole Telegram owner and two pollers on one token is a 409
    # that breaks the live gate. This check called `get_me()` unconditionally. So after the
    # D1 post-deploy step deleted the token from artec api and artec-scheduler — doing
    # exactly what the other check demands — this one began failing forever:
    #
    #     DOCTOR RED — telegram bot: TelegramError: telegram getMe failed: Not Found
    #
    # On the same service, one of the two had to be RED whatever the operator did. A pair of
    # checks that cannot both pass is not two guards, it is one guard and one false alarm,
    # and a permanent false RED is how a real RED stops being read.
    #
    # The RED dated 2026-08-03 PREDATES the deletion, so at that time it meant something
    # else — the token on that service was genuinely wrong. Both readings are real; they
    # just are not the same finding, which is why this now says which service it is on.
    if settings.HERMES_HOME:
        def _telegram():
            from app.integrations.telegram_client import Telegram
            me = Telegram(settings).get_me()
            return f"@{me.get('username', '?')}"
        checks.append(_check("telegram bot", _telegram,
                             "check TELEGRAM_BOT_TOKEN (no spaces!) and TELEGRAM_CHAT_ID "
                             "(numeric, via getUpdates)"))
    else:
        checks.append(Check(
            "telegram bot", True,
            "not checked here — this is a bespoke service and D1 requires it to hold NO "
            "Telegram token. `telegram ownership` above is the check that applies; probing "
            "getMe from here could only ever fail, and would fail for the wrong reason.",
            warn=False))

    # --- Stripe ------------------------------------------------------------------------
    def _stripe():
        resp = httpx.get("https://api.stripe.com/v1/account",
                         auth=(settings.STRIPE_SECRET_KEY, ""), timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return "key ok"
    checks.append(_check("stripe key", _stripe, "check STRIPE_SECRET_KEY"))

    # --- Billplz -----------------------------------------------------------------------
    def _billplz():
        resp = httpx.get(
            f"https://www.billplz.com/api/v3/collections/{settings.BILLPLZ_COLLECTION_ID}",
            auth=(settings.BILLPLZ_API_KEY, ""), timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return "collection ok"
    checks.append(_check("billplz key + collection", _billplz,
                         "check BILLPLZ_API_KEY and BILLPLZ_COLLECTION_ID"))

    # --- Drive: auth, taxonomy folders, _generated write probe -------------------------
    # Two modes: Shared Drive (GOOGLE_SHARED_DRIVE_ID set) or My Drive (empty — personal
    # Gmail, bank folder shared directly with the service account as Editor).
    def _drive():
        from app.integrations.drive_client import DriveClient, DriveError, _http_status
        drive = DriveClient(settings)
        mode = "My Drive mode" if drive.my_drive_mode else "Shared Drive"
        children = drive.list_children(drive.root_id)
        folder_names = {c["name"] for c in children
                        if c["mimeType"] == "application/vnd.google-apps.folder"}
        expected_top = {f.split("/")[0] for f in EXPECTED_FOLDERS}
        missing = sorted(expected_top - folder_names)
        if missing:
            raise RuntimeError(f"taxonomy folders missing at bank root: {missing}")
        # _generated/ is checked by name BEFORE the write probe so a missing folder is
        # reported as exactly that, never as a downstream 404.
        if GENERATED_FOLDER not in folder_names:
            raise RuntimeError(
                f"'{GENERATED_FOLDER}/' does not exist at the bank root "
                f"({settings.GOOGLE_DRIVE_ROOT_FOLDER_ID}) — create it in Drive; the write "
                "probe was not attempted"
            )
        for parent in ("raw-photo", "raw-video"):
            pid = next(c["id"] for c in children if c["name"] == parent)
            subs = {c["name"] for c in drive.list_children(pid)
                    if c["mimeType"] == "application/vnd.google-apps.folder"}
            missing_subs = sorted({"parent-child", "child-face", "assembled"} - subs)
            if missing_subs:
                raise RuntimeError(f"{parent}/ missing subfolders: {missing_subs}")
        try:
            probe_note = drive.probe_write()
        except DriveError:
            raise
        except Exception as e:
            msg = str(e).lower()
            status = _http_status(e)
            if "quota" in msg or "storagequota" in msg:
                raise RuntimeError(
                    "write probe hit a storage-quota error: files uploaded by the service "
                    "account count against the SERVICE ACCOUNT'S own quota in My Drive mode "
                    "(service accounts have zero) — use a Workspace Shared Drive, or free "
                    "service-account-owned files in _generated/"
                ) from e
            if status == 403:
                raise RuntimeError(
                    "write probe PERMISSION DENIED (403): the service account can read but "
                    "not write — it needs Content Manager on the Shared Drive (Editor on a "
                    "My Drive folder)"
                ) from e
            if status == 404:
                raise RuntimeError(
                    "write probe FILE NOT FOUND (404): the id being written under no longer "
                    "exists — usually a stale Drive id from before a bank migration. Verify "
                    "GOOGLE_DRIVE_ROOT_FOLDER_ID and GOOGLE_SHARED_DRIVE_ID are the "
                    "post-migration values, then run `artec assets sync --full` (a root "
                    "change auto-resets the sync cursor)"
                ) from e
            raise
        return f"auth + 11 folders ok · {probe_note} ({mode})"
    checks.append(_check("google drive bank", _drive,
                         "share the bank folder with the service account as Editor (My Drive "
                         "mode) or add it as Content Manager (Shared Drive); build the folder "
                         "tree per docs/ASSET_BANK.md; check GOOGLE_* env vars"))

    # --- config-level warnings ---------------------------------------------------------
    if session is not None:
        from app.config import get_config
        seeds = get_config(session, "seo_seeds", [])
        checks.append(Check("seo seeds", bool(seeds and len(seeds) >= 5),
                            f"{len(seeds or [])} seeds",
                            "set 5–15 seeds before the first ideate: artec config set seo_seeds '[…]'",
                            warn=True))

    return checks


def print_checks(checks: list[Check], log=print) -> bool:
    ok = True
    for c in checks:
        if c.ok:
            log(f"  GREEN  {c.name:<34} {c.detail}")
        elif c.warn:
            log(f"  YELLOW {c.name:<34} {c.detail}\n         remedy: {c.remedy}")
        else:
            ok = False
            log(f"  RED    {c.name:<34} {c.detail}\n         remedy: {c.remedy}")
    return ok
