"""EVERY PATH THE ENTRYPOINT READS MUST BE IN THE IMAGE.

The first production deploy of `main` crashed. `deploy/hermes-brain/entrypoint.sh` reads
nine paths under /bootstrap; the Dockerfile COPYed six. All three missing files existed in
the repo and were correct — they were simply never shipped. Packaging/environment class,
the first of the three, and it survived a green CI run, a green test suite, and a merge.

Why nothing caught it:
  - the test suite never looks at the Dockerfile
  - CI builds nothing that runs the entrypoint
  - two of the three failures are behind `|| echo WARN`, so they are SILENT: the digest has
    been reporting a scouting probe and an agent-memory audit for scripts absent from the
    image. A line that says "NOT YET PROBED" reads like a pending task, not a missing file.
  - the third, cron-nightly-digest.txt, was caught only because `hermes cron create` exits 0
    on failure and the boot guard verifies by LISTING instead of trusting the exit code

This test is the shape that works: assert the structural property from OUTSIDE both files.
Neither the Dockerfile nor the entrypoint can satisfy it alone, and neither can drift
without the other noticing — which is exactly what the standing review question asks for.
It is the same shape as the test that caught job 2's missing dispatch.
"""

from __future__ import annotations

import re

ENTRYPOINT = "deploy/hermes-brain/entrypoint.sh"
DOCKERFILE = "deploy/hermes-brain/Dockerfile"

# Paths the entrypoint reads that the image is NOT expected to supply, with the reason.
# Anything not listed here must be COPYed. Keeping this explicit means a new runtime-created
# path has to be justified in writing rather than silently excused.
RUNTIME_CREATED = {
    "/bootstrap/plugins/artec",   # COPYed as a directory; matched separately below
}


def _bootstrap_paths_read_by(text: str) -> set[str]:
    """Every /bootstrap/... path the entrypoint mentions."""
    return set(re.findall(r"/bootstrap/[A-Za-z0-9_./-]+", text))


def test_every_bootstrap_path_the_entrypoint_reads_is_copied_into_the_image(repo_root):
    entrypoint = (repo_root / ENTRYPOINT).read_text(encoding="utf-8")
    dockerfile = (repo_root / DOCKERFILE).read_text(encoding="utf-8")

    copied = set(re.findall(r"^COPY\s+\S+\s+(/bootstrap/\S+)", dockerfile, re.MULTILINE))
    read = _bootstrap_paths_read_by(entrypoint)

    missing = set()
    for path in read:
        if path in copied or path in RUNTIME_CREATED:
            continue
        # A file inside a COPYed directory is covered by that directory.
        if any(path.startswith(d.rstrip("/") + "/") for d in copied | RUNTIME_CREATED):
            continue
        missing.add(path)

    assert not missing, (
        "the entrypoint reads paths the Dockerfile never COPYs into the image: "
        f"{sorted(missing)}. These files exist in the repo and are correct; they simply do "
        "not ship. Two of the three that caused the first production crash failed silently "
        "behind `|| echo WARN`, so the digest reported on scripts that were not there."
    )


def test_the_three_files_that_crashed_the_first_deploy_are_copied(repo_root):
    """Named explicitly, so a future edit that drops one fails with its history attached."""
    dockerfile = (repo_root / DOCKERFILE).read_text(encoding="utf-8")
    for name, consequence in (
        ("cron-nightly-digest.txt",
         "job 12 never registers; `hermes cron create` exits 0 on an empty prompt, so only "
         "the boot guard's listing check catches it — the service CRASHES"),
        ("probe_scouting.py",
         "the digest reports 'scouting: NOT YET PROBED' forever, which reads as pending "
         "rather than missing"),
        ("audit_memory_report.py",
         "the digest reports an agent-memory-audit line for a script that never ran"),
    ):
        assert f"/bootstrap/{name}" in dockerfile, (
            f"deploy/hermes-brain/{name} is not COPYed into the image — {consequence}")


def test_the_entrypoint_verifies_cron_by_listing_not_by_exit_code(repo_root):
    """`hermes cron create` EXITS 0 ON FAILURE (VERIFY.md V2, probed twice). An entrypoint
    that trusts its exit code registers nothing and reports success — and that is precisely
    how job 12's missing prompt file would have gone unnoticed."""
    entrypoint = (repo_root / ENTRYPOINT).read_text(encoding="utf-8")
    assert "hermes cron list" in entrypoint, (
        "the entrypoint must verify cron registration by LISTING; `hermes cron create` "
        "exits 0 even when it creates nothing")
