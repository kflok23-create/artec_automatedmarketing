"""There has been no backup, and nothing said so.

    2026-08-07T19:00:09Z  job 8 pg-dump FAILED: BackupError: pg_dump is not on PATH in this
                          process — the nightly dump cannot run.

Every night, in a deploy log nobody was reading. `run_backup` was right, refused correctly,
and said exactly what was wrong; the doctor — which checks `ffmpeg on PATH` and `ffprobe on
PATH` — never looked for the one binary the backup needs. Packaging/environment class again:
the code was correct and never shipped.

Nothing implied it. psycopg speaks the wire protocol and needs no client binaries, so
`pyproject.toml` cannot express this dependency; it belongs to the IMAGE. Build was green,
boot was green, and the only component that knew was the job that failed.

THE VERSION IS PART OF THE DEPENDENCY. The server is postgres-ssl:18 and pg_dump refuses a
server newer than itself, so "pg_dump exists" is not the property that matters — it is the
property that would have stayed green through the whole outage if we had checked it.
"""

from __future__ import annotations

import re
import tomllib

SERVER_MAJOR = 18          # ghcr.io/railwayapp-templates/postgres-ssl:18


def _nixpkgs(repo_root) -> list[str]:
    with open(repo_root / "nixpacks.toml", "rb") as fh:
        return tomllib.load(fh)["phases"]["setup"]["nixPkgs"]


def test_a_postgres_client_ships_in_the_image(repo_root):
    """The image must carry pg_dump. This is the assertion whose absence cost every nightly
    backup since job 8 was registered."""
    pkgs = _nixpkgs(repo_root)
    assert any(p.startswith("postgresql") for p in pkgs), (
        f"no postgresql package in nixPkgs {pkgs} — job 8 is the only backup this system "
        "has and pg_dump is not a Python dependency, so nothing else can supply it")


def test_the_client_is_not_older_than_the_server(repo_root):
    """`postgresql` unversioned, or any major below the server's, puts the binary on PATH and
    aborts every night on "server version mismatch" — green to every presence check."""
    pinned = [p for p in _nixpkgs(repo_root) if p.startswith("postgresql")]
    for pkg in pinned:
        found = re.search(r"(\d+)", pkg)
        assert found, (
            f"{pkg!r} pins no major version. Railway's server is {SERVER_MAJOR}; pg_dump "
            "refuses a server newer than itself, so an unversioned package is a backup that "
            "silently does not happen.")
        assert int(found.group(1)) >= SERVER_MAJOR, (
            f"{pkg!r} is older than the server ({SERVER_MAJOR}) — pg_dump would abort")


def test_ffmpeg_did_not_get_dropped_while_fixing_this(repo_root):
    """The visual toolbox and the video pre-flight both need it, and `video-pipeline` is one
    of the nine proven capabilities. Editing this file is how it would go."""
    assert "ffmpeg" in _nixpkgs(repo_root)


def test_the_doctor_checks_the_binary_the_backup_needs(repo_root):
    """The check and the packaging must move together. A remedy naming a package the build
    does not install, or an install with nothing watching it, is how this recurs — the
    disconnected-guard shape, in the file whose job is connecting them."""
    source = (repo_root / "app" / "stages" / "doctor.py").read_text(encoding="utf-8")
    assert "pg_dump" in source, (
        "doctor.py checks ffmpeg and ffprobe but not pg_dump. Job 8 failed nightly with no "
        "RED line, because nothing here looked for it.")
    assert "server_version" in source, (
        "the doctor must compare pg_dump's major against the SERVER's — presence alone stays "
        "green through a version mismatch, which aborts the dump just as completely")
