"""Acceptance 2f — the Brevo template contract."""

import httpx
import pytest
import respx

from app.integrations.brevo_client import (
    Brevo,
    TemplateContractError,
    substitute_template,
)
from app.integrations.fakes import FakeBrevo
from app.settings import Settings

SIX = {
    "hero_image_url": "https://cdn.example/hero.jpg",
    "headline": "Build focus",
    "body_copy": "Blocks snap on every side.",
    "cta_text": "Get S$10 off",
    "tracked_url": "https://artec.my/?code=EMAIL50&utm_campaign=post_1482",
    "story_block": "We made this because focus is built.",
}


def test_six_variables_substituted_and_brevo_tags_survive():
    out = substitute_template(FakeBrevo.TEMPLATE, SIX)
    for v in SIX.values():
        assert v in out
    assert "{{ mirror }}" in out          # Brevo's own tags survive untouched
    assert "{{ unsubscribe }}" in out
    assert "{{headline}}" not in out


def test_non_variable_bytes_unchanged():
    out = substitute_template(FakeBrevo.TEMPLATE, SIX)
    # Strip both documents down to their non-placeholder skeleton and compare.
    skeleton_in = FakeBrevo.TEMPLATE
    skeleton_out = out
    for k, v in SIX.items():
        skeleton_in = skeleton_in.replace("{{" + k + "}}", "")
        skeleton_out = skeleton_out.replace(v, "")
    assert skeleton_in == skeleton_out


def test_missing_in_body_variable_is_a_named_contract_break():
    broken = FakeBrevo.TEMPLATE.replace("{{tracked_url}}", "")
    with pytest.raises(TemplateContractError, match="tracked_url"):
        substitute_template(broken, SIX)


def test_surviving_placeholder_fails():
    html = FakeBrevo.TEMPLATE + " {{rogue_tag}}"
    with pytest.raises(TemplateContractError, match="rogue_tag"):
        substitute_template(html, SIX)


def test_subject_is_never_an_in_body_substitution():
    with pytest.raises(TemplateContractError, match="non-contract"):
        substitute_template(FakeBrevo.TEMPLATE, {**SIX, "subject": "hi"})


@respx.mock
def test_campaign_payload_htmlcontent_never_templateid():
    route = respx.post("https://api.brevo.com/v3/emailCampaigns").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )
    brevo = Brevo(Settings())
    html = substitute_template(FakeBrevo.TEMPLATE, SIX)
    cid = brevo.create_campaign(name="post_1482", subject="The 10-minute focus builder", html=html)
    assert cid == 99
    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert "htmlContent" in body and body["htmlContent"] == html
    assert "templateId" not in body            # templateId would break variable interpolation
    assert body["subject"] == "The 10-minute focus builder"  # campaign parameter, not body
    assert body["recipients"] == {"listIds": [3]}
    assert body["type"] == "classic"
