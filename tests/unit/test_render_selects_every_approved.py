"""Render selected NOTHING over a board of six and answered HTTP 200.

    POST /commands/render {}  ->  {"rendered":0,"parked":0,"spent_cents":0}

Six APPROVED posts, four of them servicable, three days with no output — and the operator
ran the command directly and was still told nothing was wrong.

THE CAUSE IS NOT A WEEK FILTER. Render has never had a `week_start` predicate; the query is
`WHERE status == 'APPROVED'`. The discard happens in the Python comprehension after it:

    posts = [p for p in approved if all_approved or (post_ids and p.post_id in post_ids)]

`all_approved` defaults to False on the HTTPS route, so with no `post_id` the comprehension
keeps nothing. Job 6 passes `all_approved=True`; the CLI EXITS 2 rather than guess. Of the
three callers, the only one that could silently do nothing was the one a human types.

WHY WEEK-INDEPENDENCE IS ASSERTED ANYWAY: `week_start` is a planning bucket, not an execution
filter. Approved work legitimately spans weeks — post_1490 carries 2026-07-27 through a park
and an autonomous wishlist return, and the ledger is right to keep it. Any week predicate
added here later would strand exactly that post, so the property is pinned before anyone
adds one.

WHAT SUPPLIES EACH SIDE: the posts' weeks are written by the test across three planning
weeks; the selection comes from `render` itself. **`today` is pinned** — no clock is read, so
the test cannot pass merely because it ran on a convenient day (DECISIONS 112). If one
function supplied both the posts' week and the query's week, the comparison would be
trivially satisfiable and trivially breakable, which is the shape this file exists to refuse.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import Post
from app.stages.render import render

# Three planning weeks, mirroring production exactly: post_1490 is two weeks behind.
BOARD = (
    ("post_1490", "email", date(2026, 7, 27), "evening"),
    ("post_1491", "instagram", date(2026, 8, 3), "morning"),
    ("post_1492", "instagram", date(2026, 8, 3), "evening"),
    ("post_1497", "facebook", date(2026, 8, 3), "lunch"),
    ("post_new", "instagram", date(2026, 8, 10), "lunch"),
)


@pytest.fixture
def board(session):
    for post_id, channel, week, slot in BOARD:
        session.add(Post(post_id=post_id, channel=channel, week_start=week,
                         status="APPROVED", slot=slot, angle="a", hook="h",
                         cta_type="shop"))
    session.add(Post(post_id="post_draft", channel="instagram",
                     week_start=date(2026, 8, 10), status="DRAFT", slot="lunch"))
    session.flush()
    return session


class _Boom:
    """Render must SELECT these; it must not be able to render them in a unit test. Any
    attempt to touch Drive/fal/the LLM raises, so the test measures selection only."""

    def __getattr__(self, name):
        def _fail(*a, **kw):
            raise AssertionError(f"selection test touched {name}()")
        return _fail


def _select(session, **kwargs):
    """Run render far enough to learn what it selected, without rendering anything."""
    return render(session, _Boom(), _Boom(), _Boom(), log=lambda *a: None, **kwargs)


def test_all_approved_selects_every_approved_post_regardless_of_week(board):
    """N APPROVED posts in, N selected — across three planning weeks, no clock involved."""
    result = _select(board, all_approved=True)
    assert result["approved_total"] == len(BOARD)
    assert result["selected"] == len(BOARD), (
        "render did not select every APPROVED post. week_start is a planning bucket, not an "
        "execution filter — a week predicate here strands post_1490 permanently.")


def test_a_post_two_weeks_behind_is_still_selected(board):
    """THE REAL CASE. post_1490 was planned 2026-07-27, parked, and returned to APPROVED
    autonomously via the wishlist. Its week is a true fact about when it was planned."""
    result = _select(board, all_approved=True)
    assert result["approved_total"] == 5
    assert "post_1490" not in (result.get("approved_post_ids") or []), (
        "post_1490 landed in the unselected list")


def test_drafts_are_not_selected(board):
    """The exclusion that IS correct — a DRAFT has not been approved."""
    assert _select(board, all_approved=True)["approved_total"] == 5


def test_an_empty_selection_over_a_NON_EMPTY_board_is_a_named_fault(board):
    """THE DEFECT. Zero-and-200 is what cost three days."""
    result = _select(board, all_approved=False)
    assert result["selected"] == 0
    assert result["approved_total"] == 5
    assert result["fault"] == "empty_selection_over_a_non_empty_board"
    assert set(result["approved_post_ids"]) == {p[0] for p in BOARD}


def test_an_empty_board_is_NOT_a_fault(session):
    """Three states, never collapsed: nothing approved · approved-but-unselected · selected.
    Reporting the first as a fault would make the fault line noise."""
    result = _select(session, all_approved=True)
    assert result["approved_total"] == 0
    assert result["selected"] == 0
    assert "fault" not in result


def test_both_numbers_are_always_present(board):
    """A response that reports only outcomes cannot distinguish 'nothing to do' from
    'nothing attempted'. Both counts ship on every path."""
    for kwargs in ({"all_approved": True}, {"all_approved": False},
                   {"post_ids": ["post_1491"]}):
        result = _select(board, **kwargs)
        assert "approved_total" in result and "selected" in result


def test_render_has_no_week_predicate_in_its_SELECTION(repo_root):
    """Structural, so the property survives someone 'tidying' the query later.

    SCOPED TO THE SELECTION, not the whole function — and the first version of this test was
    not, which is why it failed. Render legitimately uses `week_start` further down as a
    DRIVE FOLDER: `_generated/{week}/{post_id}.{ext}`. That is the planning bucket being
    used as a planning bucket, and forbidding it wholesale would have been the same category
    error in the opposite direction.

    What must never appear is a week predicate in the statement that decides WHICH posts
    get rendered.
    """
    src = (repo_root / "app" / "stages" / "render.py").read_text(encoding="utf-8")
    body = src.split("def render(", 1)[1]
    start = body.index("approved = list(")
    selection = body[start:body.index("result_head", start)]
    code = "\n".join(ln for ln in selection.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "week_start" not in code, (
        "render's SELECTION references week_start. It is a planning bucket, not an "
        "execution filter: approved work spans weeks, and post_1490 — planned 2026-07-27, "
        "parked, returned autonomously — would be stranded permanently.")
    assert 'Post.status == "APPROVED"' in code
