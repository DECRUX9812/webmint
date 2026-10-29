"""webmint — turn a website into a deterministic, verified tool. Model-agnostic.

register(ctx) wires four tools; the model call goes through ctx.llm, so whatever
provider/model Hermes is configured with does the minting.
"""
from __future__ import annotations

from .wm_tools import ls, mint, rm, run, setup

_TOOLS = [
    ("webmint_mint", "webmint", {
        "name": "webmint_mint",
        "description": (
            "Mint a deterministic, verified tool from a website. Opens the URL with a real "
            "browser, records it, asks the configured model to write a declarative extraction "
            "spec, executes it twice, and only saves it if both runs agree. Use this when a "
            "site task is repeatable (search, listings, stock, prices) and browser automation "
            "keeps re-improvising. Uses a fresh signed-out browser; public http(s) URLs only."),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page to capture."},
                "task": {"type": "string", "description": "One sentence: what the tool should return."},
                "name": {"type": "string", "description": "Optional snake_case tool name."},
                "example_params": {"type": "object", "description": "Example input used to prove the spec, e.g. {\"query\": \"dune\"}."},
                "actions": {"type": "array", "description": "Optional browser actions before capture: [{type:fill,selector,value},{type:click,selector},{type:wait,ms}]."},
                "model": {"type": "string", "description": "Optional model override for the minting call (host must allow it)."},
            },
            "required": ["url", "task"],
        },
    }, mint),
    ("webmint_run", "webmint", {
        "name": "webmint_run",
        "description": "Run a minted webmint tool. No model is involved at run time.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Minted tool name."},
                "params": {"type": "object", "description": "Inputs matching the tool's input_schema."},
            },
            "required": ["name"],
        },
    }, run),
    ("webmint_setup", "webmint", {
        "name": "webmint_setup",
        "description": (
            "Check (and if needed install) the browser toolchain webmint needs: Node >= 18, "
            "playwright, and Chromium. Idempotent; downloads about 120 MB once on a fresh "
            "machine. Pass check_only to just report status."),
        "parameters": {
            "type": "object",
            "properties": {
                "check_only": {"type": "boolean", "description": "Report status without installing anything."},
            },
        },
    }, setup),
    ("webmint_list", "webmint", {
        "name": "webmint_list",
        "description": "List minted webmint tools with kind, site, and whether they are verified.",
        "parameters": {"type": "object", "properties": {}},
    }, ls),
    ("webmint_remove", "webmint", {
        "name": "webmint_remove",
        "description": "Delete a minted webmint tool and its capture.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }, rm),
]


def register(ctx):
    for name, toolset, schema, handler in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=(lambda h: (lambda args, **kw: h(args, ctx=ctx, **kw)))(handler),
        )
