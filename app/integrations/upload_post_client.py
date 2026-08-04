"""Upload-Post client — contract verified against docs.upload-post.com/openapi.json
(DECISIONS.md #1):

  photos → POST https://api.upload-post.com/api/upload_photos   field `photos[]`
  video  → POST https://api.upload-post.com/api/upload          field `video`
  shared → `user`, `platform[]` (repeated), `title`, `description`
  auth   → Authorization: Apikey <UPLOAD_POST_API_KEY>

Media is downloaded from Drive to /tmp and streamed as multipart — never a Drive link.
Platform rules run BEFORE render so no money is spent on an unpublishable post.
"""

from __future__ import annotations

import os

import httpx

from app.settings import Settings

BASE = "https://api.upload-post.com/api"

PLATFORMS = ("tiktok", "instagram", "facebook", "youtube", "linkedin")

# Encoded per-platform validation, run pre-render (§9.1).
PLATFORM_RULES: dict[str, dict] = {
    "tiktok":    {"media": {"video"},          "max_caption": 2200, "video_duration_s": (3, 600)},
    "instagram": {"media": {"photo", "video"}, "max_caption": 2200, "video_duration_s": (3, 900)},
    "facebook":  {"media": {"photo", "video"}, "max_caption": 5000, "video_duration_s": (1, 1200)},
    "youtube":   {"media": {"video"},          "max_caption": 5000, "max_title": 100, "video_duration_s": (1, 60)},
    "linkedin":  {"media": {"photo", "video"}, "max_caption": 3000, "video_duration_s": (3, 600)},
}


# ---------------------------------------------------------------------------------------
# PAGE TARGETING. Facebook and LinkedIn are OAuth'd through a PERSONAL account that
# administers the Artec business page. Posts must go out AS THE PAGE.
#
# The parameter names differ between the two and are not guessable — they come from
# docs.upload-post.com, not from symmetry:
#   facebook  upload: facebook_page_id          analytics: page_id
#   linkedin  upload: target_linkedin_page_id   analytics: page_urn
#
# THE LINKEDIN ACCOUNT ADMINISTERS TWO ORGANISATIONS:
#   urn:li:organization:97212204  "Artec Malaysia"              <- ours
#   urn:li:organization:74925843  "Tech Up Advance l GoTechUp"  <- A DIFFERENT BUSINESS
# Artec content reaching the second is the failure this module exists to make structurally
# impossible. Configuring the right URN proves the right URN works; it does not prove the
# wrong one is unreachable. Those are different claims — see the negative test.
# ---------------------------------------------------------------------------------------

PAGE_TARGETED_PLATFORMS = ("facebook", "linkedin")

PAGE_TARGET_FIELD = {
    "facebook": "facebook_page_id",
    "linkedin": "target_linkedin_page_id",
}
PAGE_TARGET_CONFIG_KEY = {
    "facebook": "facebook_page_id",
    "linkedin": "linkedin_organization_urn",
}
# Read by analytics as well as by publish, so the channel MEASURED is provably the channel
# POSTED TO. One source; there is no second place for these to drift.
PAGE_ANALYTICS_FIELD = {
    "facebook": "page_id",
    "linkedin": "page_urn",
}

# Known-administered but NOT ours. Named explicitly so the refusal can say why, and so a
# reader sees what the system is choosing between rather than trusting that it chose.
FOREIGN_ORGANISATIONS = {
    "urn:li:organization:74925843": "Tech Up Advance l GoTechUp",
}


class UploadPostError(RuntimeError):
    pass


class MissingPageTarget(UploadPostError):
    """No page configured for a platform that publishes as a page. Refuse, never default."""


class ForeignPageTarget(UploadPostError):
    """The configured target is an organisation that is not Artec's."""


class PlatformValidationError(UploadPostError):
    pass


def validate_for_platform(channel: str, media_kind: str, caption: str, duration_s: float | None = None) -> None:
    """Raise PlatformValidationError before any render spend if the post cannot publish."""
    rules = PLATFORM_RULES.get(channel)
    if rules is None:
        raise PlatformValidationError(f"unknown platform: {channel!r} (valid: {PLATFORMS})")
    if media_kind not in rules["media"]:
        raise PlatformValidationError(f"{channel} does not accept media kind {media_kind!r}")
    if len(caption) > rules["max_caption"]:
        raise PlatformValidationError(
            f"{channel} caption is {len(caption)} chars; limit {rules['max_caption']}"
        )
    if media_kind == "video" and duration_s is not None:
        lo, hi = rules["video_duration_s"]
        if not (lo <= duration_s <= hi):
            raise PlatformValidationError(
                f"{channel} video duration {duration_s}s outside [{lo}, {hi}]s"
            )


class UploadPost:
    def __init__(self, settings: Settings):
        self._key = settings.UPLOAD_POST_API_KEY
        self._user = settings.UPLOAD_POST_USER

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Apikey {self._key}"}

    def _post(self, path: str, data: dict, files: list[tuple[str, tuple]]) -> dict:
        # httpx multipart contract: `data` MUST be a dict (list values for repeated fields
        # like platform[]); a list of tuples silently mis-encodes and the body join blows
        # up with "expected a bytes-like object, tuple found" at request time.
        with httpx.Client(timeout=600) as client:
            resp = client.post(f"{BASE}{path}", headers=self._headers(), data=data, files=files)
        if resp.status_code >= 400:
            # Never echo the API key; the response body is provider text, safe to surface.
            raise UploadPostError(f"upload-post {path} failed: HTTP {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        if isinstance(body, dict) and body.get("success") is False:
            raise UploadPostError(f"upload-post {path} rejected: {str(body)[:500]}")
        return body

    def _page_fields(self, platform: str, page_targets: dict | None) -> dict:
        """The page/organisation parameter for platforms that publish AS A PAGE.

        BOTH PLATFORMS ARE OAUTH'd THROUGH A PERSONAL ACCOUNT that administers the Artec
        page. Omit the parameter and Upload-Post returns SUCCESS — the post simply lands on
        the personal timeline. Success is not the same as correct target, and nothing in the
        response distinguishes them.

        It breaks measurement, not only branding. LinkedIn exposes no member-level analytics
        at all, so a personally-published post can never be measured; Facebook analytics
        requires `page_id`. Published personally, `metrics` stays NULL forever and `learn`
        scores those channels on absent data while appearing to have measured them.

        REFUSES RATHER THAN DEFAULTS. A missing id raises here, before any bytes are sent.
        There is no code path that publishes to facebook or linkedin without an explicit
        target — the guarantee is the absence of a fall-through, not a flag that is off.
        """
        if platform not in PAGE_TARGETED_PLATFORMS:
            return {}
        field = PAGE_TARGET_FIELD[platform]
        value = str((page_targets or {}).get(platform) or "").strip()
        if value in FOREIGN_ORGANISATIONS:
            raise ForeignPageTarget(
                f"REFUSING to publish {platform} content to {value} "
                f"({FOREIGN_ORGANISATIONS[value]}) — that is a different business. The "
                f"OAuth'd account administers it, so Upload-Post would accept the post and "
                f"return success. This refusal is the only thing standing between an Artec "
                f"post and someone else's page.")
        if not value:
            raise MissingPageTarget(
                f"{platform} publishes AS A PAGE and no target is configured "
                f"(config key {PAGE_TARGET_CONFIG_KEY[platform]!r}). Refusing to publish: "
                f"omitting {field} does not fail — it silently posts to the PERSONAL "
                f"profile of the account that OAuth'd, publicly, and that channel then "
                f"returns no analytics ever. Seed the config key and retry.")
        return {field: value}

    def upload_photo(self, platform: str, photo_path: str, title: str,
                     page_targets: dict | None = None) -> dict:
        data = {"user": self._user, "platform[]": [platform], "title": title}
        data.update(self._page_fields(platform, page_targets))
        with open(photo_path, "rb") as fh:
            files = [("photos[]", (os.path.basename(photo_path), fh.read(), "image/jpeg"))]
        return self._post("/upload_photos", data, files)

    def upload_video(self, platform: str, video_path: str, title: str,
                     page_targets: dict | None = None) -> dict:
        data = {"user": self._user, "platform[]": [platform], "title": title}
        data.update(self._page_fields(platform, page_targets))
        with open(video_path, "rb") as fh:
            files = [("video", (os.path.basename(video_path), fh.read(), "video/mp4"))]
        return self._post("/upload", data, files)

    def list_profiles(self) -> dict:
        """doctor: verify the API key and that all five platforms show connected."""
        with httpx.Client(timeout=60) as client:
            resp = client.get(f"{BASE}/uploadposts/users", headers=self._headers())
        if resp.status_code >= 400:
            raise UploadPostError(f"upload-post profile check failed: HTTP {resp.status_code}")
        return resp.json()

    def list_pages(self, platform: str) -> list[dict]:
        """The LIVE listing of pages/organisations this profile administers.

        This is the other side of doctor's comparison. Config says where a post SHOULD go;
        this says where it CAN go. Neither is supplied by the publisher, which is what makes
        the check a guard rather than a restatement.
        """
        if platform not in PAGE_TARGETED_PLATFORMS:
            return []
        with httpx.Client(timeout=60) as client:
            resp = client.get(f"{BASE}/uploadposts/{platform}/pages",
                              headers=self._headers(), params={"profile": self._user})
        if resp.status_code >= 400:
            raise UploadPostError(
                f"upload-post {platform} page listing failed: HTTP {resp.status_code}: "
                f"{resp.text[:300]}")
        body = resp.json()
        pages = body.get("pages") if isinstance(body, dict) else body
        return list(pages or [])


def extract_external_id(response: dict) -> str:
    """Pull a stable external id out of an upload response, tolerating shape drift."""
    for key in ("request_id", "job_id", "id"):
        if key in response:
            return str(response[key])
    results = response.get("results") or response.get("uploads") or []
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            for key in ("post_id", "id", "url"):
                if key in first:
                    return str(first[key])
    return "uploaded"


def page_targets(session) -> dict:
    """The configured page/organisation per platform, read from `config`.

    ONE SOURCE. Publish reads this and so does analytics, so the channel MEASURED is
    provably the channel POSTED TO. A second place to put these ids is a second place for
    them to drift, and drift here is invisible: both a wrong page and a right page return
    success from the upload, and both return *some* analytics shape.
    """
    from app.config import get_config

    return {
        "facebook": get_config(session, PAGE_TARGET_CONFIG_KEY["facebook"], "") or "",
        "linkedin": get_config(session, PAGE_TARGET_CONFIG_KEY["linkedin"], "") or "",
    }


def verify_publish_target(platform: str, targets: dict, response: dict) -> str | None:
    """Did the post land on the page we aimed at? Returns a description of the mismatch, or
    None when it matches or when the response says nothing either way.

    WHAT SUPPLIES EACH SIDE: the id we ASKED for comes from `config`; the owner is read
    from the provider's own response. The publisher supplies neither. An absent owner field
    returns None rather than a false pass — unknown is not a match, and it is not a
    mismatch either; the doctor check is what covers the unknown case.
    """
    if platform not in PAGE_TARGETED_PLATFORMS:
        return None
    expected = str(targets.get(platform) or "").strip()
    if not expected:
        return None
    blob = str(response)
    reported = ""
    for key in ("page_id", "page_urn", "organization", "owner_id", "author",
                "facebook_page_id", "target_linkedin_page_id"):
        value = response.get(key) if isinstance(response, dict) else None
        if value:
            reported = str(value)
            break
    if not reported:
        # The response did not name an owner. Fall back to substring presence: the numeric
        # id or URN normally appears inside the returned post URL.
        digits = expected.rsplit(":", 1)[-1]
        if digits and digits in blob:
            return None
        return None
    if expected in reported or reported in expected or expected.rsplit(":", 1)[-1] in reported:
        return None
    for urn, name in FOREIGN_ORGANISATIONS.items():
        if urn in reported or urn.rsplit(":", 1)[-1] in reported:
            return (f"post landed on {urn} ({name}) — A DIFFERENT BUSINESS — "
                    f"but was aimed at {expected}")
    return f"post reports owner {reported!r}; expected {expected!r}"
