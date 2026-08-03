"""A5 — probe the search backend against its REAL endpoint at brain boot, and record the
answer where the digest can read it.

Presence is not validity. This project has already lost a cycle to a key that passed a
presence check and 401'd at first use, which is why the Anthropic probe in the entrypoint
calls the real API. Tavily gets the same treatment.

Failure does NOT fail the boot: scouting is the cheapest thing in the system to lose, and a
brain that refuses to start because a search key expired would trade a whole week of
planning for a trend feed. It logs SCOUTING UNAVAILABLE with the reason, writes that reason
to `config.scouting_status`, and the digest reports it every night until it is fixed.

The key is read from the environment and never printed — only the HTTP status and the
backend's own error text, which carries no secret.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
PROBE_QUERY = "artec blocks educational toys"


def probe_tavily(api_key: str, *, client: httpx.Client | None = None) -> dict:
    """Call the real search endpoint. Returns a status dict; never raises."""
    if not (api_key or "").strip():
        return {"available": False, "backend": "tavily",
                "reason": "TAVILY_API_KEY is not set on this service"}
    post = (client.post if client else httpx.post)
    try:
        resp = post(
            TAVILY_SEARCH_URL,
            json={"api_key": api_key.strip(), "query": PROBE_QUERY, "max_results": 1},
            timeout=30,
        )
    except Exception as e:
        return {"available": False, "backend": "tavily",
                "reason": f"{type(e).__name__}: {e}"}
    if resp.status_code != 200:
        # The body is the backend's own error text — no secret, and the only thing that
        # distinguishes "wrong key" from "out of credit" from "endpoint moved".
        return {"available": False, "backend": "tavily",
                "reason": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    try:
        results = resp.json().get("results") or []
    except ValueError:
        return {"available": False, "backend": "tavily",
                "reason": "HTTP 200 but the body did not parse as JSON"}
    if not results:
        # A 200 with nothing in it is not a working search backend.
        return {"available": False, "backend": "tavily",
                "reason": "HTTP 200 but zero results for the probe query"}
    return {"available": True, "backend": "tavily",
            "reason": f"HTTP 200, {len(results)} result(s) for the probe query"}


def _write_status(status: dict, url: str) -> None:
    from sqlalchemy import bindparam, create_engine, text
    from sqlalchemy.types import JSON as SAJSON

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO config (key, value, updated_at) VALUES "
                 "('scouting_status', :v, :t) "
                 "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t")
            .bindparams(bindparam("v", type_=SAJSON())),
            {"v": status, "t": datetime.now(UTC)},
        )


def main() -> int:
    status = probe_tavily(os.environ.get("TAVILY_API_KEY", ""))
    status["probed_at"] = datetime.now(UTC).isoformat()
    if status["available"]:
        print(f"scouting: AVAILABLE via {status['backend']} — {status['reason']}")
    else:
        print(f"SCOUTING UNAVAILABLE ({status['backend']}): {status['reason']} — "
              "planning continues without it and the digest reports this nightly")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("probe_scouting: DATABASE_URL unset — status not recorded", file=sys.stderr)
        return 0
    try:
        _write_status(status, url)
    except Exception as e:
        print(f"probe_scouting: could not record status: {type(e).__name__}: {e}",
              file=sys.stderr)
    return 0            # never fails the boot


if __name__ == "__main__":
    sys.exit(main())
