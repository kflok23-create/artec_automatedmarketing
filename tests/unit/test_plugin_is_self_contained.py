"""THE BRAIN IMAGE DOES NOT CARRY THE APP PACKAGE.

`plugins/artec/tools.py` states the invariant in its own docstring: "Self-contained on
purpose: sqlalchemy textual SQL only, never the artec app package." The Dockerfile copies
`plugins/artec` onto the volume and nothing else; `app/` is never installed there.

I broke it, and it passed every test:

    WARNING agent.tool_executor: Tool read_digest returned error (0.25s):
    {"ok": false, "error": "ModuleNotFoundError: No module named 'app'"}

`read_digest` is job 12's ONLY tool, so the digest could not be delivered at all — on the
day the digest was the thing being watched.

NOTHING LOCAL COULD HAVE CAUGHT IT. The test suite runs from the repo root where `app` is
importable, so the import resolved everywhere except the one place that matters. Tested one
way, deployed another — the failure class this build names first and keeps re-meeting.

This test is the mechanical version, and it is deliberately blunt: any `app.` import
anywhere under `plugins/` fails, by name. A convention in a docstring is a comment; this is
the guard. (Decision 95: a warning in a comment is not a guard.)
"""

from __future__ import annotations

import ast


def _app_imports(path) -> list[str]:
    """Every `import app…` / `from app… import …`, at module level OR inside a function.

    Function-level matters more, not less: that is where mine was, and a lazy import looks
    harmless precisely because it does not fire until the tool is called — in production,
    in front of the operator.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name == "app" or a.name.startswith("app.")]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "app" or mod.startswith("app."):
                found.append(f"{mod} (line {node.lineno})")
    return found


def test_no_plugin_file_imports_the_app_package(repo_root):
    offenders = {}
    for path in sorted((repo_root / "plugins").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = _app_imports(path)
        if hits:
            offenders[str(path.relative_to(repo_root))] = hits
    assert not offenders, (
        f"plugin files importing the app package: {offenders}. The brain image copies "
        "plugins/artec onto the volume and NOTHING ELSE — `app` does not exist there. This "
        "resolves in tests and raises ModuleNotFoundError in production, on whichever tool "
        "the operator calls first."
    )


def test_no_brain_bootstrap_script_imports_the_app_package(repo_root):
    """THE SAME IMAGE, THE SAME MISSING PACKAGE, A WORSE FAILURE MODE.

    The guard above covered `plugins/` and stopped there, while seven scripts under
    `deploy/hermes-brain/` are COPYed into the very same image and run by the entrypoint —
    audit_memory_report, probe_scouting, purge_memory_claims, report_board, report_volume,
    prove_brain. `app` does not exist for them either.

    And here it is worse than it was for read_digest. Those scripts run at BOOT, every one of
    them behind `|| echo WARN`, so a ModuleNotFoundError would not raise, would not crash,
    and would not even read as an error — it would look like the WARN line that has been
    scrolling past since the first deploy. The digest would go on reporting a memory audit
    and a scouting probe for scripts that died on their import line.

    The scope of a guard is part of the guard. This one was correct about half the code it
    needed to cover, which is the disconnected-guard shape wearing a passing test.
    """
    offenders = {}
    for path in sorted((repo_root / "deploy" / "hermes-brain").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = _app_imports(path)
        if hits:
            offenders[str(path.relative_to(repo_root))] = hits
    assert not offenders, (
        f"brain bootstrap scripts importing the app package: {offenders}. These are COPYed "
        "into the hermes-brain image, which carries hermes-agent and NOTHING of `app`. Every "
        "one is invoked as `python /bootstrap/<name>.py || echo WARN`, so this fails SILENTLY "
        "at boot and the digest keeps reporting results for a script that never ran."
    )


def test_the_two_digest_date_implementations_agree(repo_root):
    """The duplication is only safe while it is a duplication.

    `app.digest_dates.digest_date_for` and the plugin's copy must return the same date for
    the same instant. Same remedy as `audit_memory_report.py`'s duplicated patterns: copy
    the code, assert the equality, never import across the boundary.
    """
    from datetime import UTC, datetime

    import plugins.artec.tools  # noqa: F401 — import through the package (known cycle)
    from app.digest_dates import digest_date_for as app_impl
    from plugins.artec.tools_v4 import digest_date_for as plugin_impl

    for instant in (
        datetime(2026, 8, 4, 12, 55, tzinfo=UTC),   # 20:55 SGT — job 11
        datetime(2026, 8, 4, 13, 0, tzinfo=UTC),    # 21:00 SGT — job 12
        datetime(2026, 8, 4, 16, 30, tzinfo=UTC),   # 00:30 SGT the 5th — UTC says the 4th
        datetime(2026, 8, 4, 0, 30, tzinfo=UTC),
        datetime(2026, 12, 31, 16, 30, tzinfo=UTC),  # year boundary, SGT ahead
    ):
        assert app_impl(instant) == plugin_impl(instant), (
            f"the two digest_date_for implementations disagree at {instant}. The write key "
            "and the read key have diverged again — see DECISIONS 99.")
