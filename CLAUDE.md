# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install Vercel CLI (once)
npm i -g vercel

# Local development (mirrors Vercel production exactly)
ANTHROPIC_API_KEY=sk-ant-... vercel dev
# → http://localhost:3000

# Deploy to production
vercel --prod
```

Set `ANTHROPIC_API_KEY` as a Vercel secret before first deploy:
```bash
vercel env add ANTHROPIC_API_KEY
```

## Architecture

```
api/index.py      FastAPI app — all backend logic (calculator + scraper)
public/           Static frontend — served by Vercel CDN
  index.html      Single-page layout; element IDs are the contract with app.js
  app.js          Live recalc on input, scrape API call, DOM rendering
  style.css       All design tokens (colours, fonts) in :root CSS variables
requirements.txt  Python deps for Vercel serverless runtime
vercel.json       Routes /api/* → api/index.py; serves public/ as static
```

## Scraping pipeline (`api/index.py`)

Two-stage extraction, in order:

1. **`httpx` + BeautifulSoup** — fast regex scan of JSON-LD and inline JS state blobs (`kaufpreis`, `baseRent`, `serviceCharge`, `wohnflaeche`, etc.)
2. **Claude text API fallback** — strips scripts/nav/footer from HTML, sends first 8 KB of visible text to `claude-haiku-4-5-20251001`, asks for JSON extraction

If IS24 returns a CAPTCHA (401 or "ich bin kein roboter" in body), the error message tells the user explicitly.

## Dual calculation path

The financial model is implemented twice and must stay in sync:

- **`api/index.py` → `analyze_property()`** — Python, used by `POST /api/analyze`
- **`public/app.js` → `analyze()`** — JavaScript, powers live updates without a network round-trip

Both use the same annuity formula: `P × [r(1+r)ⁿ] / [(1+r)ⁿ − 1]`

## Financial model defaults

| Parameter | Default | Notes |
|---|---|---|
| Interest rate | 4.0% p.a. | Adjustable in UI |
| Down payment | 20% | Adjustable in UI |
| Loan term | 30 years | Adjustable in UI |
| Maintenance | 2 €/m²/month | Typical DE range 1.5–2.5 |
| Grunderwerbsteuer | 3.5% | Bavaria; NRW is 6.5% — user must adjust |
| Notar + Grundbuch | 1.5% | |
| Makler | 3.57% | Buyer-side typical |

Cashflow = Rent − Mortgage − Hausgeld − Maintenance  
Net yield excludes mortgage; uses (Annual Rent − Hausgeld − Maintenance) / Purchase Price.

## Adding a new property portal

1. Add URL pattern to `scrape_property()` in `api/index.py`
2. Add site-specific JS key → field mappings to the `_parse_html()` loop
3. The Claude text fallback handles the rest automatically
