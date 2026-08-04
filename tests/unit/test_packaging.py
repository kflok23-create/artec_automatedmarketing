"""Runtime-resource packaging — guards the whole class of 'works from the repo root,
vanishes under pip install .' bugs (hit twice in production: fonts at doctor, prompts at
ideate). Every runtime resource must live INSIDE the `app` package; the wheel-artifact
check (tests/wheel_contents_check.py, run in CI after `uv build`) inspects the actual
built wheel."""

import re
from pathlib import Path

import app
from app.config import OPERATOR_CONSTANTS
from app.db import script_head_revision
from app.integrations.anthropic_client import PROMPTS_DIR, render_prompt
from app.toolbox.text_card import FONTS_DIR

PKG_ROOT = Path(app.__file__).resolve().parent

PROMPT_FILES = ("learn_v1.md", "ideate_v1.md", "toolbox_route_v1.md", "caption_v1.md",
                "wishlist_v1.md")


def test_prompts_live_inside_the_app_package():
    assert PROMPTS_DIR == PKG_ROOT / "prompts", "PROMPTS_DIR must be package-relative"
    missing = [f for f in PROMPT_FILES if not (PROMPTS_DIR / f).exists()]
    assert not missing, f"prompt files missing from the package: {missing}"


def test_render_prompt_resolves_and_substitutes():
    text = render_prompt("ideate_v1.md", {"week_start": "2026-08-03", "cadence": {"instagram": 1},
                                          "seeds": ["s1"], "brief": "none"})
    assert "2026-08-03" in text
    assert "$week_start" not in text


def test_fonts_live_inside_the_app_package():
    assert FONTS_DIR == PKG_ROOT / "assets" / "fonts"
    missing = [f for f in set(OPERATOR_CONSTANTS["fonts"].values())
               if not (FONTS_DIR / f).exists()]
    assert not missing, f"fonts missing from the package: {missing}"


def test_migrations_live_inside_the_app_package():
    versions = PKG_ROOT / "migrations" / "versions"
    assert versions.is_dir(), "migrations must ship in the wheel for runtime head checks"
    assert (versions / "0001_initial.py").exists()


def test_script_head_resolves_without_cwd_or_alembic_ini(tmp_path, monkeypatch):
    # The runtime head check must not depend on the working directory (alembic.ini is a
    # repo-root file; the deployed process imports from site-packages).
    monkeypatch.chdir(tmp_path)
    # 0007 recreates v_brief. The PARKED fix had been in V_BRIEF_SQL for weeks and had
    # never reached production, because the view is created by 0001 and alembic never
    # re-runs it — see tests/unit/test_view_migrations.py.
    assert script_head_revision() == "0008"


REPO_ROOT_WALK = re.compile(r"parent\s*\.\s*parent\s*\.\s*parent")


def test_no_repo_root_path_walks_in_app_source():
    # Three parents from any module inside app/ escapes the installed package — the exact
    # smell that shipped both production packaging bugs. Zero tolerance.
    offenders = [str(p) for p in PKG_ROOT.rglob("*.py")
                 if REPO_ROOT_WALK.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"repo-root path walks found (resource will vanish when installed): {offenders}"
