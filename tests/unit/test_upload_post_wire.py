"""Real Upload-Post client over a mocked wire with REAL bytes payloads — doctor only
verifies the key and connected platforms, which is how a multipart-encoding TypeError
reached the first live publish."""

import os
import tempfile

import httpx
import pytest
import respx
from PIL import Image

from app.integrations.upload_post_client import UploadPost, UploadPostError
from app.settings import Settings

BASE = "https://api.upload-post.com/api"


@pytest.fixture
def jpeg_path():
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    Image.new("RGB", (32, 32), "#0168B7").save(path)
    return path


@respx.mock
def test_photo_upload_encodes_multipart_with_real_bytes(jpeg_path):
    route = respx.post(f"{BASE}/upload_photos").mock(
        return_value=httpx.Response(200, json={"success": True, "request_id": "r1"})
    )
    out = UploadPost(Settings()).upload_photo("instagram", jpeg_path, "caption text here")
    assert out["request_id"] == "r1"

    req = route.calls.last.request
    assert req.headers["Authorization"].startswith("Apikey ")
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content  # encoding the body is exactly where the tuple TypeError blew up
    assert b'name="user"' in body and b"ArtecMy" in body
    assert b'name="platform[]"' in body and b"instagram" in body
    assert b'name="title"' in body and b"caption text here" in body
    assert b'name="photos[]"' in body and b"\xff\xd8" in body  # JPEG magic — real bytes


@respx.mock
def test_video_upload_uses_upload_endpoint_with_video_field(jpeg_path):
    route = respx.post(f"{BASE}/upload").mock(
        return_value=httpx.Response(200, json={"success": True, "request_id": "v1"})
    )
    fd, vid = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42-video-bytes")
    out = UploadPost(Settings()).upload_video("tiktok", vid, "video caption")
    assert out["request_id"] == "v1"
    body = route.calls.last.request.content
    assert b'name="video"' in body and b"ftypmp42" in body
    assert b'name="platform[]"' in body and b"tiktok" in body


@respx.mock
def test_provider_rejection_surfaces_without_key(jpeg_path):
    respx.post(f"{BASE}/upload_photos").mock(
        return_value=httpx.Response(200, json={"success": False, "message": "no ig session"})
    )
    with pytest.raises(UploadPostError, match="rejected"):
        UploadPost(Settings()).upload_photo("instagram", jpeg_path, "t")


@respx.mock
def test_http_error_surfaces_status(jpeg_path):
    respx.post(f"{BASE}/upload_photos").mock(return_value=httpx.Response(429, text="rate limited"))
    with pytest.raises(UploadPostError, match="429"):
        UploadPost(Settings()).upload_photo("instagram", jpeg_path, "t")
