"""`hermes doctor` — green/red verification of every dependency, with a named remedy per
red line. Exits non-zero on any red. Includes the live LoRA base-model probe (§7.2) and the
Drive `_generated/` write probe.
"""

from __future__ import annotations

import shutil
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

    # --- fal + live LoRA probes --------------------------------------------------------
    def _lora_probe(key: str):
        from app.integrations.fal_client import Fal
        fal = Fal(settings)
        endpoint, args = probe_request(OPERATOR_CONSTANTS["loras"][key],
                                       OPERATOR_CONSTANTS["image_endpoints"]["lora"])
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
    checks.append(_check("fal LoRA probe: assembled", lambda: _lora_probe("assembled"),
                         "verify FAL_KEY; if a base-model mismatch is named, retrain the LoRA on fal-ai/qwen-image-2512-trainer"))
    checks.append(_check("fal LoRA probe: unassembled", lambda: _lora_probe("unassembled"),
                         "verify FAL_KEY; if a base-model mismatch is named, retrain the LoRA on fal-ai/qwen-image-2512-trainer"))
    vf = OPERATOR_CONSTANTS["video_family"]
    checks.append(Check("video family verified", bool(vf.get("verified")),
                        f"{vf['name']} ({vf['reference_to_video']})",
                        "endpoint slugs are operator-supplied and unverified — confirm on the fal model page and update config.video_family",
                        warn=True))

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
    def _telegram():
        from app.integrations.telegram_client import Telegram
        me = Telegram(settings).get_me()
        return f"@{me.get('username', '?')}"
    checks.append(_check("telegram bot", _telegram,
                         "check TELEGRAM_BOT_TOKEN (no spaces!) and TELEGRAM_CHAT_ID (numeric, via getUpdates)"))

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
                    "post-migration values, then run `hermes assets sync --full` (a root "
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
                            "set 5–15 seeds before the first ideate: hermes config set seo_seeds '[…]'",
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
