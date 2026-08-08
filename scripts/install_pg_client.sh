#!/bin/sh
# pg_dump 18, from PGDG, because Railway's nixpkgs pin does not have it.
#
# `nixPkgs = [..., "postgresql_18"]` FAILED THE BUILD of artec api and artec-scheduler:
#
#   error: undefined variable 'postgresql_18'
#     at /app/.nixpacks/nixpkgs-bc8f8d1be58e8c8383e683a06e1e1e57893fff87.nix:19:16
#
# Railway pins nixpkgs to one commit, and that commit predates PostgreSQL 18. The version is
# not negotiable — the server is ghcr.io/railwayapp-templates/postgres-ssl:18 and pg_dump
# refuses a server newer than itself — so an older `postgresql` from the pin would install
# cleanly, satisfy every presence check, and abort every night on "server version mismatch".
# PGDG is the only source that reliably has 18 for Debian.
#
# THIS SCRIPT MUST NEVER FAIL THE BUILD. It is invoked with `|| echo WARN` from
# nixpacks.toml, and that is normally the pattern this project treats as a defect —
# `|| echo WARN` is exactly how three missing files reached production silently. The
# difference is what happens next: `artec doctor` compares pg_dump's major against the LIVE
# server's major, daily, and its RED lines ride the nightly digest. So a failed install here
# is reported by something that is not this script. The build staying up is worth more,
# because a broken build strands EVERY other fix behind it — which is precisely what
# happened when this dependency was expressed as a nix package.
set -eu

echo "pg-client: installing postgresql-client-18 from PGDG"
. /etc/os-release

apt-get update
apt-get install -y --no-install-recommends curl ca-certificates

install -d /usr/share/postgresql-common/pgdg
curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc]" \
     "https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
     > /etc/apt/sources.list.d/pgdg.list

apt-get update
apt-get install -y --no-install-recommends postgresql-client-18
rm -rf /var/lib/apt/lists/*

# PROVE IT, HERE, rather than trusting the exit code of an install. Same reason the brain
# entrypoint verifies cron by LISTING: `apt-get install` succeeding is not evidence that the
# binary is on PATH for the process that will need it.
pg_dump --version
