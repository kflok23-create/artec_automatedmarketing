"""Text-card colour contract, pairing rotation, and acceptance 22 (dry-run cycle)."""

from app.config import OPERATOR_CONSTANTS, get_config
from app.toolbox.text_card import next_pairing

APPROVED = {("#0168B7", "#F5F3EE"), ("#014E8B", "#F5F3EE"), ("#E8A840", "#12212F")}


def test_only_three_locked_pairings_and_amber_never_light():
    pairings = OPERATOR_CONSTANTS["text_card_pairings"]
    assert {(p["bg"], p["text"]) for p in pairings} == APPROVED
    for p in pairings:
        if p["bg"] == "#E8A840":
            assert p["text"] == "#12212F", "never light text on amber"


def test_pairing_rotation_never_repeats_consecutively(session):
    seen = [next_pairing(session)["bg"] for _ in range(6)]
    for a, b in zip(seen, seen[1:], strict=False):
        assert a != b
    assert set(seen) == {p["bg"] for p in get_config(session, "text_card_pairings")}


def test_fonts_ship_inside_the_app_package():
    # The wheel packages only `app/` (hatchling packages=["app"]). FONTS_DIR must resolve
    # from the INSTALLED package, so the fonts live at app/assets/fonts/ — a repo-root
    # assets/ path silently vanishes under `pip install .` (that was a live doctor RED).
    from pathlib import Path

    import app
    from app.toolbox.text_card import FONTS_DIR

    pkg_root = Path(app.__file__).resolve().parent
    assert FONTS_DIR == pkg_root / "assets" / "fonts"
    missing = [f for f in set(OPERATOR_CONSTANTS["fonts"].values())
               if not (FONTS_DIR / f).exists()]
    assert not missing, f"fonts missing from the package: {missing}"


def test_cycle_dry_run_green():
    from app.stages.cycle import cycle_dry_run

    assert cycle_dry_run(log=lambda *_: None) == {"ok": True}  # 22
