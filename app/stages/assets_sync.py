"""§4.3 `hermes assets sync` — the only Drive read path. Manual command, no scheduler.

Full mode walks the whole tree; incremental mode replays the Drive changes API from the
persisted startPageToken. Tags derive from the folder path (app/taxonomy.py). Disappeared
files become status='missing', never deleted rows. Idempotent: upserts on drive_file_id.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config, set_config
from app.models import Asset
from app.taxonomy import GENERATED_FOLDER, path_to_tags


def derive_aspect(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    ratio = width / height
    if ratio < 0.9:
        return "vertical"
    if ratio <= 1.1:
        return "square"
    return "landscape"


def _upsert_file(session: Session, folder_path: str, meta: dict, now: datetime) -> str:
    tags = path_to_tags(folder_path)
    image = meta.get("imageMediaMetadata") or {}
    video = meta.get("videoMediaMetadata") or {}
    width = image.get("width") or video.get("width")
    height = image.get("height") or video.get("height")
    duration_ms = video.get("durationMillis")
    modified = meta.get("modifiedTime")
    row = session.get(Asset, meta["id"])
    values = dict(
        drive_path=f"{folder_path}/{meta['name']}".strip("/"),
        layer1=tags.layer1,
        layer2=tags.layer2,
        medium=tags.medium,
        subject=tags.subject,
        has_person=tags.has_person,
        name=meta.get("name"),
        mime_type=meta.get("mimeType"),
        size_bytes=int(meta["size"]) if meta.get("size") else None,
        md5_checksum=meta.get("md5Checksum"),
        width=int(width) if width else None,
        height=int(height) if height else None,
        duration_s=int(duration_ms) / 1000 if duration_ms else None,
        aspect=derive_aspect(int(width) if width else None, int(height) if height else None),
        description=meta.get("description"),
        drive_modified_time=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
        synced_at=now,
        status="active",
    )
    if row is None:
        session.add(Asset(drive_file_id=meta["id"], **values))
        return "new"
    for k, v in values.items():
        setattr(row, k, v)
    return "updated"


def sync(session: Session, drive, full: bool = False, log=print) -> dict:
    now = datetime.now(UTC)
    token = get_config(session, "drive_page_token", None)
    counts: Counter[str] = Counter()

    if full or not token:
        seen: set[str] = set()
        for folder_path, meta in drive.walk():
            disposition = _upsert_file(session, folder_path, meta, now)
            counts[disposition] += 1
            seen.add(meta["id"])
        for row in session.execute(select(Asset).where(Asset.status == "active")).scalars():
            if row.drive_file_id not in seen:
                row.status = "missing"
                counts["missing"] += 1
        set_config(session, "drive_page_token", drive.get_start_page_token())
    else:
        changes, new_token = drive.list_changes(token)
        for change in changes:
            file_meta = change.get("file")
            if change.get("removed") or (file_meta and file_meta.get("trashed")):
                row = session.get(Asset, change.get("fileId"))
                if row is not None and row.status == "active":
                    row.status = "missing"
                    counts["missing"] += 1
                continue
            if not file_meta or file_meta.get("mimeType") == "application/vnd.google-apps.folder":
                continue
            folder_path = drive.resolve_path(file_meta)
            if not folder_path and folder_path != "":
                continue
            if folder_path.split("/")[0] == GENERATED_FOLDER:
                continue  # HERMES output is not bank inventory
            counts[_upsert_file(session, folder_path, file_meta, now)] += 1
        set_config(session, "drive_page_token", new_token)

    session.flush()

    # Summary table: count per Layer 1 / Layer 2 folder, plus new-since-last-sync.
    inventory: Counter[str] = Counter()
    for row in session.execute(select(Asset).where(Asset.status == "active")).scalars():
        key = f"{row.layer1 or '?'}/{row.layer2}" if row.layer2 else (row.layer1 or "?")
        inventory[key] += 1
    log("asset bank inventory:")
    for folder, n in sorted(inventory.items()):
        log(f"  {folder:<28} {n}")
    log(f"sync: {counts.get('new', 0)} new, {counts.get('updated', 0)} updated, {counts.get('missing', 0)} missing")
    return {"counts": dict(counts), "inventory": dict(inventory)}
