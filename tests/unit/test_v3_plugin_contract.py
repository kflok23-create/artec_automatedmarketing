"""The hermes-agent plugin contract, tested at the boundary that failed in production:
layout, manifest, register(ctx), schema shape, hook behaviour — and, where the hermes
binary exists, the DOCUMENTED discovery check itself (HERMES_PLUGINS_DEBUG=1 hermes
plugins list). Contract source:
https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
"""

import importlib.util
import os
import re
import shutil
import subprocess

import pytest

# v4: the seam grew from six to FIFTEEN. The security property was never the count — it is
# the absence of capability: no tool writes orders, events or config, and metrics are
# writable by transcription only (asserted in test_v4_seam.py).
SIX = ("read_brief", "read_learnings", "read_asset_inventory", "read_parked_posts",
       "write_plan", "record_gate_decision")
V4_ADDED = ("read_draft_posts", "read_digest", "deliver_video", "review_video",
            "review_email", "record_metrics", "retry_post", "fulfil_wishlist",
            "acknowledge_price_table")
FIFTEEN = SIX + V4_ADDED


@pytest.fixture
def plugin_dir(repo_root):
    return repo_root / "plugins" / "artec"


def _load(plugin_dir, module):
    spec = importlib.util.spec_from_file_location(f"artec_pc_{module}", plugin_dir / f"{module}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_documented_layout(plugin_dir):
    # A bare .py is not a plugin (that shipped, silently). Required: a directory holding
    # plugin.yaml AND __init__.py with register(ctx); one nesting level max.
    assert (plugin_dir / "plugin.yaml").is_file()
    assert (plugin_dir / "__init__.py").is_file()
    init_src = (plugin_dir / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r"def register\(ctx\)", init_src)
    rel = plugin_dir.relative_to(plugin_dir.parent.parent)
    assert len(rel.parts) == 2, "plugins/artec — flat layout, within the one-level nesting max"


def test_manifest_declares_all_fifteen_tools(plugin_dir):
    manifest = (plugin_dir / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(r"^name:\s*artec\s*$", manifest, re.MULTILINE)
    tools_block = manifest.split("provides_tools:")[1].split("provides_hooks:")[0]
    declared = re.findall(r"-\s*(\w+)", tools_block)
    assert set(declared) == set(FIFTEEN)
    assert len(declared) == 15


class FakeCtx:
    """Records registrations the way hermes-agent's plugin context receives them."""

    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, object] = {}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, override=False):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler,
                            "override": override}

    def register_hook(self, hook_name, callback):
        self.hooks[hook_name] = callback


@pytest.fixture
def ctx(plugin_dir):
    init = _load(plugin_dir, "__init__")
    fake = FakeCtx()
    init.register(fake)
    return fake


def test_register_installs_all_fifteen_tools_with_documented_schemas(ctx):
    assert set(ctx.tools) == set(FIFTEEN)
    for name, entry in ctx.tools.items():
        assert entry["toolset"] == "artec"
        assert entry["override"] is False
        schema = entry["schema"]
        assert schema["name"] == name                      # documented schema format
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
        assert isinstance(schema["parameters"]["required"], list)
        assert callable(entry["handler"])


def test_registered_handlers_honour_the_string_contract(ctx, engine):
    # Through the exact callables hermes-agent would hold: str out, no raise, even on junk.
    import json

    for name, entry in ctx.tools.items():
        raw = entry["handler"]({}, engine=engine)  # empty args — several tools need args
        assert isinstance(raw, str), f"{name} returned {type(raw)}, contract is str"
        json.loads(raw)  # and it is JSON, success or error


def test_pre_tool_call_hook_blocks_shell_and_file_write(ctx):
    hook = ctx.hooks["pre_tool_call"]
    for dangerous in ("terminal", "write_file", "patch", "code_execution", "run_python"):
        verdict = hook(dangerous, args={}, task_id="t1")
        assert verdict and verdict["action"] == "block", f"{dangerous} must be blocked"
    for ours in SIX:
        assert hook(ours, args={}, task_id="t1") is None   # the six pass through
    assert hook("web_search", args={}, task_id="t1") is None  # trend scouting survives


HAS_HERMES = shutil.which("hermes") is not None


@pytest.mark.skipif(not HAS_HERMES, reason="hermes-agent binary not installed here — "
                    "this check runs wherever the agent is installed (the hermes-brain "
                    "entrypoint runs it on every boot and hard-fails without artec)")
def test_hermes_discovers_artec_with_all_tools(plugin_dir, tmp_path):
    # The documented discovery check — the one that would have caught the loose-file bug.
    # Verified against a real hermes-agent install: `plugins list` proves discovery +
    # enablement; tool registration happens at gateway startup via register(ctx), which
    # we invoke below exactly as the agent does.
    home = tmp_path / "hermes-home"
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(plugin_dir, home / "plugins" / "artec")
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_PLUGINS_DEBUG="1")
    subprocess.run(["hermes", "plugins", "enable", "artec"], env=env, capture_output=True,
                   text=True, timeout=120)
    out = subprocess.run(["hermes", "plugins", "list", "--plain", "--no-bundled"],
                         env=env, capture_output=True, text=True, timeout=120)
    listing = out.stdout + out.stderr
    assert "artec" in listing, f"artec plugin not discovered:\n{listing}"
    artec_line = next(line for line in listing.splitlines() if "artec" in line)
    assert "enabled" in artec_line, f"artec discovered but not enabled: {artec_line!r}"

    # All fifteen registered — through the same register(ctx) call the gateway makes.
    init = _load(home / "plugins" / "artec", "__init__")
    ctx = FakeCtx()
    init.register(ctx)
    assert set(ctx.tools) == set(FIFTEEN)
