"""Report the hermes-brain volume marker to Postgres so `artec doctor` (which runs on
artec-api, with no HERMES_HOME) can verify the volume for real instead of skipping.

Runs in the brain's entrypoint on every boot:
- reads/writes $HERMES_HOME/.artec-volume-marker (its timestamp = when the volume was
  first seen; surviving into a later boot proves persistence across redeploys)
- upserts config row 'hermes_brain_volume' = {marker_since, boot_at, hermes_home}

doctor's verdict: marker_since < boot_at → SURVIVED (green); equal → first boot (yellow,
redeploy to prove); row absent → RED (the brain never reported).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.types import JSON as SAJSON


def main() -> int:
    home = os.environ.get("HERMES_HOME", "")
    url = os.environ.get("DATABASE_URL", "")
    if not home or not url:
        print("report_volume: HERMES_HOME or DATABASE_URL unset", file=sys.stderr)
        return 1
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    now = datetime.now(UTC).isoformat()
    marker = Path(home) / ".artec-volume-marker"
    if marker.exists():
        since = marker.read_text(encoding="utf-8").strip()
    else:
        marker.write_text(now, encoding="utf-8")
        since = now

    payload = {"marker_since": since, "boot_at": now, "hermes_home": home}
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO config (key, value, updated_at) VALUES "
                 "('hermes_brain_volume', :v, :t) "
                 "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t")
            .bindparams(bindparam("v", type_=SAJSON())),
            {"v": payload, "t": datetime.now(UTC)},
        )
    survived = "SURVIVED prior boot(s)" if since < now else "first boot"
    print(f"report_volume: marker since {since}, boot {now} — {survived}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
