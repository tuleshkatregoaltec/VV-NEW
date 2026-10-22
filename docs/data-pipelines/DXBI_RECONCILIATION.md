# DXB Interact Reconciliation and Uniform Data Layer

Status: audit (§1–§9) + design, **implemented and verified in local pre-prod** — see §10.

- Environment measured: local pre-prod — the handoff snapshot of the source-Mac warehouse
  (manifest 2026-09-21T13:25:52Z, commit 86b924d), restored on this PC. All numbers are as of that snapshot.
- Method: code reading plus read-only ClickHouse queries. Every number below comes from a query in
  [`dxbi_reconciliation.sql`](dxbi_reconciliation.sql) (Q1–Q15); re-run them against any environment.
- Builds on: [PIPELINE_INVENTORY](PIPELINE_INVENTORY.md), [DATA_SOURCE_HEALTH](DATA_SOURCE_HEALTH.md),
  [PRODUCTION_READINESS_AUDIT](../audits/PRODUCTION_READINESS_AUDIT.md) (F-numbers),
  [MIGRATION_PLAN](../migration/MIGRATION_PLAN.md) (D-numbers).
- Labels: **Fact** = observed in code or data. **Recommendation** = my proposal. Unknowns say so.

---

## 1. Answers in short

1. **What DXBI builds:** 20 warehouse objects in two unrelated pipelines — a *map layer* (7 tables, Holocron/Dagster)
   and an *evidence layer* (5 tables + 1 view, off-Dagster scripts on ph-box and the developer Mac), plus
   7 leftover copies the backend never reads (§2).
2. **Is DXBI good enough to be primary?** For **market events** — yes, with conditions:
   - Sales: 95.8% of DXBI 2025 sales are the same records as DLD sales (date ±3 days, exact price). DXBI holds
     **89.6%** of all DLD sales 2014–2026 because its loader throws away sales with a hidden unit number (§4, L1).
   - Rentals: the active DXBI table tracks Ejari at **92–101% per month** (Jan–Sep 2025) and is the **only rental
     source with contract identity after 2025** — DLD open-data rent rows have no contract number (L4).
   - DXBI has **no rental history before 2025-01-01** and **no registries** (projects, developers, buildings, lands,
     brokers), no off-plan catalogue, no listings and no plots (§3).
3. **Where data leaks:** 14 leak points, from raw collection to the API (§5). The largest: 114,466 sales dropped at
   load (L1); 805,940 DLD rent rows with no contract identity (L4); four divergent rental tables with ~1.4M
   non-overlapping rows (L3); 437k transaction/rental rows that never map to a location (L9); only 15.6% of rented
   units carry owner-sale evidence (L10).
4. **Recommendation:** make DXBI the primary layer for **sale and rental events and unit identity**; keep DLD as the
   **registry and completeness reference**; keep Reelly, DDA and Property Finder for what only they hold. Unify them in
   one canonical event layer with explicit match status and lineage (§6). Pre-conditions in §7.

---

## 2. Every table DXBI builds

**Fact.** Two independent pipelines write `dxbi_*` objects.

```text
MAP LAYER (Holocron source "dxbi_transactions", Dagster job dxbi_transactions_daily_incremental, ph-box)
  dxbinteract.com rental map ──Playwright──▶ raw JSONL (object storage) ──bronze loader──▶ dxbi_heatmap_* / dxbi_fam_map_* / dxbi_transactions_bronze
                                                                          └─▶ ch_geo_buildings (view) ─▶ ch_geo_projects (view)

EVIDENCE LAYER (off-Dagster — F01)
  ph-box: scripts/dxbi_rental_backfill.py + dxbi_*_runner.js (logged-in APEX report scrape, rentals + sales)
     └─▶ /root/data/dxbi-daily/{rentals,sales}/run=<date>/ (local disk only, _SUCCESS marker)
  Mac launchd 09:00: holocron.platform.sync_market_data
     ├─ sync_dld            (mirror DLD bronze from ph-box, EXCHANGE TABLES, keeps *_local_sync_previous)
     ├─ sync_silver
     ├─ scripts/sync_dxbi_daily_runs.sh ─ rsync ─▶ load_dxbi_rental_events.py ─▶ dxbi_rental_events, dxbi_rental_unit_candidates, dxbi_rental_run_reconciliation
     │                                           load_dxbi_sales_units.py   ─▶ dxbi_sales_unit_events, dxbi_rental_sale_matches
     │                                           refresh_geography_crosswalk.py ─▶ geo_source_signatures / geo_entity_links (dxbi_rentals, dxbi_sales)
     ├─ sync_rental_repairs (repair queue, code partly outside repo — P06)
     └─ backend scripts.crm.refresh_rental_imports (CRM)
```

### 2.1 Map layer — Holocron, last loaded 2026-05-27

| Table | Holds (grain) | Key | Rows | Size | Read by |
|---|---|---|---:|---:|---|
| `dxbi_heatmap_rental_buildings_bronze` | map discovery rows per location × type × bedrooms query | `rental_key` | 9,058 | 2.0 MB | nothing |
| `dxbi_heatmap_buildings_bronze` | per-building detail payload (name, project, coordinates) | `response_key` | 3,279 | 2.0 MB | `ch_geo_buildings` → `ch_geo_projects` → `/projects`, `/radar` building pins |
| `dxbi_heatmap_building_statuses_bronze` | per-building status payload | `response_key` | 3,279 | 1.2 MB | nothing |
| `dxbi_heatmap_chessboards_bronze` | per-building floor/unit layout and rental status | `response_key` | 3,279 | 21.1 MB | **nothing** — unit-level layouts collected but unused |
| `dxbi_fam_map_buildings_bronze` | FAM map building/project metadata | `response_key` | 3,280 | 2.0 MB | nothing |
| `dxbi_heatmap_errors_bronze` | failed endpoint responses | `error_key` | 3 | — | nothing |
| `dxbi_transactions_bronze` | legacy APEX report-table cells (`cells_json`) | `transaction_key` | 600 | 47 KB | nothing (last 2026-05-04) |

### 2.2 Evidence layer — off-Dagster, last loaded 2026-09-20

| Table | Holds (grain) | Key | Rows | Size | Coverage | Read by |
|---|---|---|---:|---:|---|---|
| `dxbi_rental_events` (active) | one row per lease contract occurrence (39 columns) | `contract_key` = `event_key` | 1,632,474 | 301 MB | lease start 2025-01-01 → 2026-09-20 | CRM (`opportunity_queries`, `report_queries`), listings benchmarks, radar |
| `dxbi_rental_unit_candidates` | one row per inferred physical unit | `unit_candidate_key` | 888,474 | 104 MB | — | CRM |
| `dxbi_rental_sale_matches` | unit ↔ DXBI sale identity (`unique` / `ambiguous`) | `unit_candidate_key` | 138,212 | 8.7 MB | — | CRM owner, opportunity, report |
| `dxbi_sales_unit_events` | one row per sale **with a visible unit number** | `sale_key` | 986,930 | 60 MB | 2014-01-02 → 2026-09-19 | CRM, listings benchmarks, radar |
| `dxbi_rental_run_reconciliation` | loader ledger per run (raw → unique → loaded) | `report_type, run_id` | 29 | — | 2026-09-15 → 09-20 | nothing (ops only) |
| `dxbi_rental_ingestion_health` (view) | latest rental run health | — | — | — | — | nothing |

### 2.3 Copies the backend never reads

| Table | Rows | Columns | In repo DDL? |
|---|---:|---:|---|
| `dxbi_rental_events_v2` | 1,133,141 | 25 | yes — but the repo defines it with 39 columns |
| `dxbi_rental_events_legacy` | 1,121,091 | 25 | no |
| `dxbi_rental_events_v2_clean` | 2,179,305 | 39 | no |
| `dxbi_rental_unit_candidates_v2` / `_legacy` | 328,298 each | 24 | `_v2` only |
| `dxbi_rental_sale_matches_v2` / `_legacy` | 126,145 each | 18 | no |

**Fact.** The v2 activation (`scripts/activate_dxbi_rental_v2.py:88-92`, `EXCHANGE TABLES`) swapped contents under
the old names; nothing in the repository creates `_legacy` or `_v2_clean`. Live
`dxbi_rental_events` has the 39-column v2 schema and live `dxbi_rental_events_v2` has the old 25-column one — the
**opposite of the repo DDL**. A warehouse built from the repo gets the wrong schema for the CRM's main table.

### 2.4 DXBI data inside shared objects

- `ch_geo_buildings`, `ch_geo_projects` (views over the map layer).
- `geo_crm_rental_hierarchy` (view used by CRM opportunities).
- `geo_source_signatures` / `geo_entity_links` rows with `source_system` `dxbi_rentals` and `dxbi_sales`.

---

## 3. What the product needs from each layer

**Fact** (from `backend/app/**/queries.py` and the frontend routes) + **Recommendation** (last two columns).

| Product feature (route) | Data it needs | Served today by | Can DXBI serve it? | Must stay with |
|---|---|---|---|---|
| Sales transactions (`/transactions/sales`) | every registered sale: date, value, size, area/project/building, type, rooms, usage, reg type, off-plan, procedure | DLD `ch_transactions` (Sales only) | **Mostly** — has unit, building, beds, size, market status; lacks procedure / reg type / parties; 10.4% of sales missing (L1) | DLD: completeness, procedure detail, history before 2014 |
| Rentals (`/transactions/rentals`) | contracts: dates, amounts, area, type, rooms | DLD `ch_rent_contracts` | **Yes from 2025-01-01**, with contract identity DLD open data lacks | DLD: history 2001–2024 (Pulse) |
| Analytics, dashboard (`/analytics`, `/dashboard`) | sale/rent aggregates, project and area facts | DLD + `ch_project_fact` / `ch_area_fact` | aggregates yes (from unified events) | DLD registry for project/developer dimensions |
| Projects, radar (`/projects`, `/radar`) | project registry, buildings, units, developers, map pins, activity | DLD registry + DXBI map + DXBI activity + Reelly + DDA + PF | map pins (3,272 buildings) and activity only | DLD registry (3,413 projects, 122,115 buildings, units), DDA, Reelly, PF locations |
| Developers (`/developers`) | developer registry + sales | DLD developers + facts | no registry | DLD |
| Upcoming off-plan (`/upcoming`) | launches, unit types, price ranges | Reelly (+ unowned tables, F02) | no — DXBI has off-plan *sales*, not launches | Reelly |
| Listings (`/listings`) | live listings + price benchmarks | PF listings + DXBI rentals/sales benchmarks | benchmarks yes (already) | Property Finder |
| CRM (`/crm`, reports) | unit rental history, owner purchase evidence, leads | DXBI evidence + DLD transactions + geography + Postgres imports | **yes — already DXBI-primary** | geography crosswalk; DLD for reports |
| Feasibility (`/feasibility`) | plots, land sales, comparables | DDA + DLD buildings/lands/transactions/rents | comparables partly | DDA, DLD lands/buildings |
| News (`/dashboard`) | articles | Postgres | n/a | news pipeline |

---

## 4. Reconciliation results

### 4.1 Sales — DXBI vs DLD (Q7, Q8)

| Year | DLD sales | DXBI sales | DXBI / DLD |
|---:|---:|---:|---:|
| 2014 | 56,019 | 43,368 | 77.4% |
| 2016 | 42,698 | 31,786 | 74.4% |
| 2018 | 32,199 | 26,703 | 82.9% |
| 2020 | 33,281 | 27,142 | 81.6% |
| 2021 | 59,510 | 46,175 | 77.6% |
| 2022 | 95,735 | 95,065 | 99.3% |
| 2023 | 132,303 | 127,069 | 96.0% |
| 2024 | 179,826 | 165,230 | 91.9% |
| 2025 | 215,116 | 200,673 | 93.3% |
| 2026 (to 09-19) | 128,653 | 119,775 | 93.1% |
| **2014–2026** | **1,101,396** | **986,930** | **89.6%** |

- **Record level (2025):** 95.8% of DXBI (date, price) pairs exist in DLD Sales within ±3 days (160,741 of 167,746);
  69.4% match on the exact day. DXBI sales are DLD sales re-published with unit identity, often dated a few days
  apart. The ~4% unmatched DXBI pairs are unexplained (price differences or records DLD lacks) — **UNKNOWN**.
- Off-plan / ready split agrees closely (2025: DLD 134,688 off-plan vs DXBI 135,427).

### 4.2 Rentals — DXBI vs DLD Ejari (Q5, Q6, Q9)

| Start month | DLD contracts | DXBI active | % | DXBI `v2_clean` | % |
|---|---:|---:|---:|---:|---:|
| 2025-01 | 104,896 | 98,074 | 93.5 | 151,599 | 144.5 |
| 2025-03 | 81,209 | 80,178 | 98.7 | 79,553 | 98.0 |
| 2025-05 | 77,765 | 73,294 | 94.3 | 51,467 | 66.2 |
| 2025-07 | 82,736 | 78,273 | 94.6 | 55,183 | 66.7 |
| 2025-09 | 86,778 | 87,251 | 100.5 | 62,903 | 72.5 |

- Comparable window: **Jan–Sep 2025, DXBI active = 92.2–100.5% of Ejari contracts**. From October 2025 most DLD rows
  come from open data, which carries **no contract numbers**, so contract-level comparison stops being possible (L4).
- **Record level (June 2025):** 85.9% of DXBI (lease start, contract amount) pairs are in DLD; 77.6% on annual amount
  (DXBI annualises differently).
- `v2_clean` swings between 65.6% and 173.5% of Ejari month to month. **The active table is the one that reconciles.**
- 2026: DXBI continues at 38–101k contracts per month; DLD delivers 63–98k open-data rows per month but with no
  contract identity, so it cannot be deduplicated or matched per contract.

### 4.3 Where DXBI is not a substitute

- Rentals before 2025-01-01 (DLD holds 10.5M rent rows back to 2001).
- Sales before 2014 and hidden-unit sales (L1).
- Registries: projects, developers, buildings, units, lands, brokers, valuations — DXBI has none.
- Mortgages and gifts (DLD groups; DXBI sales are sales only).

---

## 5. Data leaks

Ordered along the chain `source → raw → bronze → derived → geography → serving → API`. Magnitudes are from the
snapshot.

| # | Stage | Leak | Evidence | Magnitude | Effect |
|---|---|---|---|---|---|
| L1 | bronze | **DXBI sales loader drops every sale whose unit number is empty or `u-hidden`**; the count is only logged | `scripts/load_dxbi_sales_units.py:335`, `holocron/sources/dxbi/sales_units.py:42` | 114,466 sales 2014–2026 (10.4% of DLD); 0.7–25.6% per year | DXBI-primary sales totals under-count; those units can never join CRM evidence |
| L2 | bronze | Rental events before 2025-01-01 are discarded as out of scope | `load_dxbi_rental_events.py:207, 357` | all history before 2025 | DXBI cannot answer multi-year rental questions |
| L3 | bronze | **Four divergent rental tables**; active vs `v2_clean` share only 1,092,717 source rows; 922,629 only in `v2_clean`, 501,064 only in active | Q4 | ~1.4M rows in dispute | No single agreed rental truth; `v2_clean` and `_v2`/`_legacy` also mislead by name (§2.3) |
| L4 | source/bronze | **DLD open-data rent rows carry no `contract_number`** | Q14: 0 of 805,940 | every rent row from the daily feed (start dates 2017→) | contract-level dedupe, counts and matching are impossible for new DLD rentals |
| L5 | source/bronze | DLD open-data transactions have no building name and no price per m² | Q14: 0 of 157,971 with building | all 2026 DLD sales | building-level analytics lose 2026 unless DXBI supplies it |
| L6 | bronze → serving | **Label drift between DLD sources**: `Mortgages` (Pulse) vs `Mortgage` (open data); `Renew` vs `Renewed` | Q13; `backend/app/market/scope.py:31` filters `Mortgages` | 33,223 open-data mortgage rows invisible to the mortgages scope | silent under-count in mortgage analytics; rent-version filters split by source |
| L7 | bronze | DLD reload duplicates (F03); `ch_*` views do not deduplicate | DATA_SOURCE_HEALTH | rents 96,175; units 41,628; buildings 15,259; projects 219; transactions 190 exact duplicates | inflated counts and sums downstream |
| L8 | extraction | DLD units daily feed adds nothing; all 2.35M rows are Pulse history | DATA_SOURCE_HEALTH | latest 2026-05-16 | unit registry frozen |
| L9 | geography | Rows whose location never maps to a canonical node (`unmapped`), plus unconfirmed `suggested`/`ambiguous` | Q11 | unmapped: DLD 221,394; DXBI rentals 128,144; DXBI sales 87,283. Unconfirmed: 109,485 more | these rows drop out of location features (radar activity, CRM hierarchy) |
| L10 | derived | Only 138,212 of 888,474 rented units (15.6%) have owner-sale evidence; 6,492 of those are ambiguous | Q10 | 750,262 units without purchase evidence | CRM owner coverage capped — driven partly by L1 (hidden-unit sales) and DXBI sales starting 2014 |
| L11 | bronze | DXBI rentals without a building name | Q12 | apartments 52.0%; commercial 81.5%; villas and townhouses 100% | building-level rental analytics and joins weaken; location text remains |
| L12 | source | DXBI map layer covers 3,272 buildings (vs 17,353 building names in DXBI sales; 122,115 DLD buildings); unit layouts (`chessboards`) unused; last refresh 2026-05-27 | Q15 | — | map pins and radar geometry cover a small fraction of the market |
| L13 | raw | Evidence-layer raw data exists only on ph-box / Mac disk; not in object storage; the Holocron raw bucket holds DLD transactions/rents only to 2026-09-09 | DATA_SOURCE_HEALTH | all DXBI evidence | cannot replay or audit DXBI loads outside those machines |
| L14 | bronze | Sales loader parse errors and skips are counted in logs only; rental loader correctly records raw/unique/quarantined per run | `load_dxbi_sales_units.py:328-369` vs Q3 | unknown | sales leaks are unmeasurable after the fact |

**Not a leak (Fact).** The rental loader's deduplication is sound: across 29 runs it quarantined **0** rows, and its
biggest reduction (first run: 94,522 raw → 32,287 unique) is overlapping filter profiles returning the same contracts.

**API layer (Fact).** `/api/v1/transactions/sales` returns 1,377,274 of 1,802,749 bronze rows **by design** — the
backend keeps `trans_group_en = 'Sales'` only (`backend/app/transaction/queries.py:27`); mortgages and gifts are
excluded intentionally. Rentals return all 10,493,181 rows, duplicates included (L7).

---

## 6. Uniform data layer — design (implemented; see §10 for what changed from this design)

**Recommendation.** One canonical layer between bronze and the backend, built with the single publish primitive in
[TARGET_ARCHITECTURE](../architecture/TARGET_ARCHITECTURE.md) (`publish_rebuild`).

### 6.1 Source precedence

| Domain | Primary | Supplements | Why |
|---|---|---|---|
| Sale events | **DXBI** | DLD adds `dld_only` rows (L1 gap) and procedure / reg-type / parties fields | DXBI adds unit identity; 95.8% record agreement |
| Rental events | **DXBI** (2025-01-01 →) | DLD Pulse for history before 2025; DLD open data as volume check | DXBI keeps contract identity DLD open data lacks |
| Unit identity and owner evidence | **DXBI** | DLD units registry | already how the CRM works |
| Building geometry and map | DXBI map | DLD buildings, PF locations | DXBI covers 3,272 buildings only |
| Projects, developers, buildings, lands, brokers, valuations, mortgages, gifts | **DLD** | — | DXBI has none |
| Off-plan catalogue | Reelly | — | only source |
| Plots and planning | DDA | — | only source |
| Listings | Property Finder | DXBI benchmarks | only source |
| Locations (join layer) | geography crosswalk (PF locations) | — | every source is mapped through it |

### 6.2 Canonical tables (serving contract)

- `market_sale_events` and `market_rental_events`: one row per real-world event with
  `event_id`, `event_date`, amounts, size, property type, bedrooms, market status, unit / building / project / area,
  `canonical_location_id`, `primary_source` (`dxbi` / `dld`), `dxbi_key`, `dld_key`,
  `match_status` (`matched`, `dxbi_only`, `dld_only`), `match_confidence`, and lineage (`source_run_id`, `loaded_at`).
- Match rules already proven here: sales = date ±3 days + exact price (95.8%); rentals = lease start + contract amount
  (85.9%). Unmatched rows stay, labelled — never silently dropped.
- Label vocabulary mapped once at this layer (`Mortgage`/`Mortgages`, `Renew`/`Renewed`) — fixes L6 for every consumer.
- Dimensions stay per source (DLD registries, Reelly, DDA, PF) keyed through `canonical_location_id`.

### 6.3 Reconciliation checks (become Dagster asset checks, Standards S5)

| Check | Threshold (proposed) | Today |
|---|---|---|
| DXBI sales ÷ DLD sales, per month | ≥ 90% | 91.9–99.3% since 2022 |
| DXBI sales matched to DLD (±3 days, price) | ≥ 95% | 95.8% (2025) |
| DXBI rentals ÷ Ejari contracts, per month (where DLD has contract ids) | 90–105% | 92.2–100.5% (Jan–Sep 2025) |
| Exactly one rental events table readable by the backend | 1 | 4 (L3) |
| Rows dropped at load, recorded per run (sales and rentals) | recorded | rentals only (L14) |
| Unmapped location share per source | ≤ 5% | 7.8–12.8% (L9) |
| Exact duplicate rows in canonical tables | 0 | L7 |
| Map layer age | ≤ 8 days | 117 days at snapshot |

---

## 7. Pre-conditions and decisions before DXBI can be primary

**Recommendation — surfaced conflicts (AGENTS Rule 1, Rule 5).**

1. **Licensing and access (extends D1).** DXBI is scraped through a logged-in account (`--credentials`,
   `--storage-state` in `dxbi_rental_backfill.py`). Making it the primary layer makes the product dependent on a third
   party whose terms are not recorded. DXBI's sales are DLD data re-published (95.8% match), so DLD must stay as the
   fallback of record. **Decision owner: business/legal.**
2. **Authoritative rental table (new, R-D1).** Evidence favours the active `dxbi_rental_events` (it reconciles with
   Ejari; `v2_clean` does not). Decide, then freeze and archive `_v2`, `_legacy`, `_v2_clean` and the candidate/match
   copies, and align the repo DDL with the live schema (§2.3).
3. **Hidden-unit sales (new, R-D2).** Either keep them in DXBI with an empty unit and a flag (preferred — fixes L1 at
   source), or fill them from DLD as `dld_only` rows.
4. **Rental history before 2025 (new, R-D3).** Backfill DXBI rentals before 2025, or accept DLD Pulse as the history
   source (it already covers 2001–2025).
5. **Bring the evidence pipeline into Holocron (F01, P05/P06).** A primary layer cannot run from a developer Mac with
   raw data only on local disks (L13).

## 8. Next steps (dependency order)

1. Decisions §7.1–7.4.
2. Move the DXBI evidence pipeline into Holocron: raw to object storage, per-run drop and quarantine counts for sales
   and rentals (L13, L14) — MIGRATION_PLAN Phase 5 item for P05/P06.
3. Stop dropping hidden-unit sales at load (L1); retire the rental copies and fix the DDL (L3).
4. Fix DLD at bronze: label vocabulary (L6), duplicate-safe publication (L7, F03), units increment (L8); derive a
   stable rent key for open-data rows since the provider sends no contract number (L4).
5. Build `market_sale_events` / `market_rental_events` with the checks in §6.3.
6. Work the geography review queue for unmapped signatures (L9).
7. Point backend features at the canonical tables one at a time: transactions → analytics → CRM → radar → listings
   benchmarks. Frontend changes only if fields change.

## 9. Unknowns

- **UNKNOWN — REQUIRES ENVIRONMENT:** DXBI raw row counts before load (sales skip totals, rental raw files) — they live
  on ph-box / Mac disk, not in this snapshot.
- **UNKNOWN:** why ~4% of DXBI 2025 sales have no DLD counterpart within ±3 days.
- **UNKNOWN:** how `v2_clean` was produced (no producer in the repository) and why it diverges from the active table.
- **UNKNOWN — requires human confirmation:** DXBInteract terms of use and data licence.

---

## 10. Implementation (local pre-prod, 2026-10-01)

Environment: the handoff snapshot restored on this PC. All three stores were backed up and restore-tested
first (`C:\Users\maan3\vv-backups\`).

### 10.1 What was built

| Piece | Where | What it does |
|---|---|---|
| Canonical sales | `market_sale_events` (+ ledger `market_sale_matches`) | One row per sale: DLD after the Pulse/open-data cut-over and corrections, enriched with DXBI unit identity; DXBI sales DLD lacks are added as `dxbi_only`; unpairable DXBI rows kept as `dxbi_probable_duplicate` (not served). |
| Canonical rentals | `market_rental_events` (+ ledger `market_rental_matches`) | Same for rentals, with location-keyed rank pairing (§10.2). |
| Build | `holocron/holocron/market/`; Dagster job `market_events_refresh` (10:30 Dubai; running by default since 2026-10-06, the only schedule that is, because the served `ch_*` views read its release) | Staging table, `OPTIMIZE FINAL`, `EXCHANGE TABLES`; about 2 minutes for both. |
| Validation | 22 Dagster asset checks (`holocron/holocron/market/checks.py`) | Conservation, uniqueness, labelling, vocabulary, match rate, pair precision, duplicate share, lag. All 22 pass. They run after `EXCHANGE TABLES`, so they observe published data and **do not gate publication** yet (Standards S4.1). |
| Serving | `ch_transactions` / `ch_rent_contracts` = `SELECT * … WHERE is_served = 1` (`backend/scripts/clickhouse/create_compatibility_views.py`) | Every backend query reads the canonical layer without query changes. |
| Facts | `backend/scripts/clickhouse/build_fact_tables.py` (F13 fixed) | Rebuilds from the source view; a second run no longer freezes the facts. |
| DXBI evidence in Holocron | asset `dxbi_evidence_bronze`, job `dxbi_evidence_load` (10:00 Dubai); `holocron/holocron/sources/dxbi/evidence.py` | Loads completed scraper runs from `DXBI_EVIDENCE_DIR`, archives raw files to object storage with sha256 manifests (L13), records loaded / skipped / rejected rows in `dxbi_evidence_load_ledger` (L14), passes credentials by environment. Replaces the Mac `.loaded` marker files. |
| Schema | `holocron/holocron/lib/sql/clickhouse_schema.sql` | Active DXBI rental tables carry the live v2 schema (stale `_v2` DDL removed); DDL added for `silver_reelly_projects` and `reelly_project_unit_types_bronze`, so the schema now applies to an empty warehouse (it failed before). |

### 10.2 Rules the measurements forced (changes from §6)

1. **Row set = DLD; values = DXBI where DXBI knows better.** 96–97% of DXBI sale prices exist in DLD, so
   serving unmatched DXBI rows on top of DLD would double count. A DXBI record is served on its own only
   when DLD has no candidate at all (`dxbi_only`).
2. **Rentals are matched by location.** Dates plus amount alone paired "Al Karama" leases with Business
   Bay contracts. Rentals now pair only within the same DLD area: DXBI area to DLD area from matched
   sales, else the ordinal-normalised name ("Jabal Ali Industrial 1" = "Jabal Ali Industrial First").
3. **Rank pairing instead of candidate joins.** The candidate join produced about 198M rental pairs and
   ran out of memory; rank pairing inside (area, start, end, amount) groups is one-to-one with no fan-out.
4. **DXBI names are mapped to the DLD vocabulary**, learned from matched rows (Dubai Marina to Marsa
   Dubai). Without it DXBI-only rows created about 195 pseudo-areas.
5. **Cut-over validated.** In the January 2026 overlap open data holds 10,327 sales against Pulse's 10,235
   and 51,264 rent contracts against 38,078, so dropping Pulse rows after the cut-over loses nothing.
6. **DXBI sales had 1,270 duplicate versions** (re-normalised addresses); one row per `sale_key` is kept.

### 10.3 Result (served numbers, before and after)

| Figure | Before | After |
|---|---:|---:|
| Duplicate sale IDs served | 4,947 | 0 |
| Sales, January 2026 (overlap double-counted) | 27,669 | 18,114 |
| Mortgages visible to the 2026 mortgages scope | 2,172 | 28,541 |
| 2026 sales with a building name | 8,225 | 109,679 |
| All sales served | 1,377,274 | 1,411,214 (includes 44,439 DXBI-only) |
| Rent rows labelled `Renew` instead of `Renewed` | 4,634,689 | 0 |
| All rentals served | 10,493,181 | 10,334,201 (duplicates and overlap removed; includes 17,120 DXBI-only) |
| Areas in `/api/v1/areas` | 312 | 379 (67 DXBI-only communities DLD does not name) |
| `/transactions/rentals` and `/transactions/sales` latency | 609 ms / 200 ms | 64 ms / 66 ms |

Match quality: 834,976 sales matched (88% of DXBI sales since 2022; 95.8% of pairs agree on size within
1%); 1,133,227 rentals matched (69% of DXBI rentals; 86% agree on size within 5%, all in the same area).

### 10.4 Redundancy removed

27 objects dropped (6.24 GB; 111 to 84 objects): the DXBI rental `_v2`, `_legacy` and `_v2_clean` copies
with their candidate and match copies, the ten `*_local_sync_previous` Mac mirror tables, two abandoned
silver staging tables, seven DLD-derived silver tables with no producer or consumer, and the handoff's
pre-repair Reelly catalogue copy. All are in the verified backup.

Kept and flagged: `silver_transactions` and `silver_rent_contracts` duplicate the canonical layer, but the
entity admin app and the DXBI coverage report read them (decision D6). Reelly asset and payment-plan tables
are not copies; their producer is missing (F02).

### 10.5 Verification

Recorded on 2026-10-01 (historical, not re-measured). The 2026-10-03 re-run of the committed code
(#5) gave Holocron 495 passed on that branch and 497 with the source fixes (#4). The
backend gave 323 passed with 3 Bun skips. ClickHouse integration, Dagster runs and API checks were not
repeated. The §10.3 latencies are single local requests, not p95 load results.

- Holocron: 495 tests pass. Backend fast suite: 323 pass, 3 skipped (they need `bun`). Backend ClickHouse
  integration: 15 pass.
- Dagster `market_events_refresh`: run succeeded, 22 of 22 checks passed.
- Evidence asset, end to end through Dagster on a scratch database and bucket: resumes after a failed sales
  run, idempotent on re-run, hidden-unit sale recorded as skipped, raw files archived.
- Every product API endpoint returns 200 on the canonical layer (see `DATA_SOURCE_HEALTH`), with no server
  errors.

### 10.6 Still open

- The DXBI scraper itself still runs on ph-box. `DXBI_EVIDENCE_DIR` must point at its output on the
  production ingestion host (D4); licence decision D1 applies.
- The DXBI sales loader still skips hidden-unit sales. DLD now supplies them (`dld_only`) and the skipped
  count is recorded.
- Retiring silver waits on D6. The Reelly producer (F02) is still missing.

### 10.7 Update, 2026-10-03 (code ready in draft PRs, not deployed)

- The 22 checks no longer run only after publication: #10 validates the staged release (blocking
  integrity checks and a 99% served-row floor) before `EXCHANGE TABLES` and keeps the previous release.
  DXBI lag, coverage and match-rate checks stay post-publication signals. Real-ClickHouse tests cover
  partial input, invalid vocabulary, corrected DLD records, in-run duplicates and the Pulse cut-over.
- Open-data sales since 2026-01-01 carry `area_id = 0` and a second area vocabulary (`DUBAI MARINA`,
  `JUMEIRAH VILLAGE CIRCLE`, `BUSINESS BAY`) beside Pulse's official areas (`Marsa Dubai`, `Al Barsha South
  Fourth`). #11 resolves communities across both in the new metrics capability; existing area lists and
  rankings still split them until the market layer gets a reviewed crosswalk.

## 11. Status after the Chat 2 data-layer stack (2026-10-03, code ready, not deployed)

- Publication is serialized and recoverable, with a durable ledger, and validation rejects a
  missing week (#15). Readers can pin immutable release
  versions (#20; `MARKET_RELEASE_CONTRACT.md`).
- Coverage is established from load evidence, not event extremes
  (#17).
- The open-data area vocabulary is reconciled through `market_geo_crosswalk`
  (#18; `GEOGRAPHY_CROSSWALK.md`).
- Multi-line DLD transaction numbers had been collapsed to one row: 722 numbers / 3,614 lines.
  They are now served line by line (#23). Served sales rows
  go from 1,829,392 to 1,832,267 on the local copy, and the shared API matches an independent
  bronze query exactly for 1–19 September 2026 (7,363 lines, AED 19,302,851,296).

## 12. Uniform DXBI-primary layer (2026-10-06)

Decision: DXBI is the primary source for sales from 2022-01-01 and rentals from 2025-01-01;
DXBI's value wins on a matched record and DLD fills empty values and the records only DLD
holds. One vocabulary (property type, bedrooms, location, size) for the Transactions and
Analytics pages. Rules, measured effects and the data errors found are in
[UNIFORM_LAYER_CHANGES.md](UNIFORM_LAYER_CHANGES.md) (generated by
`holocron/scripts/report_uniform_layer_changes.py`). Status: code ready and verified on the
local copy; not staging accepted or deployed.
