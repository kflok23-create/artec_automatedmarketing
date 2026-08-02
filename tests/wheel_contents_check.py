"""Inspect the BUILT wheel for every runtime resource. Run after `uv build --wheel`:

    uv build --wheel && python tests/wheel_contents_check.py

This exercises the installed artifact itself — the unit tests import from the source tree,
which is exactly the blind spot that let the fonts and prompts packaging bugs reach
production. CI runs this on every push; not collected by pytest (no test_ prefix)."""

from __future__ import annotations

import glob
import sys
import zipfile

REQUIRED = [
    # (prefix, suffix, minimum count)
    ("app/prompts/", ".md", 5),
    ("app/assets/fonts/", ".ttf", 4),
    ("app/migrations/versions/", ".py", 1),
    ("app/migrations/", "env.py", 1),
]


def main() -> int:
    wheels = sorted(glob.glob("dist/*.whl"))
    if not wheels:
        print("FAIL: no wheel in dist/ — run `uv build --wheel` first")
        return 1
    wheel = wheels[-1]
    names = zipfile.ZipFile(wheel).namelist()
    failures = []
    for prefix, suffix, minimum in REQUIRED:
        hits = [n for n in names if n.startswith(prefix) and n.endswith(suffix)]
        status = "ok" if len(hits) >= minimum else "MISSING"
        print(f"  {status:8} {prefix}*{suffix}  ({len(hits)} found, {minimum} required)")
        if len(hits) < minimum:
            failures.append(prefix + "*" + suffix)
    if failures:
        print(f"FAIL: {wheel} is missing runtime resources: {failures}")
        print("Every runtime resource must live inside the app/ package (hatchling ships only app/).")
        return 1
    print(f"OK: {wheel} carries all runtime resources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
