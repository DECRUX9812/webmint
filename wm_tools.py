"""Tool handlers for webmint. Every handler returns a JSON string."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, Mapping, Optional

from . import wm_run, wm_setup, wm_store, wm_synth, wm_urlsafe

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(PLUGIN_DIR, "wm_capture.cjs")


def _capture(url: str, actions: Optional[list], work: str, wait_ms: int = 3500) -> Dict[str, Any]:
    env = wm_run._node_env()
    cmd = [wm_run.NODE_BIN, CAPTURE, "--url", url, "--out", work, "--wait-ms", str(wait_ms)]
    if actions:
        cmd += ["--actions", json.dumps(actions)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    out = (p.stdout or "").strip()
    if out:
        try:
            return json.loads(out)
        except Exception:
            pass
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"ok": False, "error": (p.stderr or p.stdout or "capture produced no output")[:600]}


def mint(args: Mapping | None = None, ctx=None, **kw) -> str:
    a = dict(args or {})
    url = (a.get("url") or "").strip()
    task = (a.get("task") or "").strip()
    name = (a.get("name") or "").strip().lower().replace("-", "_")
    params = a.get("example_params") or {}
    actions = a.get("actions")
    model = a.get("model") or None
    if not url or not task:
        return json.dumps({"success": False, "error": "url and task are required"})
    targets = [url] + [x.get("value") or "" for x in (actions or [])
                       if isinstance(x, dict) and x.get("type") == "goto"]
    for target in targets:
        why = wm_urlsafe.url_problem(target)
        if why:
            return json.dumps({"success": False, "stage": "url", "error": f"refused {target!r}: {why}"})
    t0 = time.time()
    setup_info = {"ok": True, "changed": False}
    if not a.get("skip_setup") and os.environ.get("WEBMINT_NO_AUTO_SETUP") != "1":
        setup_info = wm_setup.ensure(auto=True)
        if not setup_info.get("ok"):
            return json.dumps({"success": False, "stage": "setup",
                               "error": setup_info.get("error",
                                                       "browser toolchain is not ready"),
                               "status": setup_info,
                               "hint": "call webmint_setup to provision node/playwright/chromium"})
    work = tempfile.mkdtemp(prefix="webmint-")
    cap = _capture(url, actions, work)
    if not cap.get("ok"):
        return json.dumps({"success": False, "stage": "capture", "error": cap.get("error")})
    capture = json.load(open(os.path.join(work, "capture.json"), encoding="utf-8"))
    why = wm_urlsafe.url_problem(capture.get("finalUrl") or url)
    if why:  # redirected somewhere we would not have opened directly
        return json.dumps({"success": False, "stage": "url", "error": f"capture ended on a refused URL: {why}"})
    html = open(os.path.join(work, "page.html"), encoding="utf-8", errors="replace").read()
    if not name:
        import re
        host = re.sub(r"^www\.", "", (capture.get("finalUrl") or url).split("/")[2])
        name = re.sub(r"[^a-z0-9]+", "_", host.split(".")[0]).strip("_") + "_tool"
    spec = None
    err = ""
    import time as _t
    for attempt, cap in enumerate((55000, 16000, 16000)):
        try:
            spec = wm_synth.ask_model(ctx, task=task, url=url, capture=capture, html=html,
                                      model=model, html_cap=cap)
            break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if attempt < 2:
                _t.sleep(4)  # provider hiccups and oversize prompts both surface as 400/timeout
            continue
    if spec is None:
        return json.dumps({"success": False, "stage": "synth", "error": err[:600],
                           "hint": "capture too large for this model or schema unsupported"})
    spec["name"] = name
    if isinstance(spec.get("reasoning"), str):
        spec["reasoning"] = spec["reasoning"].split("<｜end▁of▁thinking｜>")[-1].strip()[:300]
    spec.setdefault("task", task)
    spec.setdefault("site", capture.get("finalUrl") or url)
    spec.setdefault("effect", "read")
    ok, why = wm_synth.validate(spec)
    if not ok:
        try:
            spec2 = wm_synth.ask_model(ctx, task=task, url=url, capture=capture, html=html, model=model,
                                       feedback=f"validation failed: {why}")
            spec2["name"] = name
            spec2.setdefault("task", task)
            spec2.setdefault("site", capture.get("finalUrl") or url)
            spec2.setdefault("effect", "read")
            ok2, why2 = wm_synth.validate(spec2)
            if ok2:
                spec = spec2
            else:
                return json.dumps({"success": False, "stage": "validate", "error": why2, "spec": spec2})
        except Exception as e:
            return json.dumps({"success": False, "stage": "validate", "error": why, "spec": spec,
                               "repair_error": str(e)[:300]})
    probe = {**wm_synth.example_params(spec, capture.get("finalUrl") or url), **(params or {})}
    ver = wm_run.verify(spec, probe)
    repaired = False
    if not ver.get("ok"):
        # one repair round with the verifier's complaint
        try:
            spec2 = wm_synth.ask_model(ctx, task=task, url=url, capture=capture, html=html, model=model,
                                       feedback=json.dumps(ver)[:1200])
            spec2["name"] = name
            spec2.setdefault("task", task)
            spec2.setdefault("site", capture.get("finalUrl") or url)
            spec2.setdefault("effect", "read")
            ok2, why2 = wm_synth.validate(spec2)
            if ok2:
                ver2 = wm_run.verify(spec2, probe)
                if ver2.get("ok"):
                    spec, ver, repaired = spec2, ver2, True
        except Exception:
            pass
    if not ver.get("ok"):
        return json.dumps({"success": False, "stage": "verify", "error": ver.get("error"),
                           "verification": ver, "spec": spec,
                           "hint": "the spec did not survive execution; pass example_params or actions and re-mint"})
    d = wm_store.save_tool(spec, capture_dir=work,
                           meta={"verified": True, "runs": ver.get("runs"), "fields": ver.get("fields"),
                                 "model": getattr(ctx.llm, "default_model", None) or a.get("model"),
                                 "repaired": repaired, "seconds": round(time.time() - t0, 1),
                                 "example_params": probe, "capture_url": capture.get("finalUrl")})
    return json.dumps({"success": True, "name": spec["name"], "kind": spec.get("kind"),
                       "setup": {"provisioned": setup_info.get("changed"),
                                 "notes": setup_info.get("notes", [])},
                       "dir": d, "verification": ver, "repaired": repaired,
                       "seconds": round(time.time() - t0, 1),
                       "input_schema": spec.get("input_schema")}, indent=1)


def run(args: Mapping | None = None, **kw) -> str:
    a = dict(args or {})
    name = a.get("name") or ""
    params = a.get("params") or a.get("input") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            return json.dumps({"success": False, "error": "params must be a JSON object"})
    try:
        spec = wm_store.load_tool(name)
    except FileNotFoundError as e:
        return json.dumps({"success": False, "error": str(e), "available": [t["name"] for t in wm_store.list_tools()]})
    t0 = time.time()
    res = wm_run.run_spec(spec, params)
    res["success"] = bool(res.get("ok"))
    res["name"] = name
    res["seconds"] = round(time.time() - t0, 2)
    return json.dumps(res, indent=1)


def ls(args: Mapping | None = None, **kw) -> str:
    tools = wm_store.list_tools()
    return json.dumps({"success": True, "count": len(tools), "tools": tools}, indent=1)


def rm(args: Mapping | None = None, **kw) -> str:
    name = (dict(args or {}).get("name") or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"})
    return json.dumps({"success": wm_store.remove_tool(name), "name": name})


def setup(args: Mapping | None = None, **kw) -> str:
    a = dict(args or {})
    if a.get("check_only"):
        return json.dumps({"success": True, **wm_setup.status()}, indent=1)
    res = wm_setup.ensure(auto=True)
    res["success"] = bool(res.get("ok"))
    return json.dumps(res, indent=1)
