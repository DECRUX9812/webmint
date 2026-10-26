#!/usr/bin/env node
/**
 * webmint capture — open a URL, run a small action list, record every JSON/XHR response.
 * Usage: node wm_capture.mjs --url URL --out DIR [--actions '[]'] [--headed] [--wait-ms N]
 * Writes: capture.json (candidates + meta), page.html
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i === -1 ? dflt : process.argv[i + 1];
}
const url = arg('url');
const out = arg('out');
const waitMs = parseInt(arg('wait-ms', '3500'), 10);
let actions = [];
try { actions = JSON.parse(arg('actions', '[]')); } catch (e) { actions = []; }
if (!url || !out) { console.error('need --url and --out'); process.exit(2); }
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: process.argv.includes('--headed') === false });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();
  const seen = [];
  const safeHeaders = (h) => {
    const keep = {};
    for (const [k, v] of Object.entries(h || {})) {
      if (/^(cookie|authorization|set-cookie)$/i.test(k)) continue;
      keep[k] = v;
    }
    return keep;
  };

  page.on('response', async (res) => {
    try {
      const req = res.request();
      const ct = (res.headers()['content-type'] || '').toLowerCase();
      const type = req.resourceType();
      if (!/json|javascript|text\/plain/.test(ct)) return;
      if (type === 'script' || type === 'stylesheet' || type === 'image' || type === 'font') return;
      let body = '';
      try { body = await res.text(); } catch (e) { return; }
      if (!body || body.length > 900000) return;
      seen.push({
        url: res.url(),
        method: req.method(),
        status: res.status(),
        contentType: ct,
        requestHeaders: safeHeaders(req.headers()),
        postData: req.postData() || null,
        bodyLength: body.length,
        bodyHead: body.slice(0, 4000),
      });
    } catch (e) { /* ignore */ }
  });

  const nav = { url, status: null, error: null };
  try {
    const r = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    nav.status = r ? r.status() : null;
  } catch (e) { nav.error = String(e).slice(0, 300); }

  for (const a of actions) {
    try {
      if (a.type === 'fill') await page.fill(a.selector, a.value, { timeout: 15000 });
      else if (a.type === 'click') await page.click(a.selector, { timeout: 15000 });
      else if (a.type === 'press') await page.press(a.selector || 'body', a.value || 'Enter');
      else if (a.type === 'goto') await page.goto(a.value, { waitUntil: 'domcontentloaded', timeout: 45000 });
      else if (a.type === 'wait') await page.waitForTimeout(a.ms || 1000);
      else if (a.type === 'waitFor') await page.waitForSelector(a.selector, { timeout: 15000 });
    } catch (e) { /* keep going; the model sees what loaded */ }
  }
  await page.waitForTimeout(waitMs);

  const html = await page.content();
  fs.writeFileSync(path.join(out, 'page.html'), html);
  const meta = {
    url, finalUrl: page.url(), title: await page.title(),
    nav, capturedAt: new Date().toISOString(),
    candidates: seen.sort((a, b) => b.bodyLength - a.bodyLength).slice(0, 40),
  };
  fs.writeFileSync(path.join(out, 'capture.json'), JSON.stringify(meta, null, 1));
  console.log(JSON.stringify({ ok: true, out, finalUrl: meta.finalUrl, title: meta.title,
    candidates: meta.candidates.length, htmlBytes: html.length,
    top: meta.candidates.slice(0, 5).map(c => ({ url: c.url.slice(0, 120), method: c.method, len: c.bodyLength })) }, null, 1));
  await browser.close();
})().catch(e => { console.error(JSON.stringify({ ok: false, error: String(e).slice(0, 500) })); process.exit(1); });
