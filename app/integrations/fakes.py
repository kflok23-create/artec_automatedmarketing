"""In-process fakes for `artec cycle --dry-run` and the test suite.

Explicitly injected only (DECISIONS.md #18) — production code paths never select these.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any


def _write_scratch_media(suffix: str, size: tuple[int, int] = (1080, 1080)) -> str:
    """A real, openable media file.

    Sized and textured like a real render rather than like a token: publish pre-flight now
    measures CONTENT (byte floor, decode, aspect), and a 64x64 flat swatch would fail it —
    which would mean the fixture, not the artefact, decided the test. Same lesson as the
    video bitrate floor: fixtures for anything measuring content must be realistic.
    """
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    if suffix in (".png", ".jpg", ".jpeg"):
        import random

        from PIL import Image

        image = Image.new("RGB", size)
        rng = random.Random(7)                      # deterministic, but not compressible
        image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                       for _ in range(size[0] * size[1])])
        image.save(path)
    else:
        with open(path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42fakebytes")
    return path


class FakeLLM:
    """Returns canned, schema-valid replies keyed by prompt file."""

    def __init__(self, canned: dict[str, Any] | None = None):
        self.canned = canned or {}
        self.calls: list[tuple[str, dict]] = []

    def _default(self, prompt_file: str, variables: dict) -> Any:
        if prompt_file == "ideate_v1.md":
            cadence = variables.get("cadence") or {"instagram": 1}
            if isinstance(cadence, str):
                import json

                cadence = json.loads(cadence)
            plan = []
            for channel, count in cadence.items():
                for i in range(int(count)):
                    plan.append(
                        {
                            "channel": channel,
                            "angle": "build-confidence",
                            "hook": f"What a 7-year-old builds in 10 minutes ({i + 1})",
                            "cta_type": "discount",
                            "cta_placement": "caption_end",
                            "keywords": ["stem toys"],
                            "slot": "evening",
                        }
                    )
            return plan
        if prompt_file == "toolbox_route_v1.md":
            return {
                "subject": "assembled_blocks",
                "tools": ["asset"],
                "asset_ids": [],
                "prompt": "clean product shot",
            }
        if prompt_file == "caption_v1.md":
            return {
                "caption": "Build focus, one block at a time.",
                "subject": "The 10-minute focus builder",
                "headline": "Build focus, one block at a time",
                "body_copy": "Artec blocks snap on every side.",
                "cta_text": "Get S$10 off",
                "story_block": "We made this because focus is built, not born.",
            }
        if prompt_file == "learn_v1.md":
            return []
        if prompt_file == "wishlist_v1.md":
            return [
                {
                    "target_folder": "raw-photo/assembled",
                    "medium": "photo",
                    "aspect": "square",
                    "description": "assembled build on a clean background",
                }
            ]
        return {}

    def complete_json(self, prompt_file: str, variables: dict, max_tokens: int = 4000) -> Any:
        self.calls.append((prompt_file, variables))
        if prompt_file in self.canned:
            return self.canned[prompt_file]
        return self._default(prompt_file, variables)

    def complete_text(self, prompt_file: str, variables: dict, max_tokens: int = 2000) -> str:
        self.calls.append((prompt_file, variables))
        return "ok"

    def ping(self) -> bool:
        return True


class FakeFal:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, endpoint: str, arguments: dict, timeout_s: int = 600) -> dict:
        self.calls.append((endpoint, arguments))
        return {
            "images": [{"url": "https://fake.fal.media/render.png", "width": 1080, "height": 1080}],
            "video": {"url": "https://fake.fal.media/render.mp4"},
            "image": {"url": "https://fake.fal.media/render.png"},
            "seed": 42,
            "has_nsfw_concepts": [False],
        }

    def upload_public(self, local_path: str) -> str:
        return f"https://fake.fal.media/{os.path.basename(local_path)}"

    def fetch(self, url: str, suffix: str) -> str:
        return _write_scratch_media(suffix)


class FakeDrive:
    """Minimal in-memory bank; download() writes a real scratch file so Pillow/ffmpeg-free
    code paths can run."""

    def __init__(self):
        self.uploaded: list[tuple[str, str, str]] = []  # (local, week, filename)
        self._counter = 0
        self._by_id: dict[str, str] = {}

    def download(self, file_id: str, suffix: str = "") -> str:
        """Returns the bytes that were uploaded under this id where we have them — so a
        render → publish path reads back the artefact it actually produced, at the aspect
        that render chose, instead of a stand-in that pre-flight would judge instead."""
        known = self._by_id.get(file_id)
        if known and os.path.exists(known):
            fd, path = tempfile.mkstemp(suffix=suffix or os.path.splitext(known)[1])
            os.close(fd)
            with open(known, "rb") as src, open(path, "wb") as dst:
                dst.write(src.read())
            return path
        return _write_scratch_media(suffix or ".png")

    def upload_generated(self, local_path: str, week_start: str, filename: str) -> str:
        self._counter += 1
        self.uploaded.append((local_path, week_start, filename))
        file_id = f"gen_{self._counter}"
        self._by_id[file_id] = local_path
        return file_id

    def upload_backup(self, local_path: str, filename: str) -> str:
        self._counter += 1
        self.uploaded.append((local_path, "_backups", filename))
        return f"backup_{self._counter}"

    def probe_write(self) -> bool:
        return True


class FakeUploadPost:
    def __init__(self):
        self.calls: list[dict] = []

    def _fields(self, platform: str, page_targets: dict | None) -> dict:
        """THE FAKE RUNS THE REAL REFUSAL.

        A fake that accepts `page_targets` and ignores it would let the dry run publish to
        facebook with no page configured and report success — which is precisely the
        production failure this part exists to prevent, reproduced inside the harness that
        is supposed to catch it. So the fake delegates to the real `_page_fields`: the
        refusal is exercised on every dry run, and the recorded call carries the parameter
        that would have gone over the wire.
        """
        from app.integrations.upload_post_client import UploadPost

        return UploadPost.__dict__["_page_fields"](self, platform, page_targets)

    def upload_photo(self, platform: str, photo_path: str, title: str,
                     page_targets: dict | None = None,
                     description: str | None = None) -> dict:
        fields = self._fields(platform, page_targets)
        self.calls.append({"kind": "photo", "platform": platform, "title": title,
                           "description": description, **fields})
        return {"success": True, "request_id": f"up_{len(self.calls)}", **fields}

    def upload_video(self, platform: str, video_path: str, title: str,
                     page_targets: dict | None = None,
                     description: str | None = None) -> dict:
        fields = self._fields(platform, page_targets)
        self.calls.append({"kind": "video", "platform": platform, "title": title,
                           "description": description, **fields})
        return {"success": True, "request_id": f"up_{len(self.calls)}", **fields}

    def list_profiles(self) -> dict:
        return {"profiles": [{"username": "ArtecMy", "social_accounts": {}}]}


class FakeBrevo:
    TEMPLATE = (
        "<html><body><img src='{{hero_image_url}}'><h1>{{headline}}</h1><p>{{body_copy}}</p>"
        "<a href='{{tracked_url}}'>{{cta_text}}</a><p>{{story_block}}</p>"
        "<a href='{{ mirror }}'>view</a><a href='{{ unsubscribe }}'>unsub</a></body></html>"
    )

    def __init__(self):
        self.campaigns: list[dict] = []
        self.sent: list[int] = []

    def get_template_html(self) -> str:
        return self.TEMPLATE

    def get_list_count(self) -> int:
        return 7

    def create_campaign(self, name: str, subject: str, html: str) -> int:
        self.campaigns.append({"name": name, "subject": subject, "html": html})
        return len(self.campaigns)

    def send_now(self, campaign_id: int) -> None:
        self.sent.append(campaign_id)


class FakeTelegram:
    def __init__(self, scripted_updates: list[list[dict]] | None = None):
        self.sent: list[dict] = []
        self._updates = scripted_updates or []
        self.chat_id = "123"

    def send_message(self, text: str, buttons: list | None = None) -> dict:
        self.sent.append({"text": text, "buttons": buttons})
        return {"message_id": len(self.sent)}

    def get_updates(self, offset: int | None = None, timeout_s: int = 25) -> list[dict]:
        if self._updates:
            return self._updates.pop(0)
        return [{"update_id": (offset or 0) + 1, "message": {"text": "/done"}}]

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        pass

    def get_me(self) -> dict:
        return {"username": "ArtecMarktingBot"}
