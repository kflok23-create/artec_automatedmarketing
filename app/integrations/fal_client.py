"""fal.ai queue client — submit → poll inline with a bounded timeout → fetch result.

No scheduler and no worker (§2): polling happens inside the invoked command with visible
progress on stdout. Also hosts the public-file upload used wherever a public URL is needed
(video inputs, Brevo hero image) — Drive links are HTML viewer pages, never media URLs.
"""

from __future__ import annotations

import time

import fal_client as fal_sdk
import httpx

from app.settings import Settings


class FalError(RuntimeError):
    pass


class FalTimeout(FalError):
    pass


class Fal:
    """Thin wrapper so stages depend on an injectable object (see integrations/fakes.py)."""

    def __init__(self, settings: Settings):
        # fal_client reads FAL_KEY from the environment; assert it is present.
        if not settings.FAL_KEY:
            raise FalError("FAL_KEY is not set")

    def run(self, endpoint: str, arguments: dict, timeout_s: int = 600) -> dict:
        handle = fal_sdk.submit(endpoint, arguments=arguments)
        deadline = time.monotonic() + timeout_s
        last_note = 0.0
        while True:
            status = handle.status(with_logs=False)
            if isinstance(status, fal_sdk.Completed):
                return handle.get()
            if time.monotonic() > deadline:
                raise FalTimeout(f"fal request to {endpoint} exceeded {timeout_s}s")
            now = time.monotonic()
            if now - last_note > 10:
                print(f"  … fal {endpoint}: {type(status).__name__}")
                last_note = now
            time.sleep(2)

    def upload_public(self, local_path: str) -> str:
        """Upload a local file to fal storage; returns a public URL."""
        return fal_sdk.upload_file(local_path)

    def fetch(self, url: str, suffix: str) -> str:
        """Download a result URL to a scratch file; overridable by fakes so the dry run
        and tests never touch the network."""
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return download_url(url, path)


def download_url(url: str, dest_path: str, timeout_s: int = 300) -> str:
    with httpx.stream("GET", url, timeout=timeout_s, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest_path
