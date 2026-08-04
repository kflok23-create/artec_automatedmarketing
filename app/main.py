"""HERMES — the one Railway service. FastAPI app: capture endpoints + command mirrors.

Boot: settings validate fail-fast (naming the missing variable only), the secret redaction
filter installs globally, CORS restricts /event to the site origin. Migrations run in the
pre-deploy step, never at import.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_capture import router as capture_router
from app.api.routes_commands import router as commands_router
from app.config import OPERATOR_CONSTANTS
from app.settings import get_settings, install_redaction


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()  # raises MissingEnvVarError with names only
    logging.basicConfig(level=settings.LOG_LEVEL)
    install_redaction(settings)
    # THE MANIFEST WAS NEVER CHECKED AT BOOT. `validate_required_config`'s own docstring
    # says "Called at boot — refuse to start rather than discover it from a per-tick error
    # log three hours later", and NOTHING called it: the only references in the repo were
    # in tests. So `REQUIRED_CONFIG_KEYS` was a list that guaranteed nothing, and adding
    # the page-target keys to it would have guaranteed nothing either.
    #
    # This is the same shape as the branch-protection ruleset that existed and applied to
    # no branches, and the memory audit that scanned no files: a guard present in the
    # source and absent from the running system. Wired in here, where a missing key stops
    # the service instead of surfacing as a public post to the wrong page.
    from app.config import seed_config, validate_required_config
    from app.db import session_scope

    with session_scope() as session:
        # TOP UP FIRST, THEN VALIDATE. Wiring the validator in took this service down, and
        # it was right to: production was missing SIX required keys, four of them nothing
        # to do with page targeting —
        #   render_run_cap_cents, max_output_megapixels,
        #   email_review_expiry_days, video_review_expiry_days
        # The render SPEND CAP was absent from production config. Nothing failed loudly,
        # because every read passes a default: get_config(s, "render_run_cap_cents", 250).
        # A default is a fallback, not a setting, and from outside the two are identical.
        #
        # ROOT CAUSE: a key added to OPERATOR_CONSTANTS never reaches a database seeded
        # before that key existed. `artec config seed` had to be run by hand and nothing
        # ever said when. seed_config is NON-DESTRUCTIVE — it adds missing keys and keeps
        # any value the operator changed — so running it here makes a new constant arrive
        # with the deploy that introduced it, the one moment anyone knows it is needed.
        seed_config(session)
        keys = validate_required_config(session, "api")
        logging.info("config manifest validated at boot: %d keys", len(keys))
    yield


app = FastAPI(title="HERMES", docs_url=None, redoc_url=None, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[OPERATOR_CONSTANTS["site_base_url"]],
    allow_methods=["POST"],
    allow_headers=["content-type"],
)

app.include_router(capture_router)
app.include_router(commands_router)


@app.get("/healthz")
def healthz():
    from app.config import OPERATOR_CONSTANTS as _oc
    from app.db import db_ok, migration_current
    from app.integrations.anthropic_client import PROMPTS_DIR
    from app.toolbox.text_card import FONTS_DIR

    database = db_ok()
    migrations = migration_current()
    # resources_packaged verifies the INSTALLED package carries every runtime resource —
    # resolved from site-packages at runtime, exactly where a wheel regression would show
    # (fonts and prompts both reached production missing before this guard existed).
    resources = (
        all((FONTS_DIR / f).exists() for f in set(_oc["fonts"].values()))
        and all((PROMPTS_DIR / p).exists() for p in
                ("learn_v1.md", "ideate_v1.md", "toolbox_route_v1.md", "caption_v1.md", "wishlist_v1.md"))
    )
    status = "ok" if database and migrations else "degraded"
    return {"status": status, "db": database, "migrations_current": migrations,
            "resources_packaged": resources}
