"""Acceptance 5 (Drive kwargs), 6 (idempotent sync), 7 (missing marking)."""

from sqlalchemy import select

from app.integrations.drive_client import DriveClient
from app.models import Asset
from app.settings import Settings
from app.stages.assets_sync import derive_aspect, sync


class _Recorder:
    """Mimics googleapiclient's fluent surface, recording kwargs per call site."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    # files()
    def files(self):
        return self

    def changes(self):
        return self

    def list(self, **kw):
        self.calls.append(("files.list", kw))
        return _Exec({"files": [], "nextPageToken": None})

    def get(self, **kw):
        self.calls.append(("files.get", kw))
        return _Exec({"id": "f1", "name": "x", "mimeType": "image/jpeg"})

    def create(self, **kw):
        self.calls.append(("files.create", kw))
        return _Exec({"id": "new1"})

    def delete(self, **kw):
        self.calls.append(("files.delete", kw))
        return _Exec({})

    def getStartPageToken(self, **kw):  # noqa: N802 (google API casing)
        self.calls.append(("changes.getStartPageToken", kw))
        return _Exec({"startPageToken": "t1"})


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def test_every_drive_call_site_passes_shared_drive_kwargs():
    rec = _Recorder()
    client = DriveClient(Settings(), service=rec)
    client.list_children("folder1")
    client.get_file("f1")
    client.get_start_page_token()

    for name, kw in rec.calls:
        assert kw.get("supportsAllDrives") is True, f"{name} missing supportsAllDrives"
        if name in ("files.list",):
            # list/changes calls additionally need includeItemsFromAllDrives + drive scoping,
            # or the API returns an empty list with no error.
            assert kw.get("includeItemsFromAllDrives") is True, f"{name} missing includeItemsFromAllDrives"
            assert kw.get("corpora") == "drive" and kw.get("driveId") == "drive123"
        if name in ("files.list", "files.get", "files.create"):
            assert "fields" in kw, f"{name} missing explicit fields="


def test_my_drive_mode_omits_drive_scoping_but_keeps_all_drives_flags(monkeypatch):
    # Empty GOOGLE_SHARED_DRIVE_ID = My Drive mode (personal Gmail): query by parent only,
    # no corpora/driveId — but keep the AllDrives flags so a future Workspace move is free.
    monkeypatch.setenv("GOOGLE_SHARED_DRIVE_ID", "")
    rec = _Recorder()
    client = DriveClient(Settings(), service=rec)
    assert client.my_drive_mode is True
    client.list_children("folder1")
    client.get_file("f1")
    client.get_start_page_token()

    for name, kw in rec.calls:
        assert "driveId" not in kw, f"{name} must not scope to a drive in My Drive mode"
        assert "corpora" not in kw, f"{name} must not pass corpora in My Drive mode"
        assert kw.get("supportsAllDrives") is True
        if name == "files.list":
            assert kw.get("includeItemsFromAllDrives") is True
            assert kw["q"] == "'folder1' in parents and trashed = false"


class _SyncDrive:
    """Fixture-tree drive for sync tests."""

    def __init__(self, tree):
        self.tree = tree  # list of (folder_path, meta)

    def walk(self):
        yield from self.tree

    def get_start_page_token(self):
        return "tok1"


FIXTURE_TREE = [
    ("raw-photo/assembled", {"id": "a1", "name": "crane.jpg", "mimeType": "image/jpeg",
                             "size": "1000", "md5Checksum": "m1",
                             "imageMediaMetadata": {"width": 1080, "height": 1080},
                             "modifiedTime": "2026-07-01T00:00:00Z", "description": "white bg"}),
    ("raw-video/child-face", {"id": "v1", "name": "clip.mp4", "mimeType": "video/mp4",
                              "size": "5000", "md5Checksum": "m2",
                              "videoMediaMetadata": {"width": 1080, "height": 1920, "durationMillis": "8000"},
                              "modifiedTime": "2026-07-02T00:00:00Z"}),
]


def test_sync_derives_tags_and_is_idempotent(session):
    drive = _SyncDrive(FIXTURE_TREE)
    sync(session, drive, full=True, log=lambda *_: None)
    first = {(a.drive_file_id, a.subject, a.medium, a.has_person, a.aspect)
             for a in session.execute(select(Asset)).scalars()}
    assert ("a1", "assembled_blocks", "photo", False, "square") in first
    assert ("v1", "child_face", "video", True, "vertical") in first

    # 6: running twice over the same tree yields identical rows, no duplicates.
    sync(session, drive, full=True, log=lambda *_: None)
    second = {(a.drive_file_id, a.subject, a.medium, a.has_person, a.aspect)
              for a in session.execute(select(Asset)).scalars()}
    assert first == second
    assert len(list(session.execute(select(Asset)).scalars())) == 2


def test_removed_file_becomes_missing_not_deleted(session):
    sync(session, _SyncDrive(FIXTURE_TREE), full=True, log=lambda *_: None)
    sync(session, _SyncDrive(FIXTURE_TREE[:1]), full=True, log=lambda *_: None)  # v1 vanished
    v1 = session.get(Asset, "v1")
    assert v1 is not None, "missing files are marked, never deleted"
    assert v1.status == "missing"
    assert session.get(Asset, "a1").status == "active"


def test_aspect_derivation():
    assert derive_aspect(1080, 1920) == "vertical"
    assert derive_aspect(1080, 1080) == "square"
    assert derive_aspect(1920, 1080) == "landscape"
    assert derive_aspect(None, 1080) is None
