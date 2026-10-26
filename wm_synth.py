"""Model-facing half of webmint: turn a capture into a declarative replay spec.

The model never writes code. It reads a pruned page (or a captured JSON response)
and fills in a fixed schema: where the repeatable items live, and how to read the
fields. That keeps the artifact auditable and makes weak models usable — the job is
comprehension, not programming.

Model access goes through ``ctx.llm`` (the host's own routing), so whatever model
Hermes is configured with is the model that mints.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "kind", "url_template", "reasoning", "input_schema", "output_fields", "fields"],
    "properties": {
        "name": {"type": "string", "description": "snake_case tool name, a-z0-9_"},
        "task": {"type": "string", "description": "one sentence: what this tool returns"},
        "kind": {"type": "string", "enum": ["dom", "http"]},
        "url_template": {"type": "string", "description": "URL with {param} placeholders"},
        "item_selector": {"type": "string", "description": "dom only: CSS selector matching one result row"},
        "fields": {
            "type": "object",
            "description": 'dom only: field -> "css" or "css@attr"',
            "additionalProperties": {"type": "string"},
        },
        "wait_selector": {"type": "string"},
        "limit": {"type": "integer"},
        "http": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body_template": {"type": "string"},
                "items_path": {"type": "string", "description": "dot path in the JSON to the array"},
                "field_paths": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "properties": {"type": "object"},
                "required": {"type": "array", "items": {"type": "string"}},
            },
        },
        "output_fields": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string", "description": "why this selector/path was chosen, max 300 chars"},
    },
}

INSTRUCTIONS = """You are writing a deterministic extraction spec for a website, from a capture of that page.

You never write code. You fill in a JSON object that a fixed runner executes: it loads
url_template, finds elements matching item_selector, and reads each field with a CSS
selector (or "css@attr" for an attribute such as href/src).

Rules:
- Prefer kind="dom" unless the capture shows a JSON endpoint that already returns the
  finished records; use kind="http" with items_path + field_paths then.
- item_selector must match EVERY result row and NOTHING else (not the container, not the
  header). Prefer stable class/id chains over :nth-child.
- Field expressions may start with '+' (resolve from the item's next sibling) or '^' (from its
  parent) before the CSS selector — use this when one record spans sibling elements, e.g. a
  <dt> row whose title and authors live in the following <dd>.
- Fields must be null-safe: if a row lacks the element, the runner returns null — that is
  fine, but never point at a selector that can pick up navigation or ads.
- url_template keeps the search parameter as {name} placeholders matching input_schema.
- input_schema is a JSON Schema object: {"properties": {...}, "required": [...]}.
- output_fields lists the keys each item will have.
- fields is REQUIRED for kind=dom (field -> "css" or "css@attr"); for kind=http set fields to {} and use http.field_paths.
- reasoning: one short paragraph, no markup.
Return only the JSON object."""


def _strip_html(html: str, cap: int = 55000) -> str:
    h = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    h = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", h)
    h = re.sub(r"(?is)<svg[^>]*>.*?</svg>", " ", h)
    h = re.sub(r"(?s)<!--.*?-->", " ", h)
    h = re.sub(r"(?s)\s+", " ", h)
    return h[:cap]


def build_input(capture: Dict[str, Any], html: str, task: str, url: str, html_cap: int = 55000) -> List[Dict[str, str]]:
    cands = (capture.get("candidates") or [])[: (6 if html_cap > 20000 else 2)]
    cand_txt = "\n".join(
        f"- {c.get('method')} {c.get('status')} {c.get('contentType','')} {c.get('url')}\n"
        f"  head: {(c.get('bodyHead') or '')[:700]}"
        for c in cands
    ) or "(no JSON/XHR responses captured — this page is server-rendered; use kind=dom)"
    return [{
        "type": "text",
        "text": (
            f"TASK: {task}\nPAGE: {url}\nFINAL: {capture.get('finalUrl')}\n"
            f"TITLE: {capture.get('title')}\n\n"
            f"CAPTURED JSON/XHR CANDIDATES:\n{cand_txt}\n\n"
            f"PAGE HTML (scripts/styles removed):\n{_strip_html(html, html_cap)}"
        ),
    }]


def ask_model(ctx: Any, *, task: str, url: str, capture: Dict[str, Any], html: str,
              feedback: Optional[str] = None, model: Optional[str] = None,
              html_cap: int = 55000) -> Dict[str, Any]:
    inputs = build_input(capture, html, task, url, html_cap)
    if feedback:
        inputs.append({"type": "text", "text": f"YOUR PREVIOUS SPEC FAILED:\n{feedback}\nFix it."})
    kw = {"instructions": INSTRUCTIONS, "input": inputs, "json_schema": SCHEMA,
          "json_mode": True, "schema_name": "extraction_spec", "purpose": "webmint"}
    if model:
        kw["model"] = model
    try:
        res = ctx.llm.complete_structured(**kw)
        raw = getattr(res, "text", "") or ""
        parsed = getattr(res, "parsed", None)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        # Some providers reject response_format/json_schema outright (400 with no detail).
        # Fall back to a plain completion that is told, in words, to answer with JSON only.
        text = "\n\n".join(b.get("text", "") for b in inputs)
        plain = [
            {"role": "system", "content": INSTRUCTIONS + "\n\nReturn ONLY the JSON object, no prose, no code fences."},
            {"role": "user", "content": text},
        ]
        call = {"messages": plain, "purpose": "webmint"}
        if model:
            call["model"] = model
        res = ctx.llm.complete(**call)
        raw = getattr(res, "text", "") or ""
        if not raw:
            raise e
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def validate(spec: Dict[str, Any]) -> Tuple[bool, str]:
    name = spec.get("name") or ""
    if not re.match(r"^[a-z][a-z0-9_]{1,47}$", name):
        return False, f"name {name!r} must be snake_case (a-z, 0-9, _)"
    kind = spec.get("kind")
    if kind not in ("dom", "http"):
        return False, "kind must be dom or http"
    if not spec.get("url_template"):
        return False, "url_template is required"
    if kind == "dom":
        if not spec.get("item_selector"):
            return False, "dom spec needs item_selector"
        if not isinstance(spec.get("fields"), dict) or not spec["fields"]:
            return False, "dom spec needs a non-empty fields map"
    else:
        http = spec.get("http") or {}
        if not http.get("items_path"):
            return False, "http spec needs http.items_path"
        if not isinstance(http.get("field_paths"), dict) or not http["field_paths"]:
            return False, "http spec needs http.field_paths"
    if not isinstance(spec.get("input_schema"), dict):
        return False, "input_schema must be an object"
    return True, ""


_KEYWORDS = ("query", "q", "search", "term", "keyword", "site", "domain", "location",
             "zip", "city", "user", "username", "subreddit", "category", "topic", "lang")


def example_params(spec: Dict[str, Any], captured_url: str = "") -> Dict[str, Any]:
    """Best-effort example input, so verification runs with real values.

    Priority: an example already present in the capture URL's query string (the user
    literally used it), then the schema's own examples/enums, then a keyword default.
    """
    from urllib.parse import parse_qs, urlparse
    props = ((spec.get("input_schema") or {}).get("properties") or {})
    q = parse_qs(urlparse(captured_url or "").query)
    out = {}
    for k, v in props.items():
        if k in q and q[k] and q[k][0]:
            out[k] = q[k][0]
        elif isinstance(v, dict) and v.get("examples"):
            out[k] = v["examples"][0]
        elif isinstance(v, dict) and v.get("enum"):
            out[k] = v["enum"][0]
        elif k in _KEYWORDS:
            out[k] = "github.com" if k in ("site", "domain") else "test"
    return out
