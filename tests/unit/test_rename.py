"""v3 acceptance 1 — this repo declares no `hermes` console_script; `artec` carries every
v2 subcommand. `hermes` refers exclusively to the NousResearch agent CLI."""

import tomllib

from typer.main import get_command

from app.cli import cli


def test_no_hermes_console_script(repo_root):
    with open(repo_root / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    scripts = pyproject["project"]["scripts"]
    assert "hermes" not in scripts, "the `hermes` name belongs to the Nous agent CLI"
    assert scripts["artec"] == "app.cli:main"


def test_artec_carries_every_v2_subcommand():
    root = get_command(cli)
    names = set(root.commands.keys())
    for cmd in ("doctor", "learn", "ideate", "gate", "render", "publish", "measure",
                "report", "cycle", "config", "assets", "wishlist", "post"):
        assert cmd in names, f"artec lost the `{cmd}` subcommand in the rename"
    sub = {g: set(get_command(cli).commands[g].commands.keys())
           for g in ("config", "assets", "wishlist", "post")}
    assert {"seed", "set", "get"} <= sub["config"]
    assert {"sync"} <= sub["assets"]
    assert {"show", "match", "fulfil"} <= sub["wishlist"]
    assert {"retry", "show"} <= sub["post"]
