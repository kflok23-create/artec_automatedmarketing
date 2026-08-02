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
