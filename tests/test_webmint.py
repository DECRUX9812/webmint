"""Hermetic tests for webmint. No network, no model.

Run:  python3 -m pytest plugins/webmint/tests -q
The one browser test is marked and skipped automatically when node/playwright are absent.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes-webapp"))

from webmint import wm_run, wm_store, wm_synth, wm_urlsafe  # noqa: E402

wm_run.ensure_env()  # discovery is lazy now; resolve once for the skipif gates below


@pytest.fixture
def serve_dir(monkeypatch):
    """Serve a directory on 127.0.0.1 (loopback needs the explicit opt-in)."""
    import functools
    import http.server
    import threading
    monkeypatch.setenv("WEBMINT_ALLOW_PRIVATE_URLS", "1")
    servers = []

    def start(path):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(path))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield start
    for srv in servers:
        srv.shutdown()

DOM_SPEC = {
    "name": "demo_tool",
    "kind": "dom",
    "url_template": "https://example.com/search?q={query}",
    "item_selector": "li.row",
    "fields": {"title": "h3", "url": "h3 a@href"},
    "input_schema": {"properties": {"query": {"type": "string"}}, "required": ["query"]},
    "limit": 5,
}


def test_validate_accepts_good_dom_spec():
    ok, why = wm_synth.validate(dict(DOM_SPEC))
    assert ok, why


def test_validate_rejects_bad_name():
    ok, why = wm_synth.validate({**DOM_SPEC, "name": "Bad Name!"})
    assert not ok and "snake_case" in why


def test_validate_requires_fields_for_dom():
    spec = {k: v for k, v in DOM_SPEC.items() if k != "fields"}
    ok, why = wm_synth.validate(spec)
    assert not ok and "fields" in why


def test_validate_requires_items_path_for_http():
    spec = {"name": "h_tool", "kind": "http", "url_template": "https://x/{q}",
            "input_schema": {"properties": {"q": {"type": "string"}}}}
    ok, why = wm_synth.validate(spec)
    assert not ok and "items_path" in why


def test_template_fill_percent_encodes():
    assert wm_run._fill("https://x/?q={query}", {"query": "a b/c"}) == "https://x/?q=a%20b%2Fc"
    assert wm_run._fill("https://x/{missing}", {}) == "https://x/{missing}"


def test_example_params_use_captured_url_values():
    spec = {"input_schema": {"properties": {"site": {"type": "string"}}, "required": ["site"]}}
    got = wm_synth.example_params(spec, "https://news.ycombinator.com/from?site=github.com")
    assert got == {"site": "github.com"}


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    d = wm_store.save_tool(dict(DOM_SPEC), meta={"verified": True})
    assert os.path.isdir(d)
    assert wm_store.load_tool("demo_tool")["item_selector"] == "li.row"
    assert [t["name"] for t in wm_store.list_tools()] == ["demo_tool"]
    assert wm_store.remove_tool("demo_tool") is True
    assert wm_store.list_tools() == []


def test_verify_rejects_zero_rows(monkeypatch):
    monkeypatch.setattr(wm_run, "run_spec", lambda spec, params: {"ok": True, "count": 0, "items": []})
    v = wm_run.verify(dict(DOM_SPEC), {"query": "x"})
    assert not v["ok"] and v["stage"] == "run1"


def test_verify_rejects_thin_rows(monkeypatch):
    thin = [{"title": "a", "url": None}, {"title": "b", "url": None}]
    monkeypatch.setattr(wm_run, "run_spec", lambda spec, params: {"ok": True, "count": 2, "items": thin})
    v = wm_run.verify(dict(DOM_SPEC), {"query": "x"})
    assert not v["ok"] and v["stage"] == "quality" and v["fill"]["url"] == 0


def test_verify_rejects_shape_drift(monkeypatch):
    seq = [{"ok": True, "count": 1, "items": [{"title": "a", "url": "u"}]},
           {"ok": True, "count": 1, "items": [{"title": "a"}]}]
    monkeypatch.setattr(wm_run, "run_spec", lambda spec, params: seq.pop(0))
    v = wm_run.verify(dict(DOM_SPEC), {"query": "x"})
    assert not v["ok"] and v["stage"] == "shape"


def test_verify_accepts_good_runs(monkeypatch):
    good = {"ok": True, "count": 2, "items": [{"title": "a", "url": "u"}, {"title": "b", "url": "u2"}]}
    monkeypatch.setattr(wm_run, "run_spec", lambda spec, params: dict(good))
    v = wm_run.verify(dict(DOM_SPEC), {"query": "x"})
    assert v["ok"] and v["runs"] == [2, 2] and v["fill"] == {"title": 100, "url": 100}


def test_remove_tool_rejects_unsafe_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    wm_store.save_tool(dict(DOM_SPEC), meta={"verified": True})
    assert wm_store.remove_tool("../") is False
    assert wm_store.remove_tool("..") is False
    assert wm_store.remove_tool("") is False
    for bad in ("../", "..", "../../etc/passwd", "a/b", "UPPER", "x" * 60):
        with pytest.raises(ValueError):
            wm_store.load_tool(bad)
    assert wm_store.load_tool("demo_tool")["item_selector"] == "li.row"
    assert wm_store.remove_tool("demo_tool") is True


def test_url_policy(monkeypatch):
    monkeypatch.delenv("WEBMINT_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(wm_urlsafe, "_host_is_safe_url", lambda: None)  # exercise the local rules
    for bad in ("file:///srv/example/page.html", "data:text/html,hi", "about:blank",
                "http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.10.10/"):
        assert wm_urlsafe.url_problem(bad), bad
    assert wm_urlsafe.url_problem("http://93.184.215.14/") is None
    monkeypatch.setenv("WEBMINT_ALLOW_PRIVATE_URLS", "1")
    assert wm_urlsafe.url_problem("http://127.0.0.1/") is None
    assert wm_urlsafe.url_problem("http://169.254.10.10/")  # link-local stays refused
    assert wm_urlsafe.url_problem("file:///srv/example/page.html")


def test_run_spec_refuses_non_http_before_running(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not spawn node or open a connection")
    monkeypatch.setattr(wm_run.subprocess, "run", boom)
    monkeypatch.setattr(wm_run.urllib.request, "build_opener", boom)
    dom = {**DOM_SPEC, "url_template": "file:///srv/example/{query}.html"}
    out = wm_run.run_spec(dom, {"query": "x"})
    assert not out["ok"] and "http(s)" in out["error"]
    http = {**DOM_SPEC, "kind": "http", "url_template": "http://169.254.10.10/{query}",
            "http": {"items_path": "items", "field_paths": {"t": "t"}}}
    out = wm_run.run_spec(http, {"query": "x"})
    assert not out["ok"] and "refused" in out["error"]


@pytest.mark.skipif(not (wm_run.NODE_BIN and wm_run.PW_MODULES), reason="node/playwright unavailable")
def test_extract_from_local_fixture(tmp_path, serve_dir):
    html = """<html><body><ul>
      <li class="row"><h3><a href="/a">Alpha</a></h3><span class="p">$1</span></li>
      <li class="row"><h3><a href="/b">Beta</a></h3><span class="p">$2</span></li>
      <li class="row"><h3><a href="/c">Gamma</a></h3><span class="p">$3</span></li>
    </ul></body></html>"""
    f = tmp_path / "page.html"
    f.write_text(html)
    spec = {"name": "fix", "kind": "dom", "url_template": f"{serve_dir(tmp_path)}/page.html",
            "item_selector": "li.row", "fields": {"title": "h3 a", "href": "h3 a@href", "price": ".p"},
            "limit": 10}
    out = wm_run.run_spec(spec, {})
    assert out["ok"], out
    assert out["count"] == 3
    assert out["items"][0] == {"title": "Alpha", "href": "/a", "price": "$1"}


@pytest.mark.skipif(not (wm_run.NODE_BIN and wm_run.PW_MODULES), reason="node/playwright unavailable")
def test_sibling_prefix_reaches_next_element(tmp_path, serve_dir):
    html = """<html><body><dl>
      <dt><a id="1">one</a></dt><dd><div class="meta">First paper</div></dd>
      <dt><a id="2">two</a></dt><dd><div class="meta">Second paper</div></dd>
    </dl></body></html>"""
    f = tmp_path / "page2.html"
    f.write_text(html)
    spec = {"name": "sib", "kind": "dom", "url_template": f"{serve_dir(tmp_path)}/page2.html",
            "item_selector": "dt", "fields": {"id": "a@id", "title": "+ dd .meta"}, "limit": 10}
    out = wm_run.run_spec(spec, {})
    assert out["ok"], out
    assert out["items"][0]["title"] == "First paper"
