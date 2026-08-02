"""§8 THE SPINE — one tracked URL carrying code + UTM + post_id. Pure function.

post_id in utm_campaign is the ONLY join key back from orders and events. Social carries
SOCIAL_CODE with utm_medium=organic; email carries EMAIL_CODE with utm_medium=email.
"""

from __future__ import annotations

VALID_MEDIA = ("organic", "email")


def build_tracked_url(
    post_id: str,
    channel: str,
    medium: str,
    *,
    site_base_url: str = "https://artec.my",
    social_code: str = "SOCIAL50",
    email_code: str = "EMAIL50",
) -> str:
    if medium not in VALID_MEDIA:
        raise ValueError(f"medium must be one of {VALID_MEDIA}, got {medium!r}")
    code = email_code if medium == "email" else social_code
    return (
        f"{site_base_url.rstrip('/')}/"
        f"?code={code}"
        f"&utm_source={channel}"
        f"&utm_medium={medium}"
        f"&utm_campaign={post_id}"
    )


def utm_dict(post_id: str, channel: str, medium: str) -> dict[str, str]:
    return {"utm_source": channel, "utm_medium": medium, "utm_campaign": post_id}
