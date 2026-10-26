"""Storage + spec helpers for the webmint plugin.

A minted tool is a directory under ``$HERMES_HOME/webmint/tools/<name>/``:

    tool.json     the replay spec (declarative, no model at run time)
    capture/      the recording it was built from (capture.json, page.html)
    meta.json     provenance: model, timings, verification result, sha

Nothing here imports Hermes core, so it is unit-testable on its own.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


def hermes_home() -> str:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def root() -> str:
    p = os.path.join(hermes_home(), "webmint")
    os.makedirs(os.path.join(p, "tools"), exist_ok=True)
    return p


def tool_dir(name: str) -> str:
    return os.path.join(root(), "tools", name)


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def save_tool(spec: Dict[str, Any], *, capture_dir: Optional[str] = None,
              meta: Optional[Dict[str, Any]] = None) -> str:
    name = spec["name"]
    if not valid_name(name):
        raise ValueError("tool name must be lowercase letters, digits and underscores")
    d = tool_dir(name)
    os.makedirs(d, exist_ok=True)
    spec = dict(spec)
    spec.setdefault("version", 1)
    spec.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    if meta:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
    # keep a pointer to the capture, copying the two small files only
    if capture_dir and os.path.isdir(capture_dir):
        cdir = os.path.join(d, "capture")
        os.makedirs(cdir, exist_ok=True)
        for fn in ("capture.json",):
            src = os.path.join(capture_dir, fn)
            if os.path.exists(src):
                with open(src, "r", encoding="utf-8") as s, open(os.path.join(cdir, fn), "w", encoding="utf-8") as t:
                    t.write(s.read()[:400000])
    return d


def load_tool(name: str) -> Dict[str, Any]:
    p = os.path.join(tool_dir(name), "tool.json")
    if not os.path.exists(p):
        raise FileNotFoundError(f"no minted tool named {name!r}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def list_tools() -> List[Dict[str, Any]]:
    base = os.path.join(root(), "tools")
    out = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name, "tool.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                spec = json.load(f)
            meta_p = os.path.join(base, name, "meta.json")
            meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
        except Exception:
            continue
        out.append({
            "name": name,
            "kind": spec.get("kind"),
            "site": spec.get("site"),
            "task": spec.get("task"),
            "effect": spec.get("effect", "read"),
            "verified": bool(meta.get("verified")),
            "created_at": spec.get("created_at"),
        })
    return out


def remove_tool(name: str) -> bool:
    if not valid_name(name):
        return False
    import shutil
    d = tool_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False


def render_template(template: str, params: Dict[str, Any]) -> str:
    """Fill {key} from params, percent-encoding values. Unknown keys stay literal."""
    def sub(m):
        k = m.group(1)
        if k in params and params[k] is not None:
            from urllib.parse import quote
            return quote(str(params[k]), safe="")
        return m.group(0)
    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", sub, template or "")
