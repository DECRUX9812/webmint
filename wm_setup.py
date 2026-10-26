"""Dependency discovery and (optional) self-provisioning.

Plug-and-play means: install the plugin, call it, and it either works or tells you
in one line what is missing. Node cannot be installed for the user; Playwright can,
so it is provisioned on demand into the plugin's own directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PW_VERSION = "1.63.0"
NPM_TIMEOUT = 420
BROWSER_TIMEOUT = 900


def _run(cmd, cwd=None, timeout=120, env=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def status() -> Dict[str, Any]:
    from . import wm_run
    wm_run.refresh()
    info: Dict[str, Any] = {
        "node": wm_run.find_node() or "",
        "playwright_module": wm_run.find_playwright() or "",
        "npm": shutil.which("npm") or "",
        "chromium": "",
    }
    if info["node"]:
        code, out = _run([info["node"], "-v"], timeout=15)
        info["node_version"] = out.strip() if code == 0 else ""
    if info["node"] and info["playwright_module"]:
        # A real launch is the only honest readiness check: the browser binary can be
        # present-but-wrong-version, and a headless-shell install is enough for us.
        probe = ("const {chromium}=require('playwright');"
                 "chromium.launch({headless:true})"
                 ".then(async b=>{console.log('LAUNCH_OK');await b.close()})"
                 ".catch(e=>console.log('LAUNCH_FAIL '+String(e.message).slice(0,120)))")
        code, out = _run([info["node"], "-e", probe], cwd=PLUGIN_DIR, timeout=90,
                         env={**os.environ, "NODE_PATH": info["playwright_module"]})
        info["browser_installed"] = "LAUNCH_OK" in out
        info["chromium"] = "ok" if info["browser_installed"] else (out.strip()[-160:] or "")
    else:
        info["browser_installed"] = False
        info["chromium"] = "playwright module not found"
    info["ready"] = bool(info.get("node") and info.get("playwright_module")
                         and info.get("browser_installed"))
    return info


def ensure(auto: bool = True) -> Dict[str, Any]:
    """Make the plugin runnable. Idempotent; safe to call on every mint."""
    from . import wm_run
    before = status()
    if before["ready"]:
        return {"ok": True, "changed": False, **before}

    notes = []
    if not before.get("node"):
        return {"ok": False, "changed": False, **before,
                "error": "Node >= 18 is required and could not be found. Install Node, or set "
                         "WEBMINT_NODE to a node binary."}

    if not before.get("playwright_module"):
        npm = before.get("npm") or shutil.which("npm")
        if not npm:
            return {"ok": False, "changed": False, **before,
                    "error": "Playwright is missing and npm is not available to install it. "
                             "Install playwright (npm i playwright) or set WEBMINT_PW_MODULES."}
        notes.append("installing playwright into the plugin directory")
        code, out = _run([npm, "install", f"playwright@{PW_VERSION}", "--no-audit", "--no-fund",
                          "--silent"], cwd=PLUGIN_DIR, timeout=NPM_TIMEOUT)
        if code != 0:
            return {"ok": False, "changed": False, **before,
                    "error": f"npm install playwright failed: {out[-400:]}"}

    wm_run.refresh()
    if not wm_run.find_playwright():
        return {"ok": False, "changed": False, **before, "error": "playwright still not resolvable"}

    if not before.get("browser_installed"):
        notes.append("downloading Chromium for playwright (about 120 MB, one time)")
        code, out = _run([before["node"], os.path.join(PLUGIN_DIR, "node_modules", "playwright", "cli.js"),
                          "install", "chromium"], cwd=PLUGIN_DIR, timeout=BROWSER_TIMEOUT)
        if code != 0:
            return {"ok": False, "changed": True, **status(),
                    "error": f"playwright install chromium failed: {out[-400:]}", "notes": notes}

    after = status()
    return {"ok": after["ready"], "changed": True, "notes": notes, **after}
