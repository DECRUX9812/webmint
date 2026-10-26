# Changelog

## 0.1.2 — 2026-10-26

- `docs/banner.png`: catalog/repo hero image, wired into the README and the catalog entry

## 0.1.1 — 2026-10-26

- `plugin.yaml`: declare `requires_hermes`, tags, homepage and repository (catalog admission)
- no runtime changes

## 0.1.0 — 2026-10-26

First public release.

- `webmint_mint` / `webmint_run` / `webmint_list` / `webmint_remove` / `webmint_setup`
- Model-agnostic minting through the host's `ctx.llm` (no vendor SDK, no API key of its own)
- Declarative spec: `kind: dom` (selectors) or `kind: http` (captured JSON endpoint)
- Field expressions: `css`, `css@attr`, `+` next sibling, `^` parent
- Verification gate: two runs, identical field set, non-empty rows, >=50% fill per field,
  one automatic repair round on failure
- Self-provisioning browser toolchain: Node/Playwright discovery, automatic
  `npm i playwright` + `playwright install chromium` on first use
- 13 tests, including two that launch Chromium against local fixtures
