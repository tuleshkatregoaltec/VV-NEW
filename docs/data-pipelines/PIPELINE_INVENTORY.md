# Data Pipeline Inventory

**Status:** canonical inventory, audit baseline of 2026-09-29, branch `docs/production-first-readiness`
(base commit `71cf611`). Keep this file current: when a pipeline changes status, update its row
and the per-pipeline detail in [PIPELINE_AUDITS.md](../audits/PIPELINE_AUDITS.md).

Related documents:

- Current system: [CURRENT_STATE.md](../architecture/CURRENT_STATE.md)
- Standard every pipeline is measured against: [PIPELINE_STANDARDS.md](PIPELINE_STANDARDS.md)
- Executive audit: [PRODUCTION_READINESS_AUDIT.md](../audits/PRODUCTION_READINESS_AUDIT.md)
- Proposed target: [TARGET_ARCHITECTURE.md](../architecture/TARGET_ARCHITECTURE.md)
- Implementation plan: [MIGRATION_PLAN.md](../migration/MIGRATION_PLAN.md)
- Platform-wide launch architecture (auth, tenancy, AI, cloud): [production-readiness-2026-09-29.md](../../production-readiness-2026-09-29.md)

## How to read this

- **ID** is stable. Use it in commits, PRs and the migration plan (e.g. "P01: atomic publication").
- **Trigger** records what code defines. Dagster schedules exist but **every schedule defaults to
  `STOPPED`** (`holocron/holocron/orchestration/factory.py:896-903`). Whether a schedule is
  switched on in a running Dagster instance cannot be determined from the repository: *Requires
  human confirmation*.
- **Status** values: `active-scheduled` (schedule defined in code), `active-external` (runs
  outside Dagster via systemd/launchd), `manual` (operator-triggered only), `one-time`
  (historical load), `experimental`, `demo`, `unowned` (consumed but no producer in repo),
  `dead?` (no caller found; confirm before removing).
- Table names are ClickHouse unless marked **PG** (PostgreSQL) or **R2** (object storage;
  Cloudflare R2 today, S3-compatible).

## Summary table

| ID | Source | Type | Entry point | Trigger | Storage | Consumers | Status |
|----|--------|------|-------------|---------|---------|-----------|--------|
| P01 | DLD Open Data gateway (9 endpoints: transactions, rents, projects, valuations, lands, buildings, units, brokers, developers) | HTTP JSON API (POST, paginated) | `holocron/holocron/sources/dld_open_data_shared.py` via `dld_od_<category>_raw` / `_bronze` assets | Dagster `dld_od_<category>_daily_incremental` 04:00–05:30 Asia/Dubai; manual backfill/snapshot jobs | R2 raw JSONL → `dld_od_<category>_bronze` | Backend `ch_transactions`, `ch_rent_contracts`, `ch_projects`, `ch_developers`, `ch_buildings`, `ch_units` views → analytics, radar, transactions, feasibility, chat tools, CRM; silver; geography | active-scheduled |
| P02 | Dubai Pulse historic archive | Local CSV/KML files | `sources/dld_pulse_historic/` (`dld_pulse_historic_archive`, `dld_pulse_historic_bronze_backfill`) | Manual | R2 raw → same `dld_od_*_bronze` tables with `source_system='dubai_pulse'` | Same as P01 (history before open-data cutoff) | one-time |
| P03 | DLD Mashrooi project/coordinate API | HTTP JSON API with session token | `sources/dld_mashrooi/extract.py` (`dld_mashrooi_raw`) | Manual (no job wraps it; asset only) | R2 raw only; `backend/scripts/radar/import_mashrooi_coordinates_demo.py` → `demo_mashrooi_*` | None found in `backend/app` | manual / demo |
| P04 | DXB Interact map (heatmap, building status, chessboard, FAM map) | Browser automation (Playwright) + in-page fetch | `sources/dxbi/extract.py` (`dxbi_transactions_raw` / `_bronze`) | Dagster `dxbi_transactions_daily_incremental` 06:00; manual backfill | R2 raw → `dxbi_heatmap_*_bronze`, `dxbi_fam_map_buildings_bronze`, `dxbi_transactions_bronze` | `ch_geo_buildings` → `ch_geo_projects` → backend projects/radar | active-scheduled (likely failing, see F11) |
| P05 | DXB Interact rental & sales report scrapes | Browser automation with account credentials + CAPTCHA service | `holocron/scripts/dxbi_rental_backfill.py` via `deploy/systemd/dxbi-report-daily.sh` | systemd timers on ph-box (root): sales daily 22:30 UTC; rentals daily 00:30, weekly Sun 02:00, monthly day-1 03:00 UTC | Host files `/root/data/dxbi-daily/...` → rsync → developer Mac → `scripts/load_dxbi_rental_events.py`, `scripts/load_dxbi_sales_units.py` → `dxbi_rental_events(_v2)`, `dxbi_rental_unit_candidates(_v2)`, `dxbi_sales_unit_events`, `dxbi_rental_sale_matches`, `dxbi_rental_run_reconciliation` | CRM owners/ownership/opportunities/reports, chat CRM tools, analytics | active-external |
| P06 | DXB Interact historical rental repair queue | Browser automation | `/root/data/dxbi-rental-repair-20260915/rental_repair_queue.py` (**copy outside repo**; repo copy `holocron/holocron/platform/rental_repair_queue.py`) | systemd `dxbi-rentals-repair.timer` (every minute after inactivity) | Host files → `holocron.platform.sync_rental_repairs` (Mac) → rental v2 tables | Same as P05 | active-external / one-time |
| P07 | DXB Interact legacy JS backfill runners | Node + browser | `holocron/scripts/dxbi_*_runner.js`, `dxbi_*_supervisor.sh`, `dxbi_sales_watchdog.sh`, `load_dxbi_normalized_sales_csv.py` | Manual (tmux/zsh supervisors on a Mac) | Local CSV/JSONL → ClickHouse | CRM (historical sales/rentals) | legacy / one-time |
| P08 | Property Finder location tree | HTTP JSON (Next.js data) | `sources/pf_locations/extract.py` | Dagster `pf_locations_manual_full_fetch` (manual) | R2 raw → `pf_locations_bronze` | Geography (`geo_location_nodes`) → backend area/CRM/listings hierarchy | manual |
| P09 | Property Finder listings (search + detail) | Browser automation + in-page fetch | `sources/pf_listings/extract.py` | Dagster `pf_listings_daily_latest_delta` 03:00; manual plan/search/detail/snapshot chunk jobs; `holocron/scripts/run_propertyfinder_full_dubai.sh` | R2 raw + checkpoints → `pf_listings_bronze`, `pf_listing_details_bronze` | Backend listings, radar, CRM inbound; `scripts/listings/*` exports | active-scheduled |
| P10 | Property Finder listing media | HTTP download (allowlisted hosts) | `sources/pf_listings/media_assets.py` (`pf_listing_media_assets`) | Manual `pf_listing_media_assets_backfill` | R2 `media/pf_listings/...` + inventory manifests | None found in backend (media not served) | manual |
| P11 | Reelly / Xano project catalogue, documents metadata, availability | HTTP JSON API with login (bearer token) | `sources/reelly_supply/extract.py` | Dagster `reelly_supply_daily_catalog_delta` 02:00, `reelly_supply_daily_availability_delta` 02:30; manual archive chunks | R2 raw + checkpoints → `reelly_projects_bronze`, `reelly_documents_bronze`, `reelly_matrix_availability_bronze` | Backend supply/upcoming, projects, radar, feasibility; geography off-plan links | active-scheduled |
| P12 | Reelly documents and project media | HTTP download (allowlisted hosts incl. Google Drive) | `sources/reelly_supply/document_assets.py` | Part of `reelly_supply_daily_catalog_delta`; manual backfills | R2 `media/reelly_supply/...` + inventory manifests | Backend supply (document links) — *verify* | active-scheduled |
| P13 | DDA planning map (ArcGIS layers + plot pages) | HTTP (ArcGIS REST + HTML scrape, token from page) | `sources/dda/extract.py`, `parse.py` | Dagster `dda_planning_layers_weekly_full_refresh` Sun 06:00; manual full refresh | R2 raw CSV → 11 `dda_*` tables (full replace) | `ch_gis_land_plots` → backend plots, feasibility, projects/DDA valuation | active-scheduled |
| P14 | News RSS/Atom feeds (15 default feeds) + thumbnails | HTTP RSS + optional article-page fetch | `sources/news_feeds/extract.py`, `assets.py` | Dagster `news_feeds_poll_and_hydrate` every 15 min (**does not publish**); manual `news_publish_backfill` publishes | R2 raw + thumbnails → **PG** `news_articles` (DDL run by Holocron) | Backend `/api/v1/news` → frontend news cards | active-scheduled (poll) + manual (publish) |
| P15 | News push/import into the app | Service-key HTTP API; S3 import script | `backend/app/upload/router.py` `POST /api/v1/upload/news/article`; `backend/scripts/news/import_from_s3.py` | External caller *unknown*; manual script | **PG** `news_articles` | Same as P14 | dead? / manual |
| P16 | Backend chunked ETL upload ingress | Service-key HTTP multipart | `backend/app/upload/service.py` `POST /api/v1/upload/chunked` | External caller *unknown* | R2 `etl/uploads/<session>/chunk_*`, `metadata.json`; Redis receipt set | **No assembler/consumer in repo** | dead? |
| P17 | Bayut listings | Browser automation (+ proxy experiments) | `sources/bayut_listings/`, `holocron/scripts/run_bayut_*`, `holocron/handoff/bayut_standalone/` | Manual | R2 raw only (no bronze); re-checked 2026-10-03: no DDL, loader, serving view or backend consumer | None | experimental |
| P18 | Silver projections (`silver_transactions`, `silver_rent_contracts`) | Derived SQL (ClickHouse) | `holocron/holocron/silver/` ; `holocron/holocron/platform/sync_silver.py` (local) | Dagster `silver_transactions_refresh` 06:00, `silver_rent_contracts_refresh` 06:10, **and** `silver_refresh` 06:00 (overlap) | `silver_transactions`, `silver_rent_contracts` (staging + EXCHANGE) | **No consumer in `backend/app`** (serving views read bronze) | active-scheduled, unconsumed |
| P19 | Geography crosswalk and off-plan links | Derived (Python resolver + SQL) | `holocron/holocron/assets/geography.py`, `domain/geography.py`, `domain/offplan_projects.py`; `holocron/scripts/refresh_geography_crosswalk.py` | Dagster `geography_crosswalk_refresh` (manual); run after DXBI loads by `sync_dxbi_daily_runs.sh` (Mac) | `geo_*`, `offplan_reelly_project_*` tables and views (`geo_mapped_source_facts`, …); manual override tables | Backend area, CRM, projects, supply, listings | manual / active-external |
| P20 | Backend serving layer: compatibility views + fact tables | Derived (ClickHouse DDL) | `backend/scripts/clickhouse/create_compatibility_views.py`, `build_fact_tables.py` | Manual (`just build-clickhouse-facts`) | `ch_*` views; `ch_project_fact`, `ch_area_fact` (view or materialised table) | Entire backend market-data read surface | manual |
| P21 | Local market-data mirror | SSH/rsync pull + ClickHouse EXCHANGE | `holocron/holocron/platform/sync_market_data.py`, `sync_dld.py`, `sync_silver.py`, `sync_rental_repairs.py`; `holocron/scripts/sync_dxbi_daily_runs.sh` | macOS launchd `com.vitevue.dxbi-sync` daily 09:00 | Developer Mac ClickHouse + Redis cache invalidation | Local backend; also triggers P22 | active-external (developer) |
| P22 | CRM rental import refresh and owner re-match | Derived (ClickHouse → PG) | `backend/scripts/crm/refresh_rental_imports.py`, `rematch_owner_claims.py` | Stage of P21 (Mac); manual | **PG** CRM claim/import tables | CRM UI | active-external / manual |
| P23 | **Unknown producer**: `silver_reelly_projects`, `reelly_project_unit_types_bronze` | Unknown | No `CREATE TABLE` or writer anywhere in the repo | Unknown | ClickHouse | Backend `project/queries.py:87`, `project/radar_queries.py:68`, `supply/queries.py:62`; Holocron `assets/geography.py:652` and schema view `clickhouse_schema.sql:1534` | unowned |
| P24 | User uploads: CRM owner/contact and inbound spreadsheets | File upload (≤50 MB) + LLM header mapping (OpenRouter) | `backend/app/crm/imports.py`, `inbound_imports.py`, `import_parsing.py`, `ai.py` | User HTTP request | **PG** CRM tables | CRM UI, chat CRM tools | active (request-coupled) |

### Status changes since the baseline

Rows above describe the 2026-09-29 baseline. The changes below are **code ready in open PRs**. None is staging
accepted or production enabled, and every new schedule defaults to `STOPPED`.

| ID | Change | PR | Still open |
|----|--------|----|-----------|
| P01 | `dld_od_projects`, `_developers`, `_valuations` replace by stored date column | #4 | F03 proof for all nine units (Prompt 08) |
| P05 | `dxbi_evidence_bronze` / `dxbi_evidence_load` load scraper output with a raw archive and `dxbi_evidence_load_ledger` | #5 | Scraper still on ph-box; D1, D4; repair queue (P06) |
| P09 | Browser route unwraps `props.pageProps`; empty-parse "success" now raises | #4 | Live smoke |
| P13 | Plot/layer queries request only still-published fields | #4 | Stub rows on plot failure (F08) |
| P20 | Fact builder rebuilds from source views (F13 fixed); `ch_transactions` / `ch_rent_contracts` read the canonical market layer | #5 | Real-database rebuild test; move to Holocron (5A) |
| P23 | DDL for `silver_reelly_projects`, `reelly_project_unit_types_bronze` | #5 | Producer in #21; live payload run pending |
| new | Canonical `market_sale_events` / `market_rental_events` (job `market_events_refresh`) with 22 post-publication asset checks | #5 | Validate before promote (S4.1, Prompt 06) |
| new | Staged release validated before exchange: blocking checks + 99% served-row floor on run-unique staging; last release kept as `__previous` | #10 (on #5) | Addressed by #15 (lock, ledger, recovery) |
| P20 | `ch_projects` serves one row per DLD project number (was 4,129 rows for 3,413 projects, +21.6% units); facts need `just build-clickhouse-facts` | #11 (on #5) | Vocabulary split addressed by #18 |
| — | Read-only census (`just census`) and `DATA_SOURCE_HEALTH.{json,md}` | #3 | Re-run against staging |
| new | Publication: `market_release_lock`, durable `market_release_ledger`, release-stamped tables, ordered promotion, `recover`/`rollback`; per-day loss, source presence and NaN-check semantics | #15 | Staging drill of crash/rollback |
| P01, P08, P11, P13 | Shared `http_retry` (429/Retry-After, backoff+jitter, deadline, denial stops), provider limiters + Dagster provider pools; DLD watermark only after a complete bronze load, 7-day-overlap catch-up, mid-crawl TOTAL check; DDA stops instead of stub rows (F08) | #16 | D1/D2 quotas; browser sources (DXBI, PF listings, Bayut) not behind `send()`; live smoke |
| P01 → serving | Coverage from `source_load_windows` (page evidence) stored per release; API/chat statuses complete/partial/unverified/unavailable; `release_id` on answers | #17 | Existing data unverified until replayed or attested; view recreation at rollout |
| P19/P20 | Canonical district/community via `market_geo_crosswalk` (evidence from DLD); area list, rankings, metrics, delivery use it | #18 | Chart/facet/radar queries still read raw `area_name_en` |
| P01 | Bronze window replace via lightweight delete (62 s → 0.2 s on rents) | #19 | Staging measurement |
| new | Immutable `__r<release>` versions + backend `pin_market_release` for chat (#13); 24 h retention vs 30 min pins | #20 | Wire #13's `acquire_market_release`; reader grants |
| P17 | Bayut certified-snapshot loader, `bayut_listings_current`, withdrawals between certified snapshots only | #22 | D1 access/licensing; no backend endpoint; acquisition manual |
| P01 → serving | Multi-line DLD transaction numbers served line by line (722 numbers / 3,614 lines were collapsed) | #23 | — |

Out of scope for this inventory: chat/feasibility LLM calls, Stripe, auth, which the platform
reference covers. They consume data but do not ingest market data.

## Shared stages (used by many pipelines)

| Stage | Code | Used by |
|-------|------|---------|
| Raw release upload + manifest write | `holocron/holocron/orchestration/factory.py::_run_raw_extractor` | P01–P04, P08, P09, P11, P13, P14, P17 |
| Checkpoints in R2 `state/source=<s>/operation=<op>.json` | `holocron/holocron/platform/checkpoints.py` + source `orchestration.py` modules | P01 (backfill/snapshot, rents incremental), P09, P11 |
| Bronze load helpers (delete+insert, keyed replace, full EXCHANGE) | `holocron/holocron/platform/bronze.py`, `clickhouse.py::replace_table_rows` | All bronze loaders |
| Runtime schema install (`ensure_schema`, full DDL file on every load) | `holocron/holocron/platform/clickhouse_schema.py` | All bronze loaders, silver, geography |
| Replay from R2 manifests / dataset versions | `holocron/holocron/platform/bronze_replay.py`, `dataset_versions.py` | Operator recovery for all bronze sources |
| Media/document hydration (allowlist + size limit + SSRF check) | `holocron/holocron/platform/hydration.py`, `http_download.py` | P10, P12, P14 |

## Downstream map (who reads what)

```text
dld_od_transactions_bronze ─┬─> ch_transactions ─┬─> analytics, transactions, radar, chat tools, feasibility
                            │                    └─> ch_project_fact / ch_area_fact (view or frozen table)
                            ├─> silver_transactions            (no backend consumer)
                            └─> geo_source_signatures ─> geo_entity_links ─> geo_* views ─> area/CRM/projects
dld_od_rents_bronze ────────┬─> ch_rent_contracts ─> analytics, radar, facts
                            └─> silver_rent_contracts          (no backend consumer)
dld_od_projects/developers/buildings/units_bronze ─> ch_projects/ch_developers/ch_buildings/ch_units ─> projects, radar, analytics
dda_land_plots ─> ch_gis_land_plots ─> plots, feasibility, DDA valuation
dxbi_heatmap_buildings_bronze ─> ch_geo_buildings ─> ch_geo_projects ─> projects/radar maps
dxbi_rental_* / dxbi_sales_unit_events (P05, produced off-Dagster) ─> CRM owners, ownership, reports, opportunities
pf_listings_bronze ─> listings API, radar, CRM inbound
pf_locations_bronze ─> geo_location_nodes ─> geo_location_closure ─> hierarchy everywhere
reelly_*_bronze + silver_reelly_projects (P23) ─> supply/upcoming, projects, radar, offplan links
news_articles (PG) ─> /api/v1/news ─> frontend news cards
```

## Detailed audits

Per-pipeline audits (purpose, flow, code, trigger, auth, rate limits, reliability, data quality,
storage, consumers, observability, tests, security, failure modes, readiness scores) are in
[PIPELINE_AUDITS.md](../audits/PIPELINE_AUDITS.md), one section per ID above.
