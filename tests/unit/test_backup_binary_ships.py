"""There has been no backup, and nothing said so — then the fix broke the build.

    2026-08-07T19:00:09Z  job 8 pg-dump FAILED: BackupError: pg_dump is not on PATH in this
                          process — the nightly dump cannot run.

Every night, in a deploy log nobody was reading. `run_backup` was right and refused
correctly; the doctor checked `ffmpeg on PATH` and `ffprobe on PATH` and never looked for the
one binary the backup needs. Nothing could have implied it: psycopg speaks the wire protocol
and needs no client binaries, so `pyproject.toml` cannot express this dependency — it belongs
to the IMAGE.

THEN THE FIRST FIX FAILED BOTH NIXPACKS BUILDS:

    error: undefined variable 'postgresql_18'
      at /app/.nixpacks/nixpkgs-bc8f8d1be58e8c8383e683a06e1e1e57893fff87.nix:19:16

Railway pins nixpkgs to one commit, and that commit predates PostgreSQL 18. A package name
was shipped without verifying it existed in the pin, and artec api and artec-scheduler both
stopped building — stranding every other queued fix behind a broken build.

THE VERSION IS NOT NEGOTIABLE. The server is postgres-ssl:18 and pg_dump refuses a server
newer than itself, so an older `postgresql` from the pin would install cleanly, satisfy every
presence check, and abort nightly on "server version mismatch". The client therefore comes
from PGDG, in the install phase, guarded so it can never fail the build again.
"""

from __future__ import annotations

import re
import tomllib

SERVER_MAJOR = 18          # ghcr.io/railwayapp-templates/postgres-ssl:18
INSTALL_SCRIPT = "scripts/install_pg_client.sh"

# The ONLY nix packages this build may name. Anything added here must be verified to exist in
# Railway's pinned nixpkgs, which is the check that was skipped.
KNOWN_GOOD_NIXPKGS = {"python312", "ffmpeg"}


def _nixpacks(repo_root) -> dict:
    with open(repo_root / "nixpacks.toml", "rb") as fh:
        return tomllib.load(fh)


def _install_cmds(repo_root) -> list[str]:
    return _nixpacks(repo_root)["phases"]["install"]["cmds"]


def test_a_postgres_client_ships_in_the_image(repo_root):
    """The image must carry pg_dump. This is the assertion whose absence cost every nightly
    backup since job 8 was registered."""
    joined = " ".join(_install_cmds(repo_root))
    assert INSTALL_SCRIPT in joined, (
        f"nothing in the install phase runs {INSTALL_SCRIPT} — job 8 is the only backup this "
        "system has, and pg_dump is not a Python dependency, so nothing else can supply it")
    assert (repo_root / INSTALL_SCRIPT).is_file(), f"{INSTALL_SCRIPT} is referenced but absent"


def test_the_client_is_not_older_than_the_server(repo_root):
    """An older client installs cleanly, passes every presence check, and aborts nightly on
    "server version mismatch" — a green build and no backup."""
    script = (repo_root / INSTALL_SCRIPT).read_text(encoding="utf-8")
    pinned = re.findall(r"postgresql-client-(\d+)", script)
    assert pinned, (
        "the install script does not pin a postgresql-client major version. Railway's server "
        f"is {SERVER_MAJOR} and pg_dump refuses a server newer than itself, so an unpinned "
        "client is a backup that silently does not happen.")
    for major in pinned:
        assert int(major) >= SERVER_MAJOR, (
            f"postgresql-client-{major} is older than the server ({SERVER_MAJOR})")


def test_the_client_install_cannot_break_the_build(repo_root):
    """THE REGRESSION THAT COST TWO SERVICES THEIR BUILD.

    Normally `|| echo WARN` is the pattern this project treats as a defect — it is exactly how
    three missing files reached production silently. It is justified HERE and nowhere else
    because something that is not this script reports the failure: `artec doctor` compares
    pg_dump's major against the LIVE server's major, daily, and its RED lines ride the digest.

    A broken build strands every other fix behind it, which is what actually happened.
    """
    pg_cmd = next(c for c in _install_cmds(repo_root) if INSTALL_SCRIPT in c)
    assert "||" in pg_cmd, (
        "the pg client install is unguarded. If it fails, the whole image fails to build and "
        "every queued fix is stranded — the doctor is what reports a missing pg_dump, not the "
        "build.")


def test_nix_packages_stay_to_the_verified_set(repo_root):
    """`postgresql_18` was added to nixPkgs without checking it existed in Railway's pinned
    nixpkgs, and both nixpacks services failed to build. Adding a nix package is therefore a
    deliberate act that has to be made here first, with the pin verified."""
    pkgs = set(_nixpacks(repo_root)["phases"]["setup"]["nixPkgs"])
    assert pkgs == KNOWN_GOOD_NIXPKGS, (
        f"nixPkgs is {sorted(pkgs)}, expected {sorted(KNOWN_GOOD_NIXPKGS)}. Every name here "
        "must exist in the nixpkgs commit Railway pins — an undefined variable fails the "
        "build for artec api AND artec-scheduler at once.")


def test_ffmpeg_did_not_get_dropped_while_fixing_this(repo_root):
    """The visual toolbox and the video pre-flight both need it, and `video-pipeline` is one
    of the nine capabilities. Editing this file is how it would go."""
    assert "ffmpeg" in _nixpacks(repo_root)["phases"]["setup"]["nixPkgs"]


def test_the_app_is_still_installed_after_the_client(repo_root):
    """Adding a command to a phase is how the command that matters gets dropped."""
    joined = " ".join(_install_cmds(repo_root))
    assert "pip install ." in joined


def test_the_doctor_checks_the_binary_the_backup_needs(repo_root):
    """The check and the packaging must move together, and here the doctor carries more
    weight than usual: it is the ONLY thing that reports a failed install, because the install
    is deliberately non-fatal."""
    source = (repo_root / "app" / "stages" / "doctor.py").read_text(encoding="utf-8")
    assert "pg_dump" in source, (
        "doctor.py checks ffmpeg and ffprobe but not pg_dump. Job 8 failed nightly with no "
        "RED line, because nothing here looked for it.")
    assert "server_version" in source, (
        "the doctor must compare pg_dump's major against the SERVER's — presence alone stays "
        "green through a version mismatch, which aborts the dump just as completely")
