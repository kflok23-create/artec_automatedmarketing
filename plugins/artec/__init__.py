"""artec — hermes-agent plugin entry point.

Contract per https://hermes-agent.nousresearch.com/docs/developer-guide/plugins:
`register(ctx)` is called exactly once at startup; if it crashes, the plugin disables
gracefully. Tools register with ctx.register_tool(name, toolset, schema, handler);
handlers follow (args: dict, **kwargs) -> str returning JSON always.

Enablement is opt-in: `hermes plugins enable artec`. Verify discovery with:
    HERMES_PLUGINS_DEBUG=1 hermes plugins list
"""

from __future__ import annotations

import importlib.util
import pathlib


def _load_sibling(module_name: str):
    """Import schemas/tools whether we are loaded as a package (relative import works) or
    as a bare directory scan (fall back to file-path loading)."""
    try:
        from importlib import import_module

        return import_module(f".{module_name}", package=__package__ or "artec")
    except (ImportError, TypeError):
        path = pathlib.Path(__file__).resolve().parent / f"{module_name}.py"
        spec = importlib.util.spec_from_file_location(f"artec_plugin_{module_name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def register(ctx) -> None:
    schemas = _load_sibling("schemas")
    tools = _load_sibling("tools")

    for name, handler in tools.HANDLERS.items():
        ctx.register_tool(
            name=name,
            toolset="artec",
            schema=schemas.SCHEMAS[name],
            handler=handler,
        )

    # Guardrail (defense in depth beside agent.disabled_toolsets): block file-write and
    # shell tool calls for this profile at the hook layer.
    ctx.register_hook("pre_tool_call", tools.pre_tool_call)
