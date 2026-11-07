# Primo Car Parts – Njuškalo XML feed

This repository is dedicated to the Njuškalo XML feed only. Shopify is the source of truth.

## Daily workflow

- GitHub Actions: `.github/workflows/njuskalo-daily-stock-sync.yml`
- Schedule: every day at **20:00 UTC** (21:00 CET / 22:00 CEST).
- Script: `PrimoNjusaklo_DAILY_STOCK_SYNC.py`
- Output: `njuskalo.xml`
- Source: read-only Shopify product/catalog inventory data.
- Keep active, in-stock products; add new SKUs and remove SKUs no longer eligible.
- Refresh existing listing titles and Croatian descriptions.
- The current script does **not** rewrite prices or images of already existing XML ads; those fields are populated when an ad is added. A full field reconciliation would require a separate reviewed change.
- Validate XML and commit only changed XML feed data.
- Tests: `.github/workflows/njuskalo-translation-tests.yml` (not a daily product sync).

## Scope

No Shopify SEO, metafield, or vehicle-compatibility writes belong in this repository. SEO and verified fitment are maintained independently in ECU product workflows. Do not reintroduce `pcp_seo_sync.py` or an SEO cron here.
