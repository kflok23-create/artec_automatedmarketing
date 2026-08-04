"""A′ — the bearer, asserted OVER HTTP against the real app.

The dependency was already correct in isolation and a production `POST /commands/doctor`
still returned 401 against a token verified byte-identical at four layers. A test of the
dependency would have kept agreeing with it, so this drives the whole stack: routing,
middleware, router-level dependency, and the route.

It also pins the two things that made the production 401 ambiguous:
  * GET on a POST-only command returns 405 — routing matched, so the path is right
  * a 401 now leaves a server-side line naming WHICH failure it was, with fingerprints
    rather than values
"""

import hashlib
import logging

import pytest
from fastapi.testclient import TestClient

from app.settings import get_settings

TOKEN = "test-token"          # tests/conftest.py sets HERMES_API_TOKEN to this


@pytest.fixture
def client():
    from app.main import app

    get_settings.cache_clear()
    return TestClient(app, raise_server_exceptions=False)


def test_the_correct_bearer_is_accepted(client):
    """Accepted = it gets PAST auth. The body may then fail for its own reasons; what this
    asserts is that the token is not what stopped it."""
    response = client.post("/commands/wishlist-match",
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code != 401, response.text


def test_a_wrong_bearer_is_rejected(client):
    response = client.post("/commands/wishlist-match",
                           headers={"Authorization": "Bearer " + "b" * 64})
    assert response.status_code == 401


def test_a_missing_header_is_rejected(client):
    assert client.post("/commands/wishlist-match").status_code == 401


def test_a_bare_token_without_the_bearer_prefix_still_works(client):
    """`removeprefix` is a no-op without the prefix, so a bare token is accepted. Asserted
    so the behaviour is deliberate rather than incidental."""
    assert client.post("/commands/wishlist-match",
                       headers={"Authorization": TOKEN}).status_code != 401


def test_surrounding_whitespace_is_tolerated(client):
    """A shell that appends a newline must not read as a wrong token."""
    assert client.post("/commands/wishlist-match",
                       headers={"Authorization": f"Bearer {TOKEN} "}).status_code != 401


def test_get_on_a_post_only_command_is_405_not_401(client):
    """405 comes from routing, BEFORE dependencies run — which is why a 405 on GET and a
    401 on POST are consistent with each other and prove the path is correct."""
    assert client.get("/commands/doctor").status_code == 405


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").status_code == 200


def test_every_commands_route_is_behind_the_token(client):
    """Router-level dependency, asserted route by route: a route added without auth is a
    hole that no single test of the dependency would ever see."""
    from app.main import app

    # The OpenAPI surface is the honest list: it is what the app says it serves.
    paths = {p for p in app.openapi()["paths"] if p.startswith("/commands")}
    assert paths, "no /commands routes found — the router did not load"
    for path in sorted(paths):
        for method in ("post", "get"):
            response = getattr(client, method)(path.replace("{post_id}", "post_1"))
            assert response.status_code in (401, 405), \
                f"{method.upper()} {path} answered {response.status_code} with no token"


def test_a_401_names_which_failure_it_was_without_leaking_the_token(client, caplog):
    with caplog.at_level(logging.WARNING, logger="artec.auth"):
        client.post("/commands/wishlist-match",
                    headers={"Authorization": "Bearer " + "b" * 64})
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "value mismatch" in line
    assert "provided_sha8=" in line and "expected_sha8=" in line
    assert "b" * 64 not in line and TOKEN not in line
    # the fingerprint is the same comparison the operator made across layers by hand
    assert hashlib.sha256(TOKEN.encode()).hexdigest()[:8] in line


def test_the_response_body_tells_an_unauthenticated_caller_nothing(client):
    response = client.post("/commands/wishlist-match",
                           headers={"Authorization": "Bearer wrong"})
    body = response.json()
    assert body == {"detail": "invalid or missing bearer token"}
    assert "sha" not in str(body) and "len" not in str(body)


def test_doctor_over_http_does_not_die_on_the_price_table(tmp_path, monkeypatch):
    """The live bug this probe surfaced: doctor read `OPERATOR_CONSTANTS
    ['endpoint_prices_cents']`, which v4 deleted, so every call raised KeyError. A doctor
    that cannot run is a green light that never lights.

    Driven against a REAL schema, because doctor's whole job is to talk to the database.
    """
    from sqlalchemy import create_engine, text

    import app.db as app_db
    from app.models import V_BRIEF_SQL, Base

    url = f"sqlite:///{tmp_path / 'doctor.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(V_BRIEF_SQL))
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    app_db._engine = None
    app_db._session_factory = None

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/commands/doctor", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    names = [c["name"] for c in response.json()["checks"]]
    assert "endpoint price table" in names
    price = next(c for c in response.json()["checks"] if c["name"] == "endpoint price table")
    assert price["status"] in ("GREEN", "RED", "YELLOW")
