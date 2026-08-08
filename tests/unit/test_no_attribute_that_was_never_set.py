"""An attribute nobody ever set, on the one line nothing could reach.

    2026-08-08T19:00:13Z  scheduler: job 8 pg-dump due at 03:00
    2026-08-08T19:00:13Z  job 8 pg-dump FAILED: AttributeError:
                          'DriveClient' object has no attribute 'settings'

`DriveClient.__init__` stores `self.root_id`, and the folder walk and the generated-folder
lookup both use it. `upload_backup` — alone in the file — read `self.settings`, which is
never assigned anywhere. So the nightly dump's upload had never completed once.

WHY IT SURVIVED SO LONG: it was unreachable. `run_backup` calls `_require("pg_dump")` first,
and pg_dump was missing from the image, so job 8 failed one line earlier every night for
weeks. Fixing the packaging bug moved the failure forward onto this one. **A bug behind a
bug is invisible for exactly as long as the first one lasts**, which is why the fix that
finally lets a path run is the moment to expect the next defect rather than declare victory.

THE GUARD, and why it is this shape rather than a test of `upload_backup`: exercising that
method needs real Google credentials, so a behavioural test would have been skipped in CI
and caught nothing. This asks a question that needs no credentials and no network — does
every `self.X` a class READS actually get SET somewhere in that class? A name that is only
ever read is an AttributeError waiting for the first caller to reach it, and it is exactly
as invisible as this one was.

WHAT SUPPLIES EACH SIDE: the reads come from the AST, the writes come from the AST, and
neither is derived from the other or from a test author remembering to check.
"""

from __future__ import annotations

import ast

# Only the integration clients. Narrow on purpose: these are the classes whose methods are
# reached late (a nightly job, a monthly restore, an operator command), so a typo'd attribute
# in one hides for weeks. Widening this to all of app/ would add noise from mixins and
# dynamic attributes without adding signal.
SCANNED = "app/integrations"


def _unset_attribute_reads(tree: ast.ClassDef) -> set[str]:
    """Names read as `self.X` that are never assigned, never a method, never a class var."""
    assigned: set[str] = set()
    read: set[str] = set()

    for node in ast.walk(tree):
        # `self.x = ...`, `self.x += ...`, `self.x: T = ...`
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                assigned.add(target.attr)
        # `setattr(self, "x", ...)` — dynamic, but still a definition
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)):
            assigned.add(str(node.args[1].value))
        # `self.x` in a load context
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            read.add(node.attr)

    defined = {n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:                                  # class-level constants
        if isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    return read - assigned - defined


def test_no_integration_client_reads_an_attribute_it_never_sets(repo_root):
    """THE REGRESSION. `DriveClient.upload_backup` read `self.settings`; `__init__` sets
    `self.root_id`. Nothing in the file ever assigned `settings`, and the method was
    unreachable behind a missing pg_dump, so the mistake shipped and waited."""
    offenders: dict[str, set[str]] = {}
    for path in sorted((repo_root / SCANNED).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # A subclass may legitimately read what its parent sets.
            if node.bases:
                continue
            missing = _unset_attribute_reads(node)
            if missing:
                offenders[f"{path.relative_to(repo_root)}::{node.name}"] = missing

    assert not offenders, (
        f"attributes read but never set: {offenders}. Each is an AttributeError waiting for "
        "the first caller that reaches that line — which for a nightly job or a monthly "
        "restore can be weeks after the typo shipped.")


def test_the_guard_actually_catches_the_bug_it_was_written_for():
    """A guard that cannot fail is the thing this codebase keeps finding. Feed it the exact
    shape of the DriveClient defect and confirm it objects."""
    tree = ast.parse(
        "class C:\n"
        "    def __init__(self, settings):\n"
        "        self.root_id = settings.ROOT\n"
        "    def upload(self):\n"
        "        return self.settings.ROOT\n"
    )
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    assert _unset_attribute_reads(cls) == {"settings"}


def test_the_guard_does_not_object_to_the_corrected_version():
    """And passes once the read matches what __init__ actually sets — otherwise it would be
    unsatisfiable, which is its own kind of useless."""
    tree = ast.parse(
        "class C:\n"
        "    def __init__(self, settings):\n"
        "        self.root_id = settings.ROOT\n"
        "    def upload(self):\n"
        "        return self.root_id\n"
    )
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    assert _unset_attribute_reads(cls) == set()


def test_methods_and_class_constants_are_not_reported():
    """`self.helper()` and `self.TIMEOUT` are defined at class level, not in __init__."""
    tree = ast.parse(
        "class C:\n"
        "    TIMEOUT = 30\n"
        "    def helper(self):\n"
        "        return 1\n"
        "    def go(self):\n"
        "        return self.helper() + self.TIMEOUT\n"
    )
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    assert _unset_attribute_reads(cls) == set()
