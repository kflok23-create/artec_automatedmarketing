"""Acceptance 1 (spine), 4 (taxonomy), plus the net-CM money math."""

import pytest

from app.config import kill_line_minor, net_cm_minor
from app.spine import build_tracked_url
from app.taxonomy import path_to_tags


def test_spine_social_exact_string():
    url = build_tracked_url("post_1482", "tiktok", "organic")
    assert url == "https://artec.my/?code=SOCIAL50&utm_source=tiktok&utm_medium=organic&utm_campaign=post_1482"


def test_spine_email_exact_string():
    url = build_tracked_url("post_1500", "email", "email")
    assert url == "https://artec.my/?code=EMAIL50&utm_source=email&utm_medium=email&utm_campaign=post_1500"


def test_spine_rejects_bad_medium():
    with pytest.raises(ValueError):
        build_tracked_url("post_1", "tiktok", "paid")


TWELVE = [
    ("raw-photo", "photo", "loose_blocks", False),
    ("raw-photo/assembled", "photo", "assembled_blocks", False),
    ("raw-photo/parent-child", "photo", "parent_child", True),
    ("raw-photo/child-face", "photo", "child_face", True),
    ("raw-video", "video", "loose_blocks", False),
    ("raw-video/assembled", "video", "assembled_blocks", False),
    ("raw-video/parent-child", "video", "parent_child", True),
    ("raw-video/child-face", "video", "child_face", True),
    ("classroom", "photo", "classroom", True),
    ("lesson-books", "photo", "lesson_book", False),
    ("lesson-pdfs", "pdf", "lesson_pdf", False),
    ("UGC", "mixed", "ugc", None),
]


@pytest.mark.parametrize("path,medium,subject,has_person", TWELVE)
def test_taxonomy_all_twelve_paths(path, medium, subject, has_person):
    tags = path_to_tags(path)
    assert (tags.medium, tags.subject, tags.has_person) == (medium, subject, has_person)


def test_taxonomy_root_prefix_and_slashes_normalized():
    assert path_to_tags("Artec Assets Bank/raw-photo/assembled/").subject == "assembled_blocks"


def test_taxonomy_unknown_path_never_guesses():
    tags = path_to_tags("raw-photo/child_face")  # renamed folder: silently different string
    assert tags.subject == "unknown"
    assert tags.has_person is None


def test_net_cm_is_net_not_gross():
    assert net_cm_minor("SGD") == 8400 - 1000 == 7400
    assert net_cm_minor("MYR") == 25200 - 4000 == 21200


def test_net_cm_unknown_currency_raises():
    with pytest.raises(ValueError):
        net_cm_minor("USD")


def test_kill_lines_per_currency():
    assert kill_line_minor("SGD") == 2000
    assert kill_line_minor("MYR") == 6000
