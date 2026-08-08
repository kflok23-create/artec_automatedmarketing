"""Google Drive client — Shared Drive rules are non-negotiable (§4.2).

- Auth: service account JSON from GOOGLE_SERVICE_ACCOUNT_JSON (raw string, not a path).
- Every files.list / changes.list call passes supportsAllDrives=true AND
  includeItemsFromAllDrives=true with corpora='drive' + driveId. Omitting them returns an
  empty list with no error — the #1 Drive integration bug.
- files.get / files.create / files.delete pass supportsAllDrives=true
  (includeItemsFromAllDrives is a list-only parameter; the API rejects it elsewhere —
  see DECISIONS.md #2).
- Explicit fields= on every call; never the default field set.
- Downloads stream to /tmp and are discarded; Drive share links are NEVER handed to
  Upload-Post or Brevo.
- WRITES ONLY INSIDE _generated/. The taxonomy is read-only.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from collections.abc import Iterator
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from app.settings import Settings
from app.taxonomy import GENERATED_FOLDER

SCOPES = ["https://www.googleapis.com/auth/drive"]

FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,description,modifiedTime,trashed,parents,"
    "imageMediaMetadata(width,height),videoMediaMetadata(width,height,durationMillis)"
)
LIST_FIELDS = f"nextPageToken,files({FILE_FIELDS})"


class DriveError(RuntimeError):
    pass


def _http_status(e: Exception) -> int | None:
    """googleapiclient HttpError status across library versions."""
    status = getattr(e, "status_code", None)
    if status:
        return int(status)
    resp = getattr(e, "resp", None)
    if resp is not None and getattr(resp, "status", None):
        return int(resp.status)
    return None


class DriveWriteBoundaryError(DriveError):
    """Raised on any attempted write outside _generated/ — a defect, not a retry case."""


class DriveClient:
    def __init__(self, settings: Settings, service: Any | None = None):
        if service is not None:
            self.service = service
        else:
            try:
                info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            except json.JSONDecodeError as e:
                raise DriveError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON (it must be the raw key "
                    "file contents, not a file path)"
                ) from e
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_id = settings.GOOGLE_DRIVE_ROOT_FOLDER_ID
        # Empty drive_id = My Drive mode: the bank is a personal-Gmail My Drive folder
        # shared with the service account as Editor (Shared Drives are Workspace-only).
        # Queries then go by parent folder id only — no corpora/driveId scoping.
        self.drive_id = settings.GOOGLE_SHARED_DRIVE_ID or ""

    @property
    def my_drive_mode(self) -> bool:
        return not self.drive_id

    # -- reads ---------------------------------------------------------------------------

    def _list_kwargs(self) -> dict[str, Any]:
        # supportsAllDrives / includeItemsFromAllDrives stay in BOTH modes — harmless on
        # My Drive, and they preserve the Shared Drive path if the bank moves to Workspace.
        kwargs: dict[str, Any] = {
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if self.drive_id:
            kwargs["corpora"] = "drive"
            kwargs["driveId"] = self.drive_id
        return kwargs

    def list_children(self, folder_id: str) -> list[dict]:
        files: list[dict] = []
        token = None
        while True:
            resp = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=LIST_FIELDS,
                    pageSize=1000,
                    pageToken=token,
                    **self._list_kwargs(),
                )
                .execute()
            )
            files.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return files

    def walk(self) -> Iterator[tuple[str, dict]]:
        """Yield (relative_folder_path, file_meta) for every non-folder file in the bank,
        skipping the _generated/ subtree (HERMES output is not bank inventory)."""
        stack: list[tuple[str, str]] = [("", self.root_id)]
        while stack:
            rel_path, folder_id = stack.pop()
            for f in self.list_children(folder_id):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    child_rel = f"{rel_path}/{f['name']}".strip("/")
                    if child_rel.split("/")[0] == GENERATED_FOLDER:
                        continue
                    stack.append((child_rel, f["id"]))
                else:
                    yield rel_path, f

    def get_file(self, file_id: str) -> dict:
        return (
            self.service.files()
            .get(fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True)
            .execute()
        )

    def resolve_path(self, file_meta: dict) -> str:
        """Walk the parents chain up to the bank root; '' if the file is outside it."""
        segments: list[str] = []
        parents = file_meta.get("parents") or []
        guard = 0
        while parents and guard < 20:
            guard += 1
            pid = parents[0]
            if pid == self.root_id:
                return "/".join(reversed(segments))
            meta = (
                self.service.files()
                .get(fileId=pid, fields="id,name,parents", supportsAllDrives=True)
                .execute()
            )
            segments.append(meta["name"])
            parents = meta.get("parents") or []
        return ""

    def download(self, file_id: str, suffix: str = "") -> str:
        """Stream a file to /tmp (scratch only) and return the local path."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with os.fdopen(fd, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _status, done = downloader.next_chunk()
        return path

    # -- changes API (incremental sync) --------------------------------------------------

    def get_start_page_token(self) -> str:
        kwargs: dict[str, Any] = {"supportsAllDrives": True}
        if self.drive_id:
            kwargs["driveId"] = self.drive_id
        resp = self.service.changes().getStartPageToken(**kwargs).execute()
        return resp["startPageToken"]

    def list_changes(self, page_token: str) -> tuple[list[dict], str]:
        """Return (changes, new_start_page_token)."""
        changes: list[dict] = []
        token = page_token
        new_start = page_token
        while token:
            kwargs: dict[str, Any] = {
                "pageToken": token,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
            }
            if self.drive_id:
                kwargs["driveId"] = self.drive_id
            resp = self.service.changes().list(**kwargs).execute()
            changes.extend(resp.get("changes", []))
            new_start = resp.get("newStartPageToken", new_start)
            token = resp.get("nextPageToken")
        return changes, new_start

    # -- writes: _generated/ ONLY --------------------------------------------------------

    def _find_child_folder(self, parent_id: str, name: str) -> str | None:
        for f in self.list_children(parent_id):
            if f["name"] == name and f["mimeType"] == "application/vnd.google-apps.folder":
                return f["id"]
        return None

    def generated_folder_id(self) -> str:
        fid = self._find_child_folder(self.root_id, GENERATED_FOLDER)
        if fid is None:
            raise DriveError(
                f"'{GENERATED_FOLDER}/' folder not found under the bank root — create it in "
                "Drive (HERMES writes into it but the taxonomy build is operator groundwork)"
            )
        return fid

    def _ensure_generated_subfolder(self, name: str) -> str:
        gen_id = self.generated_folder_id()
        fid = self._find_child_folder(gen_id, name)
        if fid:
            return fid
        resp = (
            self.service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [gen_id],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return resp["id"]

    def upload_backup(self, local_path: str, filename: str) -> str:
        """`_backups/` is the SECOND folder HERMES may write to, added in v4 for job 8.
        The v2 rule was `_generated/` only; a nightly dump has to land somewhere an
        incident can reach, and the bank is the one storage this system already trusts.
        Stated here rather than left as an exception someone discovers later."""
        from googleapiclient.http import MediaFileUpload

        # `self.settings` DOES NOT EXIST. This line read it anyway, and job 8 died on it
        # the first night it ever got this far:
        #
        #   job 8 pg-dump FAILED: AttributeError: 'DriveClient' object has no attribute
        #   'settings'
        #
        # __init__ stores `self.root_id` and every other method uses it (the folder walk, the
        # generated-folder lookup). This one line was the only reader of an attribute nobody
        # ever set, so `upload_backup` had never completed once — it could not be reached
        # while pg_dump was missing from the image, and fixing that exposed it.
        root = self.root_id
        folder = self._find_child_folder(root, "_backups")
        if not folder:
            folder = (self.service.files().create(
                body={"name": "_backups", "mimeType": "application/vnd.google-apps.folder",
                      "parents": [root]},
                fields="id", supportsAllDrives=True).execute())["id"]
        media = MediaFileUpload(local_path, resumable=True)
        created = (self.service.files().create(
            body={"name": filename, "parents": [folder]},
            media_body=media, fields="id", supportsAllDrives=True).execute())
        return created["id"]

    def upload_generated(self, local_path: str, week_start: str, filename: str) -> str:
        """Upload a render to _generated/{week_start}/{filename}; return drive_file_id.
        This is the ONLY Drive write path in HERMES."""
        if not filename or "/" in filename or "\\" in filename:
            raise DriveWriteBoundaryError(f"invalid generated filename: {filename!r}")
        folder_id = self._ensure_generated_subfolder(week_start)
        media = MediaFileUpload(local_path, resumable=True)
        resp = (
            self.service.files()
            .create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return resp["id"]

    def probe_write(self) -> str:
        """doctor: create then delete a tiny probe file inside _generated/.

        Drive is NOT read-after-write consistent on Shared Drives: a delete (or get) by id
        immediately after create can 404 even though the create returned an id. The create
        succeeding is what proves write capability; cleanup retries with short backoff and,
        if the id still hasn't propagated, is deferred rather than failed — an orphaned
        13-byte probe file is harmless.
        """
        import time

        from googleapiclient.errors import HttpError

        gen_id = self.generated_folder_id()

        # Sweep leftovers from prior deferred cleanups FIRST — this is what makes
        # "cleanup deferred" a delay, not a slow leak in the bank: every doctor run
        # deletes whatever earlier runs could not.
        swept = 0
        for child in self.list_children(gen_id):
            if child.get("name") == "_hermes_probe.txt":
                try:
                    self.service.files().delete(fileId=child["id"], supportsAllDrives=True).execute()
                    swept += 1
                except HttpError:
                    pass  # still propagating — the next run gets it
        sweep_note = f"; swept {swept} leftover probe file(s)" if swept else ""

        media = MediaIoBaseUploadShim(b"hermes-probe")
        resp = (
            self.service.files()
            .create(
                body={"name": "_hermes_probe.txt", "parents": [gen_id]},
                media_body=media.media(),
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        probe_id = resp["id"]
        for delay in (1, 2, 2, None):  # ~3 retries over ~5 s
            try:
                self.service.files().delete(fileId=probe_id, supportsAllDrives=True).execute()
                return f"write + cleanup ok{sweep_note}"
            except HttpError as e:
                if _http_status(e) == 404:
                    if delay is None:
                        return ("write ok (probe cleanup deferred — Drive propagation lag; "
                                f"the next doctor run sweeps it){sweep_note}")
                    time.sleep(delay)
                    continue
                raise
        return f"write ok{sweep_note}"


class MediaIoBaseUploadShim:
    """Tiny in-memory upload helper for the write probe."""

    def __init__(self, data: bytes):
        self._data = data

    def media(self):
        from googleapiclient.http import MediaIoBaseUpload

        return MediaIoBaseUpload(io.BytesIO(self._data), mimetype="text/plain")
