#!/usr/bin/env node
/**
 * webmint extract — render a URL and apply a declarative selector spec.
 * Usage: node wm_extract.cjs --spec spec.json --params '{"query":"dune"}' --out result.json
 * Spec (kind=dom):
 *   { url_template, wait_selector?, wait_ms?, item_selector, fields:{k:"css"|"css@attr"},
 *     limit?, scroll? }
 * Prints {"ok":true,"items":[...]} or {"ok":false,"error":"..."}
 */
const fs = require('fs');
const { chromium } = require('playwright');

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i === -1 ? dflt : process.argv[i + 1];
}
const specPath = arg('spec');
const outPath = arg('out');
let params = {};
try { params = JSON.parse(arg('params', '{}')); } catch (e) {}
const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));

function fill(tpl, p) {
  return String(tpl || '').replace(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g, (m, k) =>
    (p[k] === undefined || p[k] === null ? m : encodeURIComponent(String(p[k]))));
}

(async () => {
  const url = fill(spec.url_template, params);
  if (!/^https?:\/\//i.test(url)) throw new Error('only http(s) URLs are allowed');
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 1200 },
    locale: 'en-US',
  });
  const page = await ctx.newPage();
  const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  if (spec.wait_selector) {
    try { await page.waitForSelector(spec.wait_selector, { timeout: 20000 }); } catch (e) {}
  }
  if (spec.scroll) {
    for (let i = 0; i < (spec.scroll === true ? 3 : Number(spec.scroll)); i++) {
      await page.mouse.wheel(0, 2500).catch(() => {});
      await page.waitForTimeout(600);
    }
  }
  await page.waitForTimeout(spec.wait_ms === undefined ? 1200 : Number(spec.wait_ms));

  const limit = Number(spec.limit || 25);
  const items = await page.evaluate(({ itemSel, fields, limit }) => {
    const nodes = Array.from(document.querySelectorAll(itemSel));
    const pick = (root, expr) => {
      let base = root;
      let rest = String(expr || '').trim();
      // '+' resolves from the next sibling, '^' from the parent: layouts that split one
      // record across sibling elements (arXiv's dt/dd pairs) are otherwise unreachable.
      if (rest.startsWith('+')) { base = root.nextElementSibling || root; rest = rest.slice(1).trim(); }
      else if (rest.startsWith('^')) { base = root.parentElement || root; rest = rest.slice(1).trim(); }
      const [sel, attr] = rest.split('@');
      const el = sel ? base.querySelector(sel) : base;
      if (!el) return null;
      if (attr) return el.getAttribute(attr);
      return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    };
    const out = [];
    for (const n of nodes.slice(0, limit)) {
      const row = {};
      let any = false;
      for (const [k, expr] of Object.entries(fields || {})) {
        const v = pick(n, expr);
        row[k] = v;
        if (v) any = true;
      }
      if (any) out.push(row);
    }
    return out;
  }, { itemSel: spec.item_selector, fields: spec.fields || {}, limit });

  const result = { ok: true, url, finalUrl: page.url(), status: resp ? resp.status() : null, count: items.length, items };
  if (outPath) fs.writeFileSync(outPath, JSON.stringify(result, null, 1));
  console.log(JSON.stringify(result));
  await browser.close();
})().catch(e => {
  const err = { ok: false, error: String(e).slice(0, 500) };
  console.log(JSON.stringify(err));
  process.exit(1);
});
