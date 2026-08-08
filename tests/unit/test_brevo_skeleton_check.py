"""The brevo-send prover reported FAILED, and the fault was its own check.

Live result from the first all-nine sweep:

    prove brevo-send: FAILED — substitution changed bytes outside the variables

`substitute_template` matches placeholders with a regex that TOLERATES WHITESPACE:

    _PLACEHOLDER = r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}"

The prover's skeleton comparison did not:

    skeleton_before.replace("{{" + name + "}}", "")

So a template written `{{ headline }}` — the ordinary convention — kept the placeholder in
the BEFORE skeleton while the AFTER skeleton had the value stripped, and the two could never
match. Two sides built from two different notions of "a placeholder": the wrong-comparison
pattern, inside the harness written to catch it.

Two further faults in the same six lines, each able to produce a wrong verdict on its own:
  - it removed each VALUE wherever it occurred, so a value that also appears naturally in the
    template over-removes, and one value that is a substring of another collides
  - it compared with all whitespace deleted, which would have HIDDEN a real change to the
    template's spacing — a false PASS in the same check that was giving a false FAIL

WHAT SUPPLIES EACH SIDE NOW: both skeletons are built with `_PLACEHOLDER`, the same regex the
substitution uses, and the values are unique sentinels that cannot occur in HTML — so
removing them afterwards cannot remove anything else. The comparison is exact.
"""

from __future__ import annotations

import re

import pytest

from app.integrations.brevo_client import _PLACEHOLDER, substitute_template

SIX = ("hero_image_url", "headline", "body_copy", "cta_text", "tracked_url", "story_block")
VALUES = {name: f"value-for-{name}" for name in SIX}


def _skeletons(html: str, variables: dict) -> tuple[str, str]:
    """The prover's check, as a function, so the test exercises the real logic."""
    sentinels = {name: f"@@ARTEC_SENTINEL_{i}@@" for i, name in enumerate(variables)}
    probe = substitute_template(html, sentinels)
    after = re.sub(r"@@ARTEC_SENTINEL_\d+@@", "", probe)
    before = _PLACEHOLDER.sub(
        lambda m: "" if m.group(1) in variables else m.group(0), html)
    return before, after


def _template(fmt: str) -> str:
    body = "".join(f"<p>{fmt.format(name=n)}</p>" for n in SIX)
    return f"<html><body>{body}<a>{{{{unsubscribe}}}}</a></body></html>"


@pytest.mark.parametrize("fmt,label", [
    ("{{{{{name}}}}}", "no spaces"),
    ("{{{{ {name} }}}}", "spaces — THE CASE THAT PRODUCED THE FALSE FAIL"),
    ("{{{{  {name}  }}}}", "extra spaces"),
])
def test_the_skeletons_match_however_the_placeholder_is_spaced(fmt, label):
    before, after = _skeletons(_template(fmt), VALUES)
    assert before == after, f"skeletons diverged for a template with {label}"


def test_a_value_that_also_appears_in_the_template_does_not_over_remove():
    """The second fault. Removing the VALUE wherever it occurs deletes template bytes that
    were never substituted — and the old check would have called that a change."""
    html = _template("{{{{ {name} }}}}").replace(
        "<html>", "<html><footer>value-for-headline</footer>")
    before, after = _skeletons(html, VALUES)
    assert before == after
    assert "value-for-headline" in after, "the literal footer text was destroyed"


def test_a_REAL_change_to_the_template_is_still_caught():
    """The check must not have been loosened into uselessness. This is the failure it exists
    for — and note the old whitespace-blind comparison would have MISSED a spacing-only
    change, so the fix tightens as well as corrects."""
    html = _template("{{{{ {name} }}}}")

    class _Vandal(dict):
        pass

    before = _PLACEHOLDER.sub(lambda m: "", html)
    after = _PLACEHOLDER.sub(lambda m: "", html).replace("<body>", "<body><script>x</script>")
    assert before != after


def test_brevo_owned_tags_survive_untouched():
    """`unsubscribe` and `mirror` are Brevo's, not ours. Substituting or stripping them would
    break the sent email in a way no local test of our six variables would notice."""
    html = _template("{{{{ {name} }}}}")
    before, after = _skeletons(html, VALUES)
    assert "{{unsubscribe}}" in before and "{{unsubscribe}}" in after
