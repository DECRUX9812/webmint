"""Runtime half of webmint: execute a minted spec with no model in the loop."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _node_candidates():
    """Node >= 18, in preference order. Env first, then PATH, then common prefixes."""
    yield os.environ.get("WEBMINT_NODE")
    yield os.path.expanduser("~/.local/node24/bin/node")
    yield shutil.which("node")
    for p in ("/usr/local/bin/node", "/usr/bin/node"):
        yield p


def find_node() -> str:
    for c in _node_candidates():
        if not c or not os.path.exists(c):
            continue
        try:
            v = subprocess.run([c, "-v"], capture_output=True, text=True, timeout=10).stdout.strip()
            major = int(v.lstrip("v").split(".")[0])
            if major >= 18:
                return c
        except Exception:
            continue
    return ""


def _pw_candidates():
    yield os.environ.get("WEBMINT_PW_MODULES")
    yield os.path.join(PLUGIN_DIR, "node_modules")
    yield os.path.expanduser("~/.local/pomerado/lib/node_modules/pomerado/node_modules")
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=15).stdout.strip()
        if root:
            yield root
    except Exception:
        pass


def find_playwright() -> str:
    for p in _pw_candidates():
        if p and os.path.isdir(os.path.join(p, "playwright")):
            return p
    return ""


NODE_BIN = find_node()
PW_MODULES = find_playwright()


def refresh() -> None:
    """Re-run discovery after a provisioning step."""
    global NODE_BIN, PW_MODULES
    NODE_BIN = find_node()
    PW_MODULES = find_playwright()
PLAYWRIGHT_HINT = ("playwright is not installed for this plugin. Either 'pip install playwright && "
                   "python -m playwright install chromium', or 'npm i playwright' inside the plugin "
                   "directory, or set WEBMINT_PW_MODULES to a node_modules dir containing playwright.")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/153.0.0.0 Safari/537.36")


def _fill(tpl: str, params: Dict[str, Any]) -> str:
    import re
    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
                  lambda m: urllib.parse.quote(str(params[m.group(1)]), safe="")
                  if params.get(m.group(1)) is not None else m.group(0),
                  tpl or "")


def _node_env() -> Dict[str, str]:
    env = dict(os.environ)
    node_dir = os.path.dirname(NODE_BIN or "node")
    env["PATH"] = node_dir + ":" + env.get("PATH", "")
    env["NODE_PATH"] = PW_MODULES
    return env


def run_dom(spec: Dict[str, Any], params: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    if not NODE_BIN:
        return {"ok": False, "error": "node >= 18 not found; set WEBMINT_NODE"}
    if not PW_MODULES:
        return {"ok": False, "error": PLAYWRIGHT_HINT}
    import tempfile
    fh, spec_path = tempfile.mkstemp(prefix="webmint-spec-", suffix=".json")
    try:
        with os.fdopen(fh, "w", encoding="utf-8") as f:
            json.dump({**spec, "limit": spec.get("limit") or 25}, f)
        cmd = [NODE_BIN, os.path.join(PLUGIN_DIR, "wm_extract.cjs"),
               "--spec", spec_path, "--params", json.dumps(params)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_node_env())
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"extraction timed out after {timeout}s"}
    finally:
        with open(spec_path, "w", encoding="utf-8") as f:  # never leave a stale spec file
            f.write("")
    out = (p.stdout or "").strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {"ok": False, "error": (p.stderr or p.stdout or "no output")[:600]}


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if part.isdigit() and isinstance(cur, list):
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def run_http(spec: Dict[str, Any], params: Dict[str, Any], timeout: int = 45) -> Dict[str, Any]:
    http = spec.get("http") or {}
    url = _fill(spec.get("url_template", ""), params)
    body = http.get("body_template")
    if body:
        body = _fill(body, params).encode()
    headers = {"user-agent": UA, "accept": "application/json, text/plain, */*"}
    for k, v in (http.get("headers") or {}).items():
        if k.lower() in ("cookie", "authorization"):
            continue  # never replay secrets
        headers[k] = _fill(str(v), params)
    req = urllib.request.Request(url, data=body, headers=headers, method=(http.get("method") or "GET").upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"ok": False, "error": "response was not JSON", "url": url, "status": status}
    items_raw = _dig(data, http.get("items_path", "")) or []
    if isinstance(items_raw, dict):
        items_raw = [items_raw]
    items = []
    for row in items_raw[: int(spec.get("limit") or 25)]:
        item = {}
        for k, path in (http.get("field_paths") or {}).items():
            item[k] = _dig(row, path)
        items.append(item)
    return {"ok": True, "url": url, "status": status, "count": len(items), "items": items}


def run_spec(spec: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    if spec.get("kind") == "http":
        return run_http(spec, params)
    return run_dom(spec, params)


def verify(spec: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Run twice; the tool only ships if both runs agree on shape and have rows."""
    first = run_spec(spec, params)
    if not first.get("ok"):
        return {"ok": False, "stage": "run1", "error": first.get("error"), "result": first}
    rows = first.get("items") or first.get("count") or []
    n1 = first.get("count", len(rows) if isinstance(rows, list) else 0)
    if not n1:
        return {"ok": False, "stage": "run1", "error": "extracted 0 items",
                "sample": (rows or [])[:3], "result": first}
    second = run_spec(spec, params)
    n2 = second.get("count", 0)
    keys1 = sorted((first.get("items") or [{}])[0].keys())
    keys2 = sorted((second.get("items") or [{}])[0].keys())
    if not second.get("ok") or n2 == 0:
        return {"ok": False, "stage": "run2", "error": second.get("error") or "second run returned nothing",
                "result": second}
    if keys1 != keys2:
        return {"ok": False, "stage": "shape", "error": f"field set changed between runs: {keys1} vs {keys2}"}
    # Fill-rate gate: a spec that returns rows of nulls is worse than a failure, because it
    # looks successful. Every declared field must be present in most rows.
    items1 = first.get("items") or []
    fill = {}
    for k in keys1:
        got = sum(1 for it in items1 if (it or {}).get(k) not in (None, "", []))
        fill[k] = round(100.0 * got / max(1, len(items1)))
    thin = {k: v for k, v in fill.items() if v < 50}
    if thin:
        return {"ok": False, "stage": "quality",
                "error": f"fields empty in most rows: {thin}",
                "fill": fill, "sample": items1[:3]}
    return {"ok": True, "runs": [n1, n2], "fields": keys1, "fill": fill,
            "filled_fields": sum(1 for v in fill.values() if v >= 50),
            "sample": items1[:3]}
