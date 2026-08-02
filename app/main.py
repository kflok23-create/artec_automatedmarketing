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
    from app.toolbox.text_card import FONTS_DIR

    database = db_ok()
    migrations = migration_current()
    # fonts_packaged verifies the INSTALLED package carries the .ttf files — this resolves
    # from site-packages at runtime, exactly where a wheel-packaging regression would show.
    fonts = all((FONTS_DIR / f).exists() for f in set(_oc["fonts"].values()))
    status = "ok" if database and migrations else "degraded"
    return {"status": status, "db": database, "migrations_current": migrations,
            "fonts_packaged": fonts}
