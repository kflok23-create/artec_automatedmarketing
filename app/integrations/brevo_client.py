"""Brevo — email campaigns to consumer list only (§9.2).

The design lives in Brevo; HERMES never authors HTML. Exact flow:
  a) GET /v3/smtp/templates/{id} → htmlContent
  b) substitute ONLY the SIX in-body variables locally; `{{subject}}` is generated but
     passed as the campaign `subject` parameter, never substituted into the body;
     `{{ mirror }}` and `{{ unsubscribe }}` are Brevo's own tags and survive untouched
  c) POST /v3/emailCampaigns (htmlContent, NEVER templateId) → sendNow

templateId on a campaign does not interpolate per-send variables — placeholders would render
as literal text and the tracked URL would never reach the reader. Transactional
POST /v3/smtp/email is forbidden here: it bypasses list unsubscribe handling.
"""

from __future__ import annotations

import re

import httpx

from app.settings import Settings

BASE = "https://api.brevo.com/v3"

SIX_VARIABLES = ("hero_image_url", "headline", "body_copy", "cta_text", "tracked_url", "story_block")
BREVO_OWN_TAGS = {"mirror", "unsubscribe"}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class BrevoError(RuntimeError):
    pass


class TemplateContractError(BrevoError):
    """Template 3 was edited in Brevo and no longer carries the agreed variables."""


class BrevoCreditsError(BrevoError):
    """402 from sendNow — insufficient Brevo credits. Post stays RENDERED; retry after top-up."""


def substitute_template(html: str, variables: dict[str, str]) -> str:
    """Substitute exactly the six in-body variables. Fail with a named error if any is
    absent from the fetched HTML, or if any non-Brevo placeholder survives substitution.
    The non-variable bytes of the template pass through unchanged."""
    present = {m.group(1) for m in _PLACEHOLDER.finditer(html)}
    missing = [v for v in SIX_VARIABLES if v not in present]
    if missing:
        raise TemplateContractError(
            f"template is missing in-body variable(s) {missing} — the Brevo template was "
            "edited and the contract is broken; restore the placeholders in Brevo"
        )
    unknown = sorted(set(variables) - set(SIX_VARIABLES))
    if unknown:
        raise TemplateContractError(f"refusing to substitute non-contract variable(s): {unknown}")

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return str(variables[name])
        return m.group(0)  # leave everything else (incl. Brevo's own tags) untouched

    result = _PLACEHOLDER.sub(_sub, html)
    leftovers = {m.group(1) for m in _PLACEHOLDER.finditer(result)} - BREVO_OWN_TAGS
    if leftovers:
        raise TemplateContractError(
            f"placeholders survived substitution: {sorted(leftovers)} — every in-body "
            "variable must be supplied"
        )
    return result


class Brevo:
    def __init__(self, settings: Settings):
        self._key = settings.BREVO_API_KEY
        self._list_id = int(settings.BREVO_LIST_ID)
        self._template_id = int(settings.BREVO_TEMPLATE_ID)
        self._sender = settings.BREVO_SENDER_EMAIL

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._key, "accept": "application/json", "content-type": "application/json"}

    def get_template_html(self) -> str:
        with httpx.Client(timeout=60) as client:
            resp = client.get(f"{BASE}/smtp/templates/{self._template_id}", headers=self._headers())
        if resp.status_code >= 400:
            raise BrevoError(f"GET template {self._template_id} failed: HTTP {resp.status_code}")
        html = resp.json().get("htmlContent")
        if not html:
            raise TemplateContractError(f"template {self._template_id} has no htmlContent")
        return html

    def get_list_count(self) -> int:
        with httpx.Client(timeout=60) as client:
            resp = client.get(f"{BASE}/contacts/lists/{self._list_id}", headers=self._headers())
        if resp.status_code >= 400:
            raise BrevoError(f"GET list {self._list_id} failed: HTTP {resp.status_code}")
        body = resp.json()
        return int(body.get("uniqueSubscribers") or body.get("totalSubscribers") or 0)

    def create_campaign(self, name: str, subject: str, html: str) -> int:
        payload = {
            "name": name,
            "subject": subject,
            "type": "classic",
            "sender": {"email": self._sender},
            "htmlContent": html,  # NEVER templateId — see module docstring
            "recipients": {"listIds": [self._list_id]},
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{BASE}/emailCampaigns", headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise BrevoError(f"create campaign failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return int(resp.json()["id"])

    def delete_campaign(self, campaign_id: int) -> bool:
        """Used ONLY by `artec prove brevo-send`, which creates a campaign to prove the
        template contract and then removes it. Deleting a campaign that was never sent
        cannot reach a subscriber; leaving proof campaigns behind would clutter the account
        and eventually get one sent by accident."""
        with httpx.Client(timeout=60) as client:
            resp = client.delete(f"{BASE}/emailCampaigns/{campaign_id}",
                                 headers=self._headers())
        return resp.status_code in (200, 204)

    def send_now(self, campaign_id: int) -> None:
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{BASE}/emailCampaigns/{campaign_id}/sendNow", headers=self._headers())
        if resp.status_code == 402:
            raise BrevoCreditsError(
                "Brevo returned 402 (insufficient email credits) — top up credits and re-run "
                "`artec publish`; the post remains RENDERED"
            )
        if resp.status_code >= 400:
            raise BrevoError(f"sendNow failed: HTTP {resp.status_code}: {resp.text[:300]}")
