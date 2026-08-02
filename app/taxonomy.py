"""§4.1 — the Drive folder path IS the tag schema. Pure, unit-tested, no I/O.

The taxonomy is READ-ONLY: HERMES never creates, renames, moves, or deletes anything in
Drive outside `_generated/`. An unrecognised path imports as subject='unknown' with a
warning — never a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetTags:
    layer1: str | None
    layer2: str | None
    medium: str      # photo | video | pdf | mixed | unknown
    subject: str     # loose_blocks | assembled_blocks | parent_child | child_face |
                     # classroom | lesson_book | lesson_pdf | ugc | unknown
    has_person: bool | None  # None = unknown (UGC / unrecognised)


# Normalized folder path (relative to the bank root) → tags. Exactly the §4.1 table.
_TABLE: dict[str, AssetTags] = {
    "raw-photo":              AssetTags("raw-photo", None,           "photo", "loose_blocks",     False),
    "raw-photo/assembled":    AssetTags("raw-photo", "assembled",    "photo", "assembled_blocks", False),
    "raw-photo/parent-child": AssetTags("raw-photo", "parent-child", "photo", "parent_child",     True),
    "raw-photo/child-face":   AssetTags("raw-photo", "child-face",   "photo", "child_face",       True),
    "raw-video":              AssetTags("raw-video", None,           "video", "loose_blocks",     False),
    "raw-video/assembled":    AssetTags("raw-video", "assembled",    "video", "assembled_blocks", False),
    "raw-video/parent-child": AssetTags("raw-video", "parent-child", "video", "parent_child",     True),
    "raw-video/child-face":   AssetTags("raw-video", "child-face",   "video", "child_face",       True),
    "classroom":              AssetTags("classroom", None,           "photo", "classroom",        True),
    "lesson-books":           AssetTags("lesson-books", None,        "photo", "lesson_book",      False),
    "lesson-pdfs":            AssetTags("lesson-pdfs", None,         "pdf",   "lesson_pdf",       False),
    "UGC":                    AssetTags("UGC", None,                 "mixed", "ugc",              None),
}

# Valid targets for a PARK wishlist entry — the bank's own vocabulary.
WISHLIST_FOLDERS: tuple[str, ...] = tuple(_TABLE.keys())

# The eleven folders `hermes doctor` expects to exist (plus `_generated`, which HERMES owns).
EXPECTED_FOLDERS: tuple[str, ...] = WISHLIST_FOLDERS
GENERATED_FOLDER = "_generated"

_UNKNOWN = AssetTags(None, None, "unknown", "unknown", None)


def normalize_path(path: str) -> str:
    """'Artec Assets Bank/raw-photo/assembled/' → 'raw-photo/assembled'."""
    p = path.replace("\\", "/").strip("/")
    parts = [seg for seg in p.split("/") if seg]
    if parts and parts[0] == "Artec Assets Bank":
        parts = parts[1:]
    return "/".join(parts)


def path_to_tags(folder_path: str) -> AssetTags:
    """Derive tags from the folder that CONTAINS a file. Exact string match on the
    normalized path — renaming a taxonomy folder silently breaks matching, which is why
    the folders must never be renamed (docs/ASSET_BANK.md)."""
    key = normalize_path(folder_path)
    tags = _TABLE.get(key)
    if tags is None:
        log.warning("unrecognised bank path %r — imported as subject='unknown'", folder_path)
        return _UNKNOWN
    return tags
