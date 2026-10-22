# Pipeline-by-Pipeline Audit

**Audit date:** 2026-09-29. **Baseline:** branch `docs/production-first-readiness`, commit `71cf611`.
**Method:** static reading of the repository, plus running the Holocron unit suite
(`uv run pytest`: **469 passed, 0 skipped, 4.3 s**; every test uses in-memory fakes). No live
database, object store, Dagster instance, or provider was accessed, so anything about live state
is marked **UNKNOWN**.

IDs match [PIPELINE_INVENTORY.md](../data-pipelines/PIPELINE_INVENTORY.md). Finding IDs (`F01`…)
match [PRODUCTION_READINESS_AUDIT.md](PRODUCTION_READINESS_AUDIT.md). Scores are against
[PIPELINE_STANDARDS.md](../data-pipelines/PIPELINE_STANDARDS.md):

- **PASS**: meets the standard.
- **PARTIAL**: useful mechanisms exist, but a required control is missing.
- **FAIL**: the control is absent, or the current behaviour can corrupt, lose, or silently degrade data.

Paths are relative to the repository root. `holocron/holocron/...` is the Python package inside
the `holocron/` project.

---

## Cross-cutting facts (apply to every Holocron pipeline unless stated)

These are stated once here and referenced below instead of being repeated.

| # | Observed | Evidence |
|---|----------|----------|
| X1 | Every pipeline has the shape *raw extract → R2 raw files + manifest → bronze loader → ClickHouse*. The manifest is written **only after** all raw files upload, so a crashed extract publishes nothing. This is a good commit point. | `holocron/holocron/orchestration/factory.py:177-327` |
| X2 | All 18 schedules are `DefaultScheduleStatus.STOPPED`. The code cannot tell which are enabled in production. | `factory.py:877-904` |
| X3 | No Dagster `RetryPolicy`, no asset checks, no freshness policies, no run-failure sensors. `SourceSpec.checks` exists but is never populated or read. **Status 2026-10-03:** #5 adds 22 asset checks on the canonical market tables only. They run after the table swap, so they do not gate publication. Retry, freshness and failure sensors remain absent. | `grep -rn "asset_check\|RetryPolicy\|checks=" holocron/holocron` returns nothing (at baseline) |
| X4 | `DefaultRunCoordinator` + `DefaultRunLauncher`: no run queue and no concurrency limits, so two runs of the same job can overlap. | `holocron/dagster.yaml` |
| X5 | Raw → bronze hand-off uses Dagster's default filesystem IO manager (no `io_manager` resource). Pickled outputs live under `DAGSTER_HOME=/app/storage` inside each container. The webserver and daemon are separate containers with no shared volume. | `holocron/holocron/orchestration/definitions.py`, `holocron/docker-compose.yml`, `holocron/Dockerfile` |
| X6 | Every bronze load calls `ensure_schema`, which executes the **entire** `clickhouse_schema.sql` (about 100 statements, including `CREATE OR REPLACE VIEW`) at runtime. The ingestion credential therefore needs DDL rights. | `holocron/holocron/platform/clickhouse_schema.py:50-60` |
| X7 | Bronze publication strategies: (a) `ALTER TABLE … DELETE … mutations_sync=1` then `INSERT` (DLD, DXBI report, PF, Reelly); (b) staging table + `EXCHANGE TABLES` (DDA, PF locations, DXBI heatmap, silver, geography). Strategy (a) is **not atomic**: readers see missing rows between delete and insert, and a crash after the delete leaves them missing until a replay. | `holocron/holocron/platform/bronze.py:93-157`, `clickhouse.py:59-80` |
| X8 | Type coercion silently defaults bad or missing values: `coerce_int("")→0`, `coerce_float("")→0.0`, `iso_datetime_or_default("")→1970-01-01`, `coerce_boolish_uint8` maps anything unrecognised to 1. No rejected-row counts are recorded. | `holocron/holocron/platform/bronze.py:183-257` |
| X9 | Raw integrity is good: SHA-256 and row counts are recorded per file and verified when bronze downloads (`validate_file_sha256`, `validate_manifest_row_count`). Manifests are Pydantic-validated. | `holocron/holocron/platform/raw_files.py` |
| X10 | Config and secret redaction in manifests (`safe_config_metadata`) masks keys containing `password`, `token`, `cookie`, etc. Dagster **run config itself** is stored unredacted in Dagster's Postgres. | `holocron/holocron/platform/metadata.py` |
| X11 | Holocron has **no CI**. The two GitHub workflows cover the backend image build/deploy and backend evaluation tests only. | `.github/workflows/*.yml` |
| X12 | The Dagster webserver binds `0.0.0.0:3000` and has no authentication (Dagster OSS). The README directs operators to `http://<vps-ip>:3000`. | `holocron/docker-compose.yml`, `holocron/README.md` |
| X13 | Logs are Dagster run logs plus stdout. No metrics, no alert routing, no freshness dashboard. | — |

---

## P01 — DLD Open Data (9 endpoint units)

### 1. Purpose
Pulls Dubai Land Department open-data records (sales/mortgage/gift transactions, Ejari rents,
projects, valuations, lands, buildings, units, brokers, developers) as the primary market-data
backbone.

### 2. Data flow
```text
gateway.dubailand.gov.ae/open-data/<command>  (POST JSON, P_TAKE/P_SKIP paging)
 ↓ DldOpenDataApiClient.fetch (httpx, 60 s timeout, 3 linear retries on 5xx/transport)
 ↓ _extract_files → dld_od_<c>_rows.jsonl + dld_od_<c>_pages.jsonl  (date windows × pages)
 ↓ _run_raw_extractor → R2 raw/source=dld_od_<c>/extract_date=…/run_id=…/ + manifests/<run_id>.json
 ↓ write_open_data_checkpoint → R2 state/source=dld_od_<c>/operation=<op>.json
 ↓ load_open_data_release_to_clickhouse (sha/row-count verify → row tuples)
 ↓ ALTER TABLE dld_od_<c>_bronze DELETE WHERE <window predicate>  ; INSERT
 ↓ backend views ch_transactions / ch_rent_contracts / ch_projects / ch_developers / ch_buildings / ch_units
 ↓ analytics, transactions, radar, projects, feasibility, chat tools, CRM, geography, silver
```

### 3. Code locations
- `holocron/holocron/sources/dld_open_data_shared.py` has `DldOpenDataConfig`, `DldOpenDataApiClient`, `_extract_files`, `_request_windows`, `_filter_rows_to_window`.
- `holocron/holocron/sources/dld_open_data_orchestration.py` has the defaults per job, `resolve_open_data_config`, and `write_open_data_checkpoint`.
- `holocron/holocron/sources/dld_open_data_bronze.py` has `dld_row_key`, `load_open_data_release_to_clickhouse`, and `_open_data_delete_predicate`.
- `holocron/holocron/sources/dld_od_<category>/{source,extract,bronze}.py` holds each endpoint's contract and column mapping.
- `holocron/holocron/sources/dld_open_data.md` is the documentation.
- `backend/scripts/clickhouse/create_compatibility_views.py` defines the serving views.

### 4. Triggering
Nine Dagster jobs `dld_od_<c>_daily_incremental` (raw + bronze), cron 04:00–05:30 Asia/Dubai,
**STOPPED** by default (X2). Manual `dld_od_<c>_manual_backfill` / `_manual_snapshot` jobs are
raw-only; bronze for those runs is loaded later through `holocron.platform.bronze_replay`.

### 5. External dependencies
DLD open-data gateway (public web backend), `httpx`, R2, ClickHouse.

### 6. Authentication and secrets
No credential. A fixed `consumer-id` header (`DLD_OPEN_DATA_CONSUMER_ID`) and browser-like
headers copied from the public DLD website are hard-coded (`dld_open_data_shared.py:38-52`). This
is a public web-app identifier, not a licence. **Usage and redistribution rights: requires human
confirmation** (see F04).

### 7. Rate limiting
`request_pause_seconds` defaults to **0**. There is no client-side rate limiter or concurrency
control beyond sequential requests, and 429 is **not** retried (`_retryable_request_error` only
retries ≥500). Provider limits are UNKNOWN.

### 8. Reliability
- Retries: 3 attempts with linear backoff (2 s, 4 s, 6 s), no jitter (`dld_open_data_shared.py:220-244`).
- Timeouts: 60 s per request.
- Resumability: backfill/snapshot chunks resume from R2 checkpoints (`window_cursor`, `skip_cursor`). Rents incremental is checkpointed (`checkpointed_incremental=True`, 7-day overlap). **Transactions and the other seven incrementals are not checkpointed**: they re-read a fixed `lookback_days=3` window. An outage longer than about 3 days leaves a silent gap until someone runs a manual backfill (**F24**).
- Idempotency: see §9. Transactions and rents replace by business date range, so re-running is idempotent. The other seven endpoints are **not idempotent** (**F03**).
- Partial failure: extraction failure publishes nothing (X1). Bronze failure after the DELETE leaves the window empty (X7).

### 9. Data quality
- The response envelope is validated (`response.result` must be a list of objects; `validationErrorsList` and `responseCode` checked).
- There is no row schema validation. Fields are mapped by name and coerced silently (X8), so a renamed provider field becomes 0/"" rather than an error.
- **Duplicate data (F03, confirmed by code reading):** for `projects`, `valuations`, `lands`, `buildings`, `units`, `brokers` and `developers`, the delete predicate is `date_window_start = <start> AND date_window_end = <end>` (`dld_open_data_bronze.py:235-274`, test `holocron/tests/test_dld_open_data_bronze.py:141-143`). The daily window slides by one day (`end=today`, `start=today-3`), so yesterday's rows never match today's delete predicate. Today's run re-inserts the overlapping records under new `row_key` values (`row_key` hashes `row_index`, `dld_open_data_bronze.py:47-60`). These tables are plain `MergeTree` (`clickhouse_schema.sql:573-875`), and the serving views `ch_projects`, `ch_developers`, `ch_buildings`, `ch_units` select without deduplication. **Once the daily schedules run, each record whose date falls in the lookback window accumulates up to about 4 copies.** Whether this has already happened in production is UNKNOWN. Verification query: `SELECT project_number, count() c FROM dld_od_projects_bronze GROUP BY 1 HAVING c>1 LIMIT 10`.
- The incremental date filter semantics for inventory endpoints (`lands`, `units`, `brokers`) are **UNKNOWN**, because their snapshots deliberately omit date filters. Requires provider-behaviour confirmation.
- Freshness: not measured anywhere.

### 10. Transformation
- The raw envelope adds category, window, page and row indexes.
- The bronze mapping renames columns 1:1 from provider fields, with numeric and date coercion.
- `request_to_date_offset_days=1` plus the row-level date filter handles the provider's exclusive `P_TO_DATE`.
- Provenance columns (`source_system`, `source_file`, `source_row_index`, `raw_manifest_s3_key`) are appended.

### 11. Storage
Tables `dld_od_<c>_bronze` (MergeTree), ordered by business keys (e.g. `(instance_date,
transaction_number)`), unpartitioned, with no TTL and no uniqueness guarantee. `units` is ordered by
`(run_id,row_key)`. Rows are scoped by `source_system` so Pulse history (P02) coexists. Raw
releases are immutable in R2; retention is UNKNOWN (no lifecycle config in the repo).

### 12. Downstream consumers
```text
dld_od_*_bronze → backend ch_* views → analytics/radar/transactions/projects/feasibility/chat
               → silver_* (P18, unconsumed)  → geo_source_signatures (P19)  → facts (P20)
```

### 13. Observability
Structured progress logs per page (`dld_open_data: page …`) and Dagster materialisation metadata
(`row_count`, `page_count`, `completed_result_set`). There are no metrics, freshness or anomaly
checks, or alerts (X3, X13).

### 14. Testing
`holocron/tests/test_dld_open_data_sources.py` and `test_dld_open_data_bronze.py` cover
pagination, windows, checkpoints and delete predicates with fakes. The test **asserts the
window-equality predicate** (it encodes the duplication behaviour as intended). There is no
ClickHouse integration test, no provider contract/schema-change test, and no duplicate or overlap
test.

### 15. Security
- Transport is HTTPS. Plain-HTTP ClickHouse is the default (`CLICKHOUSE_SECURE=false` in `holocron/docker-compose.yml`).
- SQL predicates are built with `sql_string`/`quote_clickhouse_identifier` from dates and validated identifiers. Low injection risk.
- The runtime DDL privilege requirement (X6) prevents least privilege.

### 16. Failure modes
| Event | Behaviour |
|-------|-----------|
| Source unavailable | 3 retries then the run fails; nothing published; transactions gap if outage > 3 days (F24) |
| Format change | Envelope change fails loudly; field rename silently yields 0/""/1970 |
| Timeout | Retried (transport); then fails |
| Rate limit (429) | Not retried, so the run fails |
| Auth expiry | N/A (no auth); a consumer-id revocation would present as 4xx → fail |
| Malformed data | Non-object rows fail the run; bad values coerced silently |
| DB unavailable | Bronze step fails; raw already in R2; re-run bronze (subject to X5) or replay |
| Validation fails | No validation gate exists |
| Crash mid-bronze | DELETE done, INSERT not done, so the window is missing until re-run (X7) |
| Duplicate execution | Transactions/rents idempotent; other seven endpoints duplicate rows (F03); overlapping runs possible (X4) |

### 17. Production readiness
```text
Reliability:     PARTIAL  retries/timeouts/checkpoints exist; no 429/backoff jitter; transactions gap risk
Observability:   PARTIAL  good logs/metadata; no freshness, alerts, metrics
Security:        PARTIAL  no secrets needed; DDL-privileged writer; plaintext CH default; licence unconfirmed
Data Quality:    FAIL     duplicates on 7 endpoints; silent coercion; no checks
Recoverability:  PARTIAL  immutable raw + replay tool; non-atomic publish; IO-manager hand-off (X5)
Testing:         PARTIAL  good unit coverage; tests encode the duplicating predicate; no integration
Maintainability: PASS     one shared implementation; endpoints are declarative
Scalability:     PARTIAL  sequential paging fine for daily; full-table mutations on unpartitioned tables (F20)
```

---

## P02 — Dubai Pulse historic archive (one-time)

- **Purpose:** archive and load historical DLD CSV/KML files that predate the open-data API into the same bronze tables (`source_system='dubai_pulse'`).
- **Flow:** local `pulse-data/` directory → `dld_pulse_historic_raw` (raw-only job `dld_pulse_historic_archive`) → R2 → `dld_pulse_historic_bronze` asset (job `dld_pulse_historic_bronze_backfill`, config `manifest_key`, `batch_size`) → `dld_od_*_bronze`. Uses staging + `EXCHANGE TABLES` (`sources/dld_pulse_historic/bronze.py:676`). Scratch is `HOLOCRON_SCRATCH_DIR` or `./var/scratch`.
- **Code:** `holocron/holocron/sources/dld_pulse_historic/{source,extract,bronze,assets}.py`; `platform/dld_provenance_backfill.py` (one-off provenance column backfill).
- **Trigger:** manual only. **Auth:** none (local files). **Rate limits:** N/A.
- **Reliability:** batch inserts to staging, then exchange (atomic). One-time, so re-run replaces.
- **Data quality:** row counts recorded; file checksums verified. Licence and provenance of the archive files: *requires human confirmation*.
- **Consumers:** same as P01 (history before the open-data cutoff; `silver/transforms.py` uses an `open_data_cutoff`).
- **Tests:** `test_dld_pulse_historic.py`, `test_dld_pulse_historic_bronze.py`, `test_dld_provenance_backfill.py`.
- **Failure modes:** a crash before exchange leaves the staging table and the live table unchanged (good).
- **Readiness:** Reliability PASS · Observability PARTIAL · Security PASS · Data Quality PARTIAL · Recoverability PASS · Testing PARTIAL · Maintainability PASS · Scalability PASS (one-time).

---

## P03 — DLD Mashrooi (manual, raw-only, demo consumer)

- **Purpose:** project list and detail payloads with project/building/land coordinates.
- **Flow:** `b2c.dubailand.gov.ae/mashrooi` (session `Token` header echoed from responses, `dld_mashrooi/extract.py:47-81`) → `dld_mashrooi_raw` → R2. No bronze. `backend/scripts/radar/import_mashrooi_coordinates_demo.py` materialises `demo_mashrooi_project_coordinates` / `demo_mashrooi_detail_points` (drop/truncate + insert). **No reader in `backend/app`.**
- **Trigger:** manual asset materialisation (no job, no schedule).
- **Reliability:** no retries; `continue_on_detail_error=True` swallows detail failures (logged); pause default 0.
- **Security:** same hard-coded public consumer-id as P01.
- **Status:** demo. Decide whether radar needs coordinates (then promote to a bronze table under the standard) or retire (F21).
- **Readiness:** Reliability FAIL · Observability PARTIAL · Security PARTIAL · Data Quality FAIL · Recoverability PARTIAL · Testing PARTIAL (`test_dld_mashrooi_source.py`) · Maintainability PARTIAL · Scalability N/A.

---

## P04 — DXB Interact map (Dagster `dxbi_transactions`)

### 1. Purpose
Captures the DXB Interact rental/property map: rental-building discovery, per-building details,
status, chessboard (floor/unit layout), and FAM map metadata. The legacy `report_tables` mode
captures sales/rental tables. The source name is historical (`sources/dxbi/README.md`).

### 2. Data flow
```text
dxbinteract.com (Playwright Chromium, headful by default, optional storage state)
 ↓ in-page fetch with a locally generated client signature (_heatmap_client_signature)
 ↓ hCaptcha detection → 2Captcha solve (TWOCAPTCHA_API_KEY / CAPTCHA_API_KEY)
 ↓ dxbi_heatmap_*.jsonl, dxbi_fam_map_buildings.jsonl, dxbi_heatmap_errors.jsonl
 ↓ R2 raw + manifest
 ↓ bronze: EXCHANGE full replace of each heatmap table present; report rows delete-by-slice + insert
 ↓ ch_geo_buildings view (JSON extract of payload) → ch_geo_projects → backend projects/radar map
```

### 3. Code
`holocron/holocron/sources/dxbi/{source,extract,bronze}.py` (`extract.py` is 1,281 lines;
`_maybe_solve_captcha` at 1162, `_virtual_display_if_needed` at 682).

### 4. Trigger
Dagster `dxbi_transactions_daily_incremental` 06:00 Asia/Dubai (STOPPED by default); manual backfill (raw-only).

### 5–6. Dependencies, auth and secrets
Playwright, Chromium, Xvfb (headful), `twocaptcha` SDK, optional browser storage-state file.
Credentials come from env or run config. **CAPTCHA solving and client-signature generation
circumvent the provider's access controls** (F04).

### 7. Rate limiting
`heatmap_request_pause_seconds=0.5` between endpoint calls; 30 s per-fetch timeout; sequential.
Provider limits are UNKNOWN.

### 8. Reliability
- **Scheduled job likely fails in the container (F11):** `headless=False` by default, so without `DISPLAY` it needs Xvfb, and the `holocron/Dockerfile` does not install Xvfb. The schedule's run config sets only `mode`. *Verify by launching once.*
- The whole extraction runs in one browser session with no chunking or checkpoint (README "Operational Notes" acknowledges this). A crash loses the whole run.
- Endpoint failures are written to `dxbi_heatmap_errors.jsonl` and the run continues.

### 9. Data quality
The heatmap tables are **fully replaced by whatever a run fetched**. A run that errors on many
buildings, or a smoke run with `max_heatmap_buildings`, still exchanges into the live tables. No
row-count floor and no smoke marker is set by this source (no code sets `metadata.smoke`) (F08).

### 11. Storage
`dxbi_heatmap_*_bronze`, `dxbi_fam_map_buildings_bronze` (MergeTree, JSON payload strings);
`dxbi_transactions_bronze`.

### 12. Consumers
`ch_geo_buildings` parses `payload_json` at query time, then `ch_geo_projects` (project centroids
by lower-cased name join). Consumed by backend projects/radar maps.

### 13–15. Observability, tests, security
- Progress logs every `heatmap_progress_interval` buildings.
- Tests: `test_dxbi_source.py`, `test_dxbi_bronze.py` (fakes).
- Security: CAPTCHA key and storage state are secrets; storage state is a live session.

### 16. Failure modes
- Blocked AJAX: `_raise_if_blocked_ajax` fails the run.
- CAPTCHA without a key: fails.
- Crash: nothing published.
- Partial fetch: published as the full truth.

### 17. Readiness
```text
Reliability: FAIL  Observability: PARTIAL  Security: FAIL (access-control circumvention, live sessions)
Data Quality: FAIL Recoverability: PARTIAL Testing: PARTIAL Maintainability: PARTIAL Scalability: FAIL (single session, no chunks)
```

---

## P05 — DXB Interact rental and sales report scrapes (systemd, outside Dagster)

### 1. Purpose
Collects DXBI rental contracts and sales reports that feed CRM owner/tenant evidence: unit
candidates, rental events, sales unit events, and rental↔sale matches.

### 2. Data flow
```text
ph-box systemd timers (User=root)
 ↓ /usr/local/sbin/dxbi-report-daily  (deploy/systemd/dxbi-report-daily.sh; flock; timeout 3h/12h/24h)
 ↓ holocron/scripts/dxbi_rental_backfill.py (1,670 lines; Playwright; --credentials /root/.config/vitevue/dxbi/account-{a,b}.json;
   --storage-state …; delay 2–4 s; writes run folder + _SUCCESS/_FAILURE markers)
 ↓ /root/data/dxbi-daily/{rentals,sales}/run=<date>-<cadence>/   (host disk, not R2)
 ↓ developer Mac launchd 09:00 → sync_market_data → scripts/sync_dxbi_daily_runs.sh
 ↓ rsync over SSH from a hard-coded root@<ip> (5 attempts)
 ↓ scripts/load_dxbi_rental_events.py --target active   (ReplacingMergeTree, staging+EXCHANGE, reconciliation JSON)
 ↓ scripts/load_dxbi_sales_units.py                      (inserts + OPTIMIZE FINAL; match table staging+EXCHANGE)
 ↓ scripts/refresh_geography_crosswalk.py
 ↓ Mac-local ClickHouse: dxbi_rental_events(_v2), dxbi_rental_unit_candidates(_v2), dxbi_sales_unit_events,
   dxbi_rental_sale_matches, dxbi_rental_run_reconciliation
 ↓ backend CRM (owners, ownership, opportunities, reports), chat CRM tools
```

### 3. Code
- `holocron/deploy/systemd/*.service|*.timer`, `dxbi-report-daily.sh`
- `holocron/scripts/dxbi_rental_backfill.py`, `load_dxbi_rental_events.py`, `load_dxbi_sales_units.py`, `activate_dxbi_rental_v2.py`, `report_dxbi_rental_coverage.py`, `sync_dxbi_daily_runs.sh`
- `holocron/holocron/sources/dxbi/{rental_events,rental_coverage,sales_units}.py`
- `holocron/docs/runbooks/dxbi-rental-v2.md`, `local-market-data-sync.md`

### 4. Trigger
- Sales: daily 22:30 UTC.
- Rentals: daily 00:30 UTC (14-day lookback), weekly Sunday 02:00 (days 15–180), monthly day-1 03:00 (days 181–365).
- The Mac load runs daily at 09:00 local and only if the laptop is awake and online.

### 5–6. Auth and secrets
- Two provider accounts (`account-a` sales, `account-b` rentals).
- Credential JSON and browser storage state are plain files under `/root/.config/vitevue/dxbi/`, and services run as root.
- `sync_dxbi_daily_runs.sh` hard-codes a remote `root@<ip>` (a public IP) and SSH `BatchMode`. Keys live on the developer machine.
- Commercial right to automate the licensed accounts: *requires human confirmation* (F04).

### 7. Rate limiting
2–4 s random delay between requests; one scrape per report type via `flock`.

### 8. Reliability
- Good local discipline: flock, bounded timeouts, `_SUCCESS`/`_FAILURE` markers, idempotent load markers (`.loaded/`), a 36-hour health check, ReplacingMergeTree with staging + EXCHANGE, and a v1/v2 reconciliation gate (`activate_dxbi_rental_v2.py`).
- **Structural failure (F01):** publication depends on a developer laptop. The repository contains **no path that loads these tables into the production ClickHouse.** Whether production is loaded manually, by an unrecorded process, or not at all is **UNKNOWN**. *Requires human confirmation.*
- Raw run folders live on one host's disk, not in R2, so host loss means data loss.

### 9. Data quality
Reconciliation JSON per load, minimum-row acceptance for repairs (`rental_repair_queue.accepted_run`), and coverage reports against DLD. This is the most mature validation in the repo. It runs only on the Mac.

### 11. Storage
Tables are defined **twice**: in `holocron/holocron/lib/sql/clickhouse_schema.sql:142-372` and
inline `CREATE TABLE IF NOT EXISTS` in the loader scripts. That is a drift risk (F02).

### 13. Observability
Log files on ph-box and the Mac, plus `DLD-Sync/automation-status.json` on the Mac. The health
failure only goes to stderr. No alerting.

### 14. Tests
`test_dxbi_rental_*.py`, `test_dxbi_sales_units.py`, `test_rental_repair_queue.py`,
`test_market_sync_automation.py` (unit, fakes).

### 16. Failure modes
| Event | Behaviour |
|-------|-----------|
| Laptop off | No publication; health check only runs when the laptop runs |
| ph-box disk loss | Unpublished raw runs lost |
| Session expiry / CAPTCHA | Run fails → `_FAILURE` marker; no alert |
| Duplicate execution | flock prevents concurrent scrapes; load markers prevent reloads |

### 17. Readiness
```text
Reliability: FAIL (laptop-dependent publication)  Observability: FAIL  Security: FAIL (root, plaintext creds, circumvention)
Data Quality: PARTIAL (good reconciliation, Mac-only)  Recoverability: FAIL (raw not in object storage)
Testing: PARTIAL  Maintainability: FAIL (second orchestration stack, duplicated DDL)  Scalability: PARTIAL
```

---

## P06 — DXBI historical rental repair queue

- **Purpose:** re-scrape 396 historical dates to fill 2025/March-2026 v2 coverage gaps.
- **Flow:** `dxbi-rentals-repair.timer` (every minute after inactivity) → `/root/data/dxbi-rental-repair-20260915/rental_repair_queue.py` with `PYTHONPATH=/root/data/dxbi-rental-v2-experiments-20260905/code` → per-attempt raw under `/root/data/...` → Mac `holocron.platform.sync_rental_repairs` imports only verified runs.
- **Key finding (F14):** the running service executes **a copy of the scraper and queue outside version control**. The repo copies (`holocron/holocron/platform/rental_repair_queue.py`, `holocron/scripts/dxbi_rental_backfill.py`) may differ. What actually runs is UNKNOWN.
- **Readiness:** Reliability PARTIAL · Observability FAIL · Security FAIL · Data Quality PARTIAL (verified-run gate) · Recoverability FAIL · Testing PARTIAL · Maintainability FAIL · Scalability N/A (finite).

---

## P07 — DXBI legacy JS backfill runners

- **What:** `holocron/scripts/dxbi_backfill_runner.js`, `dxbi_recursive_shard_runner.js`, `dxbi_sales_dynamic_bucket_runner.js`, `dxbi_sample_scrape.js`, `dxbi_export_*.js`, zsh supervisors/watchdog with `/Users/eier/...` defaults, `load_dxbi_normalized_sales_csv.py`.
- **Status:** historical/one-time backfill tooling run on a Mac in tmux. There is no scheduler reference in the repo.
- **Risk:** a second language and runtime for the same provider; hard-coded personal paths.
- **Recommendation:** freeze. Record which tables and date ranges it produced, then remove in Phase 6 once the P05 replacement has proven coverage.
- **Readiness:** FAIL on Reliability, Observability, Maintainability. N/A for scheduled operation.

---

## P08 — Property Finder locations

- **Purpose:** the PF Dubai location hierarchy; the canonical geography backbone for P19.
- **Flow:** `propertyfinder.ae` Next.js JSON (httpx, 60 s timeout, pause 0) → `pf_locations_raw` → `pf_locations_bronze` via staging + EXCHANGE full replace → `geo_location_nodes` → `geo_location_closure` → every hierarchy consumer.
- **Trigger:** manual `pf_locations_manual_full_fetch`.
- **Reliability:** no retries in extract (0 retry references in `pf_locations/`); full replace is atomic but has **no row-count floor**, so a truncated location tree would replace the good one (F08).
- **Tests:** `test_propertyfinder_sources.py`, `test_propertyfinder_bronze.py`.
- **Readiness:** Reliability PARTIAL · Observability PARTIAL · Security PASS · Data Quality PARTIAL · Recoverability PASS · Testing PARTIAL · Maintainability PASS · Scalability PASS.

---

## P09 — Property Finder listings

### 1–4. Purpose, flow, code, trigger
Current PF inventory for listings search, radar and CRM inbound context.

- **Daily** `pf_listings_daily_latest_delta` 03:00 Asia/Dubai: newest-first search, stop after 2 already-seen pages, up to 250 details. State lives in `state/source=pf_listings/operation=latest_delta_inventory.json`.
- **Full crawl:** manual `plan` → `search` chunks (500 pages/run) → `details` chunks (1,000/run), each checkpointed. Driven by `holocron/scripts/run_propertyfinder_full_dubai.sh`.
- **Code:** `holocron/holocron/sources/pf_listings/{source,extract,orchestration,bronze,media_assets}.py` (`extract.py` is 2,765 lines).

### 5–7. Dependencies, auth, rate limits
- Playwright headless Chromium, in-page `fetch` of `_next/data/<buildId>` JSON. Optional storage state.
- `_MAX_FETCH_ATTEMPTS` retries per fetch.
- `request_pause_seconds` default **0**; no concurrency (single page).
- Provider limits and terms: UNKNOWN (F04).

### 8. Reliability
Chunked, checkpointed, resumable (good). Checkpoints are written after the manifest (factory).
Overlapping runs of the same chunk job are not prevented (X4) and could race on the checkpoint.

### 9. Data quality
- **Stale inventory (F18):** bronze is `replace_keyed_rows` by `property_key` (delete keys, insert). Nothing marks a listing withdrawn, so a delisted property keeps its last `is_available=1` row indefinitely. The API's freshness indicator is `max(scraped_at)` (`backend/app/listings/queries.py:66-70`), which looks fresh even when most rows are old.
- A manual full replacement exists: `holocron/scripts/replace_pf_bronze_snapshot.py`.

### 11. Storage
`pf_listings_bronze` ordered by `(run_id, property_key)` and `pf_listing_details_bronze`, with JSON
columns parsed at query time (`JSONExtract…` in `listings/queries.py`).

### 12. Consumers
Backend listings API, radar, CRM inbound, export scripts `backend/scripts/listings/*`.
`import_pf_listings_demo.py` writes a PG `demo_pf_listings` table that no app code reads (F21).

### 14. Tests
`test_pf_listings_orchestration.py`, `test_propertyfinder_*`, backend `integration/clickhouse/test_listings.py`.

### 17. Readiness
```text
Reliability: PARTIAL  Observability: PARTIAL  Security: PARTIAL (session state; terms unconfirmed)
Data Quality: FAIL (no withdrawal handling; misleading freshness)  Recoverability: PASS (checkpoints + raw)
Testing: PARTIAL  Maintainability: PARTIAL (2.7k-line extractor)  Scalability: PARTIAL
```

---

## P10 — Property Finder listing media

- Manual `pf_listing_media_assets_backfill` only; excluded from the daily job by design (`factory.py:762-771`).
- Downloads through `validated_stream_get` with host allowlist, size limit, redirect re-validation, and inventory manifests (`platform/hydration.py`). No backend consumer found. Media licence: *requires human confirmation*.
- **Readiness:** Reliability PARTIAL · Observability PASS (inventory counts) · Security PARTIAL (F17) · Data Quality PASS · Recoverability PASS · Testing PARTIAL · Maintainability PASS · Scalability PASS.

---

## P11 — Reelly supply (catalogue, availability, archive)

### Flow
```text
api.reelly.io / Xano endpoints  (POST /auth/login0 with REELLY_EMAIL/REELLY_PASSWORD → bearer token)
 ↓ catalog_delta (200 pages/run, details + document metadata for changed projects; state catalog_inventory.json)
 ↓ availability_delta (matrix floors/bedrooms)
 ↓ archive chunks (5 pages/run, checkpointed)
 ↓ R2 raw → reelly_projects_bronze / reelly_documents_bronze / reelly_matrix_availability_bronze
   (replace_keyed_rows: delete keys, insert; tables ordered by (run_id, key))
 ↓ backend supply (LIMIT 1 BY project_id ORDER BY scraped_at DESC), projects, radar; geography offplan links
```

### Findings
- **Auth:** `REELLY_EMAIL`/`REELLY_PASSWORD` from env *or run config* (`reelly_supply/extract.py:1547-1551`). Run config is stored unredacted in Dagster Postgres (X10).
- **Rate limiting/reliability:** **no retries** in the extractor; `request_pause_seconds` default 0; 60 s timeout. A single transient error fails the chunk. Resumable through checkpoints.
- **Contract drift (F02, confirmed):** Holocron's DDL for `reelly_projects_bronze` has 7 columns (`clickhouse_schema.sql:1119-1128`). The backend selects columns that do not exist there, such as `project_name`, `developer_name`, `sale_status` and `min_price_aed` (`backend/app/supply/queries.py:20-50`). Its integration fixture creates the table with extra `region`, `area_name`, `latitude` and `longitude` columns (`backend/tests/integration/clickhouse/test_supply.py:8-12`). The backend also depends on `reelly_project_unit_types_bronze` and `silver_reelly_projects`, which **nothing in the repo creates or populates** (P23). The live warehouse therefore has columns or tables outside source control (inferred; `holocron/holocron/platform/sync_dld.py` also explicitly skips `ALIAS` columns). *Confirm with `SHOW CREATE TABLE` on the live warehouse.*
- **Consumers tolerate history:** the backend picks the latest row per project, so duplicate runs are harmless to reads but grow the table unboundedly (no retention).
- **Tests:** `test_reelly_supply.py`, `test_reelly_bronze.py`, `test_reelly_document_assets.py`, backend `test_supply.py` (fixture schema does not match Holocron DDL).
- **Readiness:** Reliability FAIL (no retries) · Observability PARTIAL · Security PARTIAL · Data Quality FAIL (contract drift) · Recoverability FAIL (cannot rebuild serving tables from repo) · Testing PARTIAL · Maintainability PARTIAL · Scalability PARTIAL.

---

## P12 — Reelly documents and project media

- Runs inside `reelly_supply_daily_catalog_delta` (latest manifest only) plus manual backfills.
- Uses the P10 hydration framework, max 8 concurrent downloads, and a size limit.
- The host allowlist includes `drive.google.com`, `.googleusercontent.com` and `storage.googleapis.com` (`reelly_supply/source.py`), which are broad hosts serving arbitrary user content. That is acceptable only with a size and content-type limit, which is present (`max_document_mb=100`).
- Licence to mirror developer documents: *requires human confirmation*.
- **Readiness:** Reliability PARTIAL · Observability PASS · Security PARTIAL · Data Quality PASS · Recoverability PASS · Testing PARTIAL · Maintainability PASS · Scalability PASS.

---

## P13 — DDA planning layers

### Flow
```text
dda.gov.ae DIS page → scrape "AGSToken" from HTML (_fetch_token)
 ↓ ArcGIS REST layers (paged, token refresh) + per-plot HTML "PlotInfo" pages (20 threads)
 ↓ 11 CSV files → R2 → bronze staging + EXCHANGE per table (full replace)
 ↓ dda_land_plots → ch_gis_land_plots → plots, feasibility, DDA valuation
```

### Findings
- **Silent partial data (F08, confirmed):** a plot page that fails 3 attempts (exponential 2 s/4 s) returns the stub `base` row with only the list attributes (`dda/extract.py:188-209`). It is written as a normal row, and the full-replace publish then **overwrites good plot details with stubs**. There is no failed-plot count in the manifest and no row or completeness floor.
- A "WAF block" is detected only by a missing marker string.
- **Concurrency:** 20 concurrent requests against a government site by default. The limited paid quota mentioned in `sources/dda/README.md` is not enforced in code.
- `max_plots` (smoke) with bronze enabled would replace the full table with a sample. The manual job `dda_planning_layers_manual_full_refresh` includes bronze.
- Scheduled weekly Sunday 06:00 Asia/Dubai (STOPPED by default). The `cadence="0 7 * * 1"` in `SourceSpec` disagrees with the schedule. The field is informational only.
- **Tests:** `test_dda_source.py`, `test_dda_bronze.py`.
- **Readiness:** Reliability PARTIAL · Observability PARTIAL · Security PARTIAL · Data Quality FAIL · Recoverability PASS (atomic swap, raw kept) · Testing PARTIAL · Maintainability PASS · Scalability PARTIAL.

---

## P14 — News feeds (poll, thumbnails, publish to PostgreSQL)

### Flow
```text
15 default RSS/Atom feeds (or NEWS_FEED_URLS / config)
 ↓ news_feeds_raw (per-feed failures recorded as poll rows, run continues)
 ↓ news_thumbnail_assets (latest manifest; allowlist optional/empty by default; optional article-page fetch)
   ── scheduled job news_feeds_poll_and_hydrate every 15 min STOPS HERE ──
 ↓ news_publish (manual job news_publish_backfill; 3-day lookback; ensure_schema=True)
 ↓ psycopg → PLATFORM_POSTGRES_URL → news_articles (CREATE TABLE IF NOT EXISTS + ALTER … ADD COLUMN …, indexes)
 ↓ backend /api/v1/news → frontend news cards
```

### Findings
- **Not scheduled to publish (F12, confirmed):** the scheduled job selects only `news_feeds_raw` and `news_thumbnail_assets` (`factory.py:799-803`; `news_feeds/README.md` "Runtime Flow"). The frontend feed is refreshed only when an operator runs `news_publish_backfill`. This **conflicts with** `production-readiness-2026-09-29.md` §10, which says the scheduled job publishes to PostgreSQL. The code is the source of truth.
- **Schema ownership conflict:** Holocron issues DDL against the application database (`_ensure_news_articles_schema`, `news_feeds/assets.py:479-527`). It widens `url`/`headline`/`thumbnail_url` to `text` and adds 12 columns. Alembic declares `varchar(255)` and 6 columns (`backend/migrations/versions/8395bd1f2518_baseline_schema.py:40-50`, `backend/app/news/models.py`). A future Alembic autogenerate would try to reverse Holocron's changes.
- **Transaction bug (likely, verify):** each article runs in `try/except` inside one psycopg transaction without savepoints (`assets.py:403-469`). After the first failing statement PostgreSQL aborts the transaction and every later statement fails. `connection.commit()` then rolls back, including rows already counted as `created`/`updated`.
- **Not an upsert:** `UPDATE … WHERE article_key=%s OR url=%s`, then `INSERT` if no row. There is no unique constraint, so concurrent or overlapping runs can double-insert (X4).
- **Three writers** to `news_articles`: this job, backend `POST /api/v1/upload/news/article` (P15), and `backend/scripts/news/import_from_s3.py` (P15), with different normalisation rules.
- **SSRF:** thumbnail host allowlist empty by default, so any public host is fetched. The private-IP check is literal-host only (F17).
- **Licensing:** RSS availability ≠ republication right (headline, summary, thumbnails). *Requires human confirmation.*
- **Tests:** `test_news_feeds_assets.py`, `test_news_feeds_source.py`, backend `integration/postgres/test_news_ingestion.py`.
- **Readiness:** Reliability FAIL (publish unscheduled; transaction handling) · Observability PARTIAL · Security PARTIAL · Data Quality PARTIAL (dedupe by key/URL) · Recoverability PASS (raw in R2, re-publishable) · Testing PARTIAL · Maintainability FAIL (three writers, dual schema ownership) · Scalability PASS.

---

## P15 — News push/import into the app

- `POST /api/v1/upload/news/article` (service API key, `backend/app/upload/router.py:62-71`) and `backend/scripts/news/import_from_s3.py` both write `news_articles` through `backend/app/news/ingestion.py` (dedupe by URL, insert only new).
- No in-repo caller of the endpoint. It may be an external legacy ETL: *requires human confirmation*.
- **Recommendation:** make one writer authoritative (see MIGRATION_PLAN Phase 5E) and remove or disable the others.
- **Readiness:** Maintainability FAIL (duplicate writer); others PARTIAL.

---

## P16 — Backend chunked ETL upload ingress

- `POST /api/v1/upload/chunked` stores chunks in R2 and tracks receipts in a Redis set (1 h TTL). On the last chunk it writes `metadata.json`. The docstring says an "ETL node" assembles, but **no assembler exists in the repository**, the declared `expected_hash`/sizes are not verified server-side, and there is no caller in the repo. Health endpoint `/api/v1/upload/health` is public and writes/deletes an object.
- **Recommendation:** disable unless an owner confirms a consumer (F21; reference doc §3 and §14 agree).
- **Readiness:** FAIL across Reliability, Data Quality, Recoverability, Testing (only storage test). Security PARTIAL (service key).

---

## P17 — Bayut listings (experimental)

- `sources/bayut_listings/` (stops on CAPTCHA by design, `source.py` docstring) plus probe and full-collection machinery (`full_collection.py` 1,544 lines, `probe.py` 1,022 lines).
- `holocron/scripts/configure_bayut_proxy.zsh`, DataImpulse proxy example configs, and a **tracked** `holocron/handoff/bayut_standalone/` copy plus `bayut_standalone_handoff.zip`, which duplicate the code.
- No bronze table, no job, no consumer. Proxy rotation to avoid blocking is an access-control concern (F04).
- **Recommendation:** keep out of the production supply chain. Move the handoff artifacts out of the repo (Phase 6).
- **Readiness:** not production-scoped; Maintainability FAIL (duplicated code in `handoff/`).

---

## P18 — Silver projections

- `silver_transactions` / `silver_rent_contracts` are rebuilt fully from bronze via staging + `EXCHANGE` (`holocron/holocron/silver/transforms.py:302-317`). This is atomic, but the Dagster path has **no validation** before the swap. The local-mirror path (`platform/sync_silver.py::validate_replacement`) has date-coverage validation and checksums. **Two implementations of the same publication with different safety.** Following AGENTS Rule 5, adopt the validated local pattern as the standard and flag the other for cleanup.
- **Schedule overlap:** `silver_transactions_refresh` 06:00, `silver_rent_contracts_refresh` 06:10 **and** `silver_refresh` 06:00 all exist. Enabling all would run `silver_transactions` twice concurrently on the same staging table name (`silver_transactions_staging`), which risks drop-while-inserting failures. Cron offsets are not dependencies: if bronze runs late, silver publishes stale data.
- **Unconsumed:** nothing in `backend/app` reads `silver_*` (serving views read bronze). Holocron architecture prose says silver is deferred.
- **Readiness:** Reliability PARTIAL · Observability PARTIAL · Security PASS · Data Quality PARTIAL · Recoverability PASS · Testing PARTIAL (`test_sync_silver.py`) · Maintainability FAIL (two paths, overlapping schedules) · Scalability PARTIAL.

---

## P19 — Geography crosswalk and off-plan links

- Five Dagster assets (`assets/geography.py`) with a deterministic resolver (`domain/geography.py`, `NORMALIZATION_VERSION = 10`) and off-plan matcher (`domain/offplan_projects.py`). Published via `replace_table_rows` (EXCHANGE). Manual override tables `geo_entity_link_overrides` and `offplan_reelly_project_link_overrides` (ReplacingMergeTree by `reviewed_at`) are **hand-curated state that exists only in ClickHouse**, so they need explicit backup (reference doc §7).
- **Triggers:** Dagster job `geography_crosswalk_refresh` (unscheduled) and `holocron/scripts/refresh_geography_crosswalk.py` run by the Mac sync after DXBI loads.
- Depends on `silver_reelly_projects` (P23, unknown producer).
- `holocron/apps/entity_admin/app.py` (Streamlit) edits `holocron/holocron/lib/entities/*.yaml` and launches Dagster runs via GraphQL. **No pipeline code reads those YAML files** (`grep area_aliases.yaml` matches only the admin app), so those edits have no effect on data. Requires owner confirmation.
- **Readiness:** Reliability PARTIAL · Observability PARTIAL (status counts in metadata) · Security PASS · Data Quality PARTIAL (candidate/evidence retained, overrides tracked, but no gate on link-rate regressions) · Recoverability PARTIAL (overrides not backed up) · Testing PARTIAL (`test_geography.py`, `test_offplan_projects.py`) · Maintainability PARTIAL · Scalability PARTIAL (in-memory resolver over all signatures).

---

## P20 — Backend serving layer (compatibility views and facts)

- `create_compatibility_views.py` issues `CREATE OR REPLACE VIEW` for 11 `ch_*` views over **bronze**, including the heavy `ch_project_fact` / `ch_area_fact` aggregations. It uses the backend app's ClickHouse credentials, so the API credential needs DDL.
- `build_fact_tables.py` (confirmed, matches reference §8):
  - The first run materialises facts and renames the views to `*__source_view`.
  - A second run **drops** `*__source_view` (the original view definitions), then materialises from the already-frozen tables. The facts are frozen permanently.
  - After the first run, `create_compatibility_views.py` would fail on `CREATE OR REPLACE VIEW ch_project_fact`, because that name is now a table.
  - It uses `clickhouse_driver`, native port 9001, and a default password literal `vitevue_dev` (a local dev default).
- Neither script is scheduled. Nothing refreshes facts after new data, and Redis caches are invalidated only by the Mac sync (`invalidate_prefixes`).
- **Readiness:** Reliability FAIL · Observability FAIL · Security PARTIAL · Data Quality FAIL (serving reads undeduplicated bronze, see F03) · Recoverability FAIL · Testing FAIL (backend tests fabricate these tables instead of creating them from the scripts) · Maintainability FAIL (serving contract split between backend scripts and Holocron schema) · Scalability PARTIAL (views scan bronze per request).

---

## P21 — Local market-data mirror (developer Mac)

- launchd 09:00 runs `sync_market_data`, which runs in order: `sync_dld` (SSH into the server's Dagster container, fingerprints each DLD bronze table, streams a gzip Native export, validates, EXCHANGEs locally while keeping `<table>_local_sync_previous`), then `sync_silver`, then DXBI (P05), then `sync_rental_repairs`, then CRM `refresh_rental_imports`, then Redis cache invalidation. It writes `automation-status.json`.
- Well built for its purpose: validated exchange, retained previous table, locks.
- It is **developer replication, not production** (reference §10). The risk is that it currently acts as the *only* publisher for P05/P06 data (F01).
- Hard-coded `/Users/eier/...` paths in `deploy/launchd/com.vitevue.dxbi-sync.plist`.
- **Readiness (as a production mechanism):** FAIL. As a dev tool: acceptable.

---

## P22 — CRM rental import refresh and owner re-match

- `backend/scripts/crm/refresh_rental_imports.py`: snapshots rental tables (`FINAL`), refreshes claim links and import counts in one PG transaction, and skips if unchanged. `rematch_owner_claims.py` is manual per organisation/import.
- Correctly scoped by `--organization-id` for re-match. Refresh runs across all organisations after a warehouse change.
- Triggered only by P21 on the Mac. Production trigger is UNKNOWN.
- **Tests:** `backend/tests/unit/test_crm_rental_refresh.py`.
- **Readiness:** Reliability PARTIAL · Observability PARTIAL · Security PARTIAL · Data Quality PASS (abort on warehouse change) · Recoverability PARTIAL · Testing PARTIAL · Maintainability PARTIAL · Scalability PARTIAL.

---

## P23 — Tables with no producer in the repository

| Table | Read by | Created by |
|-------|---------|------------|
| `silver_reelly_projects` | `backend/app/project/queries.py:87`, `radar_queries.py:68`, `holocron/holocron/assets/geography.py:652`, view in `clickhouse_schema.sql:1534` | **Nothing in repo** |
| `reelly_project_unit_types_bronze` | `backend/app/supply/queries.py:62` | **Nothing in repo** |
| Extra columns on `reelly_projects_bronze` (`region`, `area_name`, `latitude`, `longitude`, `project_name`, …) | backend supply/projects | **Nothing in repo** |

- **Risk:** the production warehouse cannot be rebuilt from source control, and `ensure_schema` on a fresh warehouse may fail at the view referencing `silver_reelly_projects`. Whether ClickHouse rejects it depends on the version and settings: UNKNOWN, verify on a fresh 24.8 instance.
- **Action:** Phase 1 task 1.2 (discover the producer with `SHOW CREATE TABLE` and `system.query_log`), then codify it as a Holocron asset.

---

## P24 — User uploads (CRM owner/contact and inbound imports)

- Request-coupled parsing of files ≤ 50 MB (`backend/app/crm/imports.py:43`, `inbound_imports.py:43`). Header mapping and enquiry extraction use direct OpenRouter HTTP calls with output caps and redaction (`backend/app/crm/ai.py`).
- Organisation-scoped PostgreSQL writes. Matching reads DXBI evidence (P05).
- The reference doc covers the durable-job gap (§10 "Minimal durable jobs", §14 "Heavy imports/reports", P0). It is **not re-audited here**. This inventory records it because it is a data source whose correctness depends on P05.
- **Readiness:** see reference §14. Classified outside the market-data pipeline scope.
