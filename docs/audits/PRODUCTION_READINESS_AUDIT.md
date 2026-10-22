# Data Pipeline Production-Readiness Audit

**Date:** 2026-09-29 · **Baseline:** branch `docs/production-first-readiness`, commit `71cf611` ·
**Scope:** every mechanism by which data enters, is transformed in, is stored in, and is served
from this repository. Identity, tenancy, AI policy, and cloud cost are covered by
[production-readiness-2026-09-29.md](../../production-readiness-2026-09-29.md) and are referenced,
not repeated.

**Method and limits:**
- Performed: static reading of all ingestion code, orchestration, deploy units, scripts, backend consumers and schema.
- Run: Holocron unit tests (469 passed, 0 skipped).
- Not performed: live database, object store, Dagster instance or provider access; backend tests; load tests.
- Every statement about live state is labelled **UNKNOWN**. Findings marked "confirmed" were verified by reading the exact code path. "Likely" means the code strongly implies it but it needs one runtime check.

**Status since the baseline (2026-10-03):** findings below are kept as audited. Where code has since
changed, a **Status** line records it. "Code ready" means committed and unit tested in an open PR. It
does not mean staging accepted or production enabled.

Document set:

| Document | Purpose |
|----------|---------|
| [CURRENT_STATE.md](../architecture/CURRENT_STATE.md) | What exists |
| [PIPELINE_INVENTORY.md](../data-pipelines/PIPELINE_INVENTORY.md) | Canonical list, P01–P24 |
| [PIPELINE_AUDITS.md](PIPELINE_AUDITS.md) | Per-pipeline detail and scores |
| [PIPELINE_STANDARDS.md](../data-pipelines/PIPELINE_STANDARDS.md) | The bar |
| [TARGET_ARCHITECTURE.md](../architecture/TARGET_ARCHITECTURE.md) | Proposed state |
| [MIGRATION_PLAN.md](../migration/MIGRATION_PLAN.md) | How to get there, plus the AI handoff |

---

## 1. Executive summary

**What works today.** Holocron has a sound skeleton:

- Source packages are isolated.
- Raw releases are immutable, with checksummed manifests written only after a complete extract.
- Checkpointed, resumable chunk jobs exist for DLD backfills, Property Finder and Reelly.
- Atomic `EXCHANGE TABLES` publication is used for DDA, silver, geography and the DXBI evidence loaders.
- Dataset versions and a replay tool exist.
- Media downloads have host allowlists and size limits.
- Config is redacted in manifests.
- 469 fast unit tests pass.
- The DXBI evidence loaders and the local mirror contain the most careful validation in the repo (reconciliation, verified-run gates, fingerprinted exchanges).

The foundation is worth keeping. **This is not a rewrite.**

**What is risky.**

1. **Production data depends on a laptop and root cron jobs.** CRM evidence (DXBI rentals/sales) is scraped by root systemd timers and published only through a developer Mac's 09:00 launchd job. The repository contains no path that loads it into production (F01).
2. **The warehouse cannot be rebuilt from the repository.** The backend queries tables and columns that nothing in the repo creates (`silver_reelly_projects`, `reelly_project_unit_types_bronze`, extra `reelly_projects_bronze` columns) (F02).
3. **Seven DLD datasets will duplicate rows daily** once their schedules run, inflating counts shown to users (F03).
4. **Access-control circumvention** (CAPTCHA solving, generated client signatures, proxy experiments) sits in the supply chain without a recorded licence (F04).
5. **The Dagster UI is published on the host's public interface with no authentication** (F05).
6. **Nobody is told when anything fails:**
   - there are no asset checks, retry policies, run-failure alerts or freshness monitoring;
   - every schedule defaults to STOPPED;
   - the news feed is not actually scheduled to publish (F07, F09, F12, F23).

**What needs architectural improvement.**

- One publication primitive (staging → validate → exchange) instead of today's two, one of which is non-atomic.
- One orchestrator per dataset instead of Dagster + systemd + launchd + backend scripts.
- An explicit, tested warehouse serving contract owned by Holocron instead of backend scripts and server-side hand edits.
- Checks and alerts as standard equipment rather than per-source effort.

**Bottom line.** No data pipeline is production-ready against the standard. The DLD open-data
pipeline is closest and is the recommended pilot. The P0 items are few, concrete, and mostly small
code or configuration changes, except F01 (moving DXBI into Dagster), which is gated on a business
decision about whether that collection is permitted.

---

## 2. Repository architecture (summary)

```text
External sources ──► Holocron/Dagster (18 units) ──► R2 raw + manifests ──► ClickHouse bronze ──► silver/geo
       │                                                                              │
       └──► ph-box systemd (DXBI reports) ──► host disk ──rsync──► developer Mac ──► Mac ClickHouse
                                                                              │
backend/scripts (views, facts, demo imports) ──► ClickHouse ch_* ◄────────────┘
ClickHouse + PostgreSQL ──► FastAPI (Redis cache, LLM tools) ──► SvelteKit SPA
```
Full detail: [CURRENT_STATE.md](../architecture/CURRENT_STATE.md).

## 3. Pipeline summary

| ID | Pipeline | Status | Worst dimension |
|----|----------|--------|-----------------|
| P01 | DLD Open Data (9 units) | scheduled (STOPPED by default) | Data Quality FAIL (F03) |
| P02 | Dubai Pulse archive | one-time | — (acceptable) |
| P03 | DLD Mashrooi | manual/demo | Reliability FAIL |
| P04 | DXBI map | scheduled, likely failing | Security/Data Quality FAIL (F04, F11) |
| P05 | DXBI rental/sales evidence | systemd + laptop | Reliability/Recoverability FAIL (F01) |
| P06 | DXBI repair queue | systemd, code outside repo | Maintainability FAIL (F14) |
| P07 | DXBI legacy JS backfills | legacy | FAIL (retire) |
| P08 | PF locations | manual | PARTIAL |
| P09 | PF listings | scheduled | Data Quality FAIL (F18) |
| P10 | PF media | manual | PARTIAL |
| P11 | Reelly supply | scheduled | Data Quality/Recoverability FAIL (F02) |
| P12 | Reelly documents/media | scheduled | PARTIAL |
| P13 | DDA planning | scheduled | Data Quality FAIL (F08) |
| P14 | News poll/publish | poll scheduled, publish manual | Reliability FAIL (F12) |
| P15 | News push/import | unknown caller | Maintainability FAIL |
| P16 | Chunked upload ingress | no consumer | FAIL (disable) |
| P17 | Bayut | experimental | not in scope for production |
| P18 | Silver | scheduled, unconsumed | Maintainability FAIL (F07) |
| P19 | Geography | manual / Mac | PARTIAL |
| P20 | Serving views/facts | manual scripts | FAIL (F13 fixed in code; still manual, 5A open) |
| P21 | Local mirror | developer Mac | FAIL as a production mechanism |
| P22 | CRM rental refresh | Mac / manual | PARTIAL |
| P23 | Unknown producers | unowned | FAIL (F02; DDL added, producer still missing) |
| P24 | CRM user uploads | request-coupled | see reference §14 |

---

## 4. Critical findings (P0 and P1)

Priority reasoning:

- **P0** means one of: customer-visible data can be wrong or lost; data cannot be recovered or rebuilt; there is a direct security exposure; or there is a legal or compliance blocker.
- **P1** means operations would be unreliable or blind but data stays correct, or the risk is bounded by manual workarounds that exist today.

### F01 — CRM evidence is published only through a developer laptop (P0)
- **Observed:**
  - P05/P06 run as root systemd timers on ph-box (`holocron/deploy/systemd/*`) and write run folders to host disk (`/root/data/dxbi-daily`).
  - The only loader path is `holocron/scripts/sync_dxbi_daily_runs.sh`, launched by a macOS LaunchAgent at 09:00 (`deploy/launchd/com.vitevue.dxbi-sync.plist`). It rsyncs from a hard-coded `root@<ip>` and loads into the ClickHouse named in the Mac's `.env.dev`.
  - Nothing in the repo loads these tables into production ClickHouse.
- **Risk:** CRM owner, ownership and opportunity features read `dxbi_rental_*` / `dxbi_sales_unit_events`. If production has these tables, their provenance is undocumented. If it does not, the features are empty. Laptop sleep, travel or loss stops publication. Raw runs exist only on one host's disk.
- **Recommendation:** MIGRATION_PLAN 1.2 (establish what production has), then 5C (move into Dagster with raw in object storage), gated on D1.
- **Priority reasoning:** P0 because customer-facing CRM correctness depends on it and it is unrecoverable if the host disk is lost.
- **Status (2026-10-03): partial, code ready (#5).** The Dagster asset `dxbi_evidence_bronze` (job `dxbi_evidence_load`, schedule STOPPED) loads completed runs from `DXBI_EVIDENCE_DIR`. It archives raw files to object storage with sha256 manifests and records each run in `dxbi_evidence_load_ledger`. The scraper still runs on ph-box. The production ingestion host (D4) and licence decision (D1) are open. The laptop path stays the only proven publication until cut-over.

### F02 — Warehouse serving contract is not reproducible from the repository (P0)
- **Observed (confirmed):**
  - `silver_reelly_projects` (read by `backend/app/project/queries.py:87`, `radar_queries.py:68`, `holocron/holocron/assets/geography.py:652`, schema view at `clickhouse_schema.sql:1534`) and `reelly_project_unit_types_bronze` (`backend/app/supply/queries.py:62`) are not created or populated anywhere in the repo.
  - The backend selects columns such as `project_name`, `sale_status` and `min_price_aed` from `reelly_projects_bronze`, whose Holocron DDL has 7 unrelated columns (`clickhouse_schema.sql:1119-1128`).
  - Backend integration tests hand-write their own table DDL (`backend/tests/integration/clickhouse/*.py`) rather than using Holocron's.
  - `holocron/holocron/platform/sync_dld.py` explicitly filters `ALIAS` columns, which implies live tables carry server-side ALTERs.
- **Risk:** a restore, a new environment (staging, AWS UAE per the reference) or a fresh developer machine produces a warehouse the backend cannot query. `ensure_schema` on a fresh warehouse may fail at the view referencing the missing table (UNKNOWN; depends on ClickHouse view validation). Backend tests pass against schemas that do not exist in production.
- **Recommendation:** MIGRATION_PLAN 1.2 (capture live DDL, find the producer), 2.1 (serving-contract test on a fresh warehouse), 5A (codify producers; move serving views into Holocron).
- **Priority reasoning:** P0, because recoverability and environment creation are launch prerequisites (reference §8, §15 step 9).
- **Status (2026-10-03): partial (#5).** `clickhouse_schema.sql` now defines `silver_reelly_projects` and `reelly_project_unit_types_bronze`, and a schema test checks that every view reads only objects defined earlier, so the file applies to an empty warehouse. Their producer is still missing, and an empty table is not data completeness. Backend integration fixtures still hand-write DDL. 5A remains open.

### F03 — Daily DLD incrementals duplicate rows for seven datasets (P0)
- **Observed (confirmed by code and test):**
  - For projects, valuations, lands, buildings, units, brokers and developers, the bronze delete predicate is exact equality on `(date_window_start, date_window_end)` (`holocron/holocron/sources/dld_open_data_bronze.py:235-274`; asserted in `holocron/tests/test_dld_open_data_bronze.py:141-143`).
  - The daily window slides (`today-3 … today`), so previous rows are never deleted, and re-fetched records get new `row_key`s (the hash includes `row_index`).
  - The tables are plain MergeTree, and the serving views `ch_projects`, `ch_developers`, `ch_buildings`, `ch_units` don't deduplicate.
- **Risk:** project, unit, building and developer counts and aggregates (radar, project fact, supply, area fact) inflate by up to about 4× for recent records. Whether this is already in production depends on whether the schedules were ever enabled: UNKNOWN.
- **Recommendation:** do not enable those schedules. Measure duplicates (1.2), then fix in the pilot (Phase 3: `key_replace` / `window_replace` via `publish_rebuild`, plus a one-time dedupe).
- **Priority reasoning:** P0, a data-integrity defect in customer-visible numbers.

### F04 — Access-control circumvention and unconfirmed licences in the supply chain (P0, business decision)
- **Observed:**
  - DXBI extraction solves hCaptcha through 2Captcha (`sources/dxbi/extract.py:1162-1180`) and generates the site's client request signature (`_heatmap_client_signature`).
  - P05 automates two provider accounts.
  - Bayut tooling includes proxy configuration (`holocron/scripts/configure_bayut_proxy.zsh`, DataImpulse examples).
  - PF, Reelly and DLD are accessed through website or partner endpoints with browser-like headers.
  - No licence or terms record exists in any source README. The reference doc §10 reaches the same conclusion.
- **Risk:** contractual or legal exposure, and sudden loss of access (account bans) with no fallback. No engineering control resolves this.
- **Recommendation:** decision D1 (MIGRATION_PLAN). Record the legal basis per source (standard S3.1). Enforce with `production_approved` (Phase 2.7).
- **Priority reasoning:** P0 because it gates which data the product may legally rely on.

### F05 — Dagster UI/GraphQL exposed without authentication (P0)
- **Observed:** `holocron/docker-compose.yml` publishes `${DAGSTER_PORT:-3000}:3000` on all interfaces. Dagster OSS has no auth. `holocron/README.md` instructs `http://<vps-ip>:3000`.
- **Risk:** anyone who can reach the port can:
  - launch any job (including scraping with the company's accounts);
  - read run configs, which may include provider passwords (Reelly accepts them in config, F16);
  - read logs;
  - wipe checkpoints through `reset_checkpoint` runs.

  Whether a host firewall blocks the port is UNKNOWN.
- **Recommendation:** MIGRATION_PLAN 1.1 (bind to 127.0.0.1; tunnel for access).
- **Priority reasoning:** P0, a direct security exposure fixed by a one-line change.

### F06 — Non-atomic publication and truncate-first replay (P1)
- **Observed:** most bronze loaders `ALTER TABLE … DELETE … mutations_sync=1` then `INSERT` (`platform/bronze.py:93-157`). `bronze_replay --initial-load` `TRUNCATE`s live tables, then continues past per-manifest failures and returns partial success (`platform/bronze_replay.py:131-167`).
- **Risk:** readers see empty or partial windows during loads. A crash between delete and insert leaves holes until someone notices. A failed replay leaves production tables partially loaded.
- **Recommendation:** `publish_rebuild` (Phase 2.2), loaders migrated in Phases 3 and 5, replay into staging (3.6).
- **Priority reasoning:** P1, because recovery by replay is possible and the windows are short, but it becomes a P0 incident if replay is used on production before 3.6.

### F07 — Scheduling is fragmented, time-offset based, and unowned (P0)
- **Observed:**
  - All 18 Dagster schedules default to STOPPED (`factory.py:896-903`).
  - DXBI runs under systemd, while a separate Dagster DXBI job exists.
  - `silver_refresh` duplicates `silver_transactions_refresh` at 06:00.
  - Derived jobs rely on cron offsets rather than dependencies.
  - Which schedules are RUNNING in production is UNKNOWN.
- **Risk:** freshness has no owner. Stale or partially derived data is published silently. Concurrent silver runs collide on one staging table name.
- **Recommendation:** Phase 1.2 (inventory of what runs), Phase 2.6–2.7 (queue, approval gate, environment-aware defaults), 5A/5H (dependency-driven derived assets), 5C (single orchestrator for DXBI).
- **Priority reasoning:** P0 (reference §14 rates ingestion scheduling P0), because the freshness guarantees promised to users (reference §2) have no mechanism.

### F08 — No data-quality gates; partial runs published as complete (P1)
- **Observed (confirmed):**
  - Zero Dagster asset checks, although holocron AGENTS.md requires them. `SourceSpec.checks` is unused.
  - Coercion defaults bad values to 0, "" or 1970-01-01 (`platform/bronze.py:183-257`).
  - DDA plot failures become stub rows that replace good data (`sources/dda/extract.py:188-209`).
  - Full-replace tables (DDA, PF locations, DXBI map) have no row floor, and smoke caps (`max_plots`, `max_heatmap_buildings`) are not blocked from bronze.
- **Risk:** silent degradation of plot, planning and map data; provider schema changes become zeros.
- **Recommendation:** Phase 2.4 check factory plus publisher floors; source-specific fixes in 5F/5G.
- **Priority reasoning:** P1. Data can be wrong, but only after a provider failure; recoverable from raw.
- **Status (2026-10-03): open.** The canonical market layer has 22 Dagster asset checks (#5), but they run after `EXCHANGE TABLES`. They observe data that is already published and do not gate publication (S4.1). No other table has checks. Separately, #4 stops two provider changes from failing silently. Property Finder now raises instead of parsing every page as empty. DDA requests only fields the layer still publishes and logs the dropped optional fields. Neither fix is a quality gate.

### F09 — No retries policy, run queue, or concurrency control (P1)
- **Observed:** no `RetryPolicy`. `DefaultRunCoordinator` (`holocron/dagster.yaml`). Reelly and PF locations have no request retries. DLD does not retry 429 and uses linear backoff without jitter.
- **Risk:** transient provider errors fail daily runs, and overlapping runs race on checkpoints and tables.
- **Recommendation:** Phase 2.5–2.6.
- **Priority reasoning:** P1, reliability only.

### F10 — Raw→bronze hand-off lives on container-local disk (P1, likely)
- **Observed:** no IO manager is configured. The bronze asset receives the manifest through Dagster's default filesystem IO manager at `/app/storage` inside the container. The webserver and daemon are separate containers without a shared volume (`holocron/docker-compose.yml`, `Dockerfile`).
- **Risk:** re-executing bronze from the UI for a schedule-launched run, or after `just deploy` rebuilds the image, cannot find the pickled manifest.
- **Recommendation:** Phase 2.6 (bronze reads the manifest by key from object storage).
- **Priority reasoning:** P1. Workaround exists (`bronze_replay`).

### F11 — Scheduled DXBI map job likely cannot run in the image (P1, likely)
- **Observed:** `DxbiConfig.headless=False` by default. Without `DISPLAY`, the extractor requires Xvfb (`sources/dxbi/extract.py:682-690`). The image does not install Xvfb, and the schedule config sets only `mode`.
- **Risk:** the job fails every day if enabled. Because there are no alerts (F23), nobody notices.
- **Recommendation:** MIGRATION_PLAN 5G (gated on D1).
- **Priority reasoning:** P1.

### F12 — News: not scheduled to publish; dual schema ownership; transaction bug (P1)
- **Observed (confirmed):**
  - The 15-minute job selects only poll and thumbnails (`factory.py:799-803`). Publication is the manual `news_publish_backfill`. This contradicts reference §10.
  - Holocron runs DDL on the app database (`news_feeds/assets.py:479-527`) that diverges from Alembic (`8395bd1f2518_baseline_schema.py:40-50`).
  - Per-row `try/except` inside one transaction without savepoints: a single failure aborts the rest, and the commit rolls back rows already counted as published (likely; verify with a test).
  - No unique constraint, and three separate writers exist (P14, P15).
- **Risk:** stale news on the frontend; Alembic autogenerate conflicts; silent loss of a publish batch; duplicates.
- **Recommendation:** MIGRATION_PLAN 5E.
- **Priority reasoning:** P1. Customer-visible staleness, but low business impact per incident.

### F13 — Serving fact builder destroys its own source views on second run (P1)
- **Observed (confirmed):** `backend/scripts/clickhouse/build_fact_tables.py:48-80` drops `*__source_view` (the original view definitions renamed away by the previous run), materialises from the already-frozen tables, and renames. After the first run, `create_compatibility_views.py` fails because `ch_project_fact` is a table. Default password literal `vitevue_dev`.
- **Risk:** permanently frozen radar/area facts, with no automated refresh.
- **Recommendation:** MIGRATION_PLAN 1.4 (guard), then 5A (Dagster fact assets).
- **Priority reasoning:** P1, since it is a manual script; P0 if it is part of any deployment routine (UNKNOWN).
- **Status (2026-10-03): fixed in code (#5), by repeatable rebuild instead of the 1.4 guard.** `_materialize_fact` rebuilds from the source view wherever the last run left it, swaps the fresh table in, and fails loudly if the view is missing. Three unit tests cover first build, rebuild and missing view. Still open: a changed-input rebuild against a real ClickHouse (Prompt 05); `create_compatibility_views.py` still cannot run while the facts are tables; the `vitevue_dev` password default; the move to Dagster fact assets (5A).

### F14 — Running code and host-specific configuration outside version control (P1)
- **Observed:** `dxbi-rentals-repair.service` executes `/root/data/dxbi-rental-repair-20260915/rental_repair_queue.py` with `PYTHONPATH=/root/data/dxbi-rental-v2-experiments-20260905/code`. Units hard-code `/root/...`, the launchd plist and supervisors hard-code `/Users/eier/...`, and the sync script hard-codes a public IP.
- **Risk:** behaviour cannot be reviewed, reproduced or rolled back. Host loss loses the code.
- **Recommendation:** MIGRATION_PLAN 1.2 (diff), 5C (port into the repo), 6 (remove paths).
- **Priority reasoning:** P1.

### F15 — No CI for ingestion; tests are fake-only (P1)
- **Observed:** no workflow runs Holocron checks (`.github/workflows/`). The 469 tests use fakes. There are no ClickHouse integration tests, so F03 passed its tests because the test encodes the defective predicate.
- **Recommendation:** MIGRATION_PLAN 1.3 and 2.1.
- **Priority reasoning:** P1.

### F16 — Secrets handling gaps in ingestion (P1)
- **Observed:**
  - Reelly credentials are accepted in run config (`sources/reelly_supply/source.py`, `extract.py:1547`), which Dagster stores unredacted.
  - DXBI credentials and live browser sessions are plain JSON files under `/root/.config/vitevue/dxbi/`, used by root services.
  - `CLICKHOUSE_SECURE=false` is set in the VPS compose file, so credentials cross the network in plaintext if ClickHouse is remote (host placement UNKNOWN).
  - The positive side: `.env*` is excluded from images, and manifests redact.
- **Recommendation:** Standards S3.4 and S7.2; MIGRATION_PLAN 5B, 5C, 7.2.
- **Priority reasoning:** P1.

### F18 — Withdrawn listings stay "available" indefinitely (P1)
- **Observed:** PF bronze is keyed-replaced and never marks unseen listings. The API's freshness is `max(scraped_at)` (`backend/app/listings/queries.py:66-70`).
- **Risk:** users see listings that no longer exist, while the freshness indicator suggests the data is current.
- **Recommendation:** MIGRATION_PLAN 5D.
- **Priority reasoning:** P1, a customer-visible correctness issue with a manual workaround (`replace_pf_bronze_snapshot.py`).

### F23 — No alerting or freshness visibility (P1)
- **Observed:** no failure sensors, alert channel, freshness checks or dashboards. The DXBI health check only writes to stderr on a laptop.
- **Recommendation:** MIGRATION_PLAN 2.4, 2.6, 7.5. Decision D3.
- **Priority reasoning:** P1. It turns every other failure into a silent one.

### F24 — DLD transactions incremental gap after a >3-day outage (P1)
- **Observed:** transactions incremental uses a fixed 3-day lookback and no checkpoint (`dld_open_data_orchestration.py:20-24`, `dld_open_data_shared.py:490-492`). Rents is checkpointed.
- **Recommendation:** MIGRATION_PLAN 3.3.
- **Priority reasoning:** P1. Recoverable by manual backfill, but only if someone notices.

### Lower-priority findings (P2/P3)
| ID | Finding | Priority | Plan |
|----|---------|----------|------|
| F17 | SSRF guard checks literal hosts only (no post-DNS check); news thumbnail allowlist empty by default | P2 | 7.4, 5E |
| F19 | `ensure_schema` executes the whole DDL file on every load; runtime writers need DDL privileges | P2 | 7.1 |
| F20 | `ALTER … DELETE` mutations on large unpartitioned tables (cost, merge pressure) | P2 | removed by `publish_rebuild` (3, 5) |
| F21 | Dead or unclear ingress and demo code: `/upload/chunked` (no assembler), demo import scripts, Bayut handoff copies + zip, missing `load_local_clickhouse.py`, orphan entity YAML | P2/P3 | 6 |
| F22 | Orchestration factory branches on source names (control plane knows source internals) | P2 | 6 (hooks from 2.4) |
| F25 | `request_pause_seconds=0` defaults; DDA 20-thread concurrency against a government site | P2 | 3.4, 5B, 5F |

---

## 5. Production readiness matrix

| Area | Current state | Risk | Required change |
|------|---------------|------|-----------------|
| Reliability | Timeouts mostly present; DLD retries linear; Reelly/PF-locations none; no Dagster retries; checkpoints for chunked jobs; overlapping runs possible | Transient failures fail daily runs; races | `http_retry`, `RetryPolicy`, `QueuedRunCoordinator` + per-table concurrency (Phase 2) |
| Rate limiting | Pauses default 0; DDA 20 threads; DXBI 2–4 s; no shared limiter | Provider blocking, quota exhaustion, ToS breach | Non-zero defaults per source, concurrency caps (S3.2), decisions D1/D2 |
| Authentication (sources) | Public identifiers (DLD), login (Reelly), accounts + CAPTCHA (DXBI), sessions (PF) | Legal exposure; credential leakage via run config/files | Licence register (D1), secrets from manager only (S3.4) |
| Publication integrity | Mix of delete+insert (non-atomic) and EXCHANGE; seven DLD tables duplicate | Wrong customer numbers; holes after crashes | `publish_rebuild` everywhere (Phases 2, 3, 5) |
| Logging | Good progress logs; redacted manifests | Logs exist only in Dagster/stdout/laptop files | Structured logs shipped centrally (7.5; reference §11) |
| Monitoring and alerting | None | Failures and staleness are silent | Checks, failure sensors, freshness SLAs, alert channel (2.4, 2.6) |
| Data quality | Raw checksums and row counts verified; no row/table validation; silent coercion; partial runs published | Silent degradation | Row models, rejects, blocking floors, key checks (S5) |
| Recoverability | Immutable raw + replay + dataset versions (good); replay truncates live; serving layer not reproducible; P05 raw on host disk | Cannot rebuild environments; unsafe recovery | Staging replay (3.6), serving contract (5A), P05 raw to object storage (5C), drills (7.3) |
| Testing | 469 fast unit tests; no integration; no CI for Holocron; backend fixtures diverge from real DDL | Defects like F03 pass tests | CI (1.3), ClickHouse integration harness + contract test (2.1) |
| Security | Dagster UI public; DDL-privileged runtime users; plaintext CH default; root services; allowlisted media downloads | Unauthorized job launch; lateral damage | Private UI (1.1), least privilege (7.1), TLS (7.2), post-DNS SSRF check (7.4) |
| Configuration | Typed `Settings`; per-source Pydantic configs; host paths and IPs hard-coded in units/scripts | Unreproducible hosts | Env-driven paths (6), environment-aware schedules (2.7) |
| Deployment | `just deploy` git-pull + rebuild on the VPS; no Holocron CI/CD; systemd/launchd installed by hand | Drift between hosts and repo | CI (1.3), single orchestrator (5C/6), IaC per reference §15 |
| Maintainability | Clean source/platform split; 2–3k-line extractors; duplicated DDL; two silver implementations; factory branches | Slow, risky changes | Hooks (6), single DDL source (5A/5C), one silver path (5A) |

---

## 6. Architectural findings

Each uses: current design → why problematic → impact → target → migration.

### A1. Two orchestration stacks
- **Current design:** Dagster for most sources; systemd plus launchd plus shell for DXBI evidence, repairs, mirroring and CRM refresh.
- **Why problematic:** there is no single view of what ran, failed or is stale, and retries, locks and alerts are reimplemented per stack.
- **Impact:** F01, F07, F14, F23.
- **Target:** Dagster owns every scheduled publication (S1.1).
- **Migration:** 5C, then 6.

### A2. Two publication patterns, one unsafe
- **Current design:** delete+insert mutations (`replace_where_rows`, `replace_keyed_rows`) alongside staging+EXCHANGE (`replace_table_rows`, silver, geography, DXBI loaders).
- **Why problematic:** delete+insert is non-atomic and its window-equality predicate caused F03.
- **Impact:** F03, F06, F20.
- **Target:** one `publish_rebuild` primitive with keep rules. Per AGENTS Rule 5 the more-used and safer EXCHANGE pattern wins, and the other is flagged for removal.
- **Migration:** Phase 2.2, then 3, then 5, then 6.

### A3. Serving contract split across three owners
- **Current design:** Holocron DDL; backend scripts creating `ch_*` views and facts; server-side hand edits; backend test fixtures with their own DDL.
- **Why problematic:** nothing verifies that the tables the backend reads exist.
- **Impact:** F02, F13.
- **Target:** Holocron owns all warehouse objects, with the serving contract verified on a fresh warehouse in CI.
- **Migration:** 2.1, then 5A, then 6.

### A4. Control plane knows source internals
- **Current design:** `factory._run_raw_extractor` special-cases `reelly_supply`, `pf_listings`, `dld_od_*` and `dld_pulse_historic`.
- **Why problematic:** it contradicts `holocron/ARCHITECTURE.md` ("Dagster must not contain source-specific … logic"), and every new stateful source edits the factory.
- **Impact:** F22.
- **Target:** `SourceSpec` hooks.
- **Migration:** 2.4 adds the fields; 6 moves the branches.

### A5. Quality is per-source effort, not platform equipment
- **Current design:** the best validation (DXBI reconciliation, sync fingerprints) lives in Mac-only scripts; Dagster assets have none.
- **Why problematic:** quality depends on who wrote the source.
- **Impact:** F08, F23.
- **Target:** a generated check factory plus blocking floors in the publisher.
- **Migration:** 2.4, then 3 and 5.

### A6. Application database written by the ingestion runtime without shared schema ownership
- **Current design:** Holocron runs DDL on `news_articles`, alongside two backend writers.
- **Why problematic:** there are two schema owners and three writers.
- **Impact:** F12.
- **Target:** Alembic owns the schema, Holocron is the only writer, and it uses `ON CONFLICT` upserts.
- **Migration:** 5E, then 6.

### A7. Large source modules mixing fetch, pagination, state and parsing
- **Current design:** `pf_listings/extract.py` (2,765 lines), `reelly_supply/extract.py` (1,738), `dxbi/extract.py` (1,281), `scripts/dxbi_rental_backfill.py` (1,670).
- **Why problematic:** hard to test the failure paths, and hard to review.
- **Impact:** maintainability (P2).
- **Target:** split only where a migration touches them (e.g. 5C moves the DXBI report logic into a package). **Do not refactor for its own sake** (AGENTS Rule 3).

---

## 7. Recommended target architecture (summary)

Keep Holocron and Dagster. Add a thin shared layer:

- `publish_rebuild` (staging → validate → EXCHANGE, with window, key or full keep rules);
- a generated asset-check factory;
- `http_retry`;
- a publication ledger on every publish;
- a queued run coordinator with per-table concurrency and failure alerts.

Also:

- Make Holocron the single owner of all warehouse objects, verified by a serving-contract test on a fresh ClickHouse.
- Move every scheduled publication into Dagster.
- Make Alembic the owner of application tables that pipelines write to.
- Add no new infrastructure.

Full design: [TARGET_ARCHITECTURE.md](../architecture/TARGET_ARCHITECTURE.md).

## 8. Migration strategy (summary)

| Phase | Name | Content |
|-------|------|---------|
| 0 | Audit | Done |
| 1 | Guardrails and baseline | Private Dagster UI, Holocron CI, fact-builder guard, read-only live baseline, decision register. Small PRs, no data-behaviour change. |
| 2 | Shared infrastructure | Publisher, checks, retry helper, ledger, run queue/tags/retries/alerts, durable hand-off, approval gate, ClickHouse integration harness |
| 3 | Pilot on DLD (9 units) | Fixes F03/F24, safe replay |
| 4 | Validation | 14 days in staging, ADRs |
| 5 | Remaining sources | Serving contract and unknown producers, then Reelly, DXBI evidence (gated), PF, news, DDA, DXBI map (gated), derived automation |
| 6 | Legacy removal | Retire replaced paths |
| 7 | Hardening | Migrator and least privilege, secrets/TLS, backups and drills, SSRF, runbooks |

Full detail, per-phase files, tests, rollback and acceptance: [MIGRATION_PLAN.md](../migration/MIGRATION_PLAN.md).

---

# What I Would Do Next

*Plain-language summary for an engineer who has not worked on production data pipelines before.*

### 1. What is wrong today
The code that collects market data (Holocron) is mostly well organised. How it runs and
publishes is not production-grade:

- Some important data (the rental and sales evidence behind CRM) is collected by scheduled scripts on a server and loaded into a database by a developer's laptop each morning.
- Some tables the app reads were created by hand on the server, so nobody can recreate the database from the code.
- Seven Dubai Land Department datasets would quietly accumulate duplicate rows every day once their schedules are switched on.
- When anything fails, nobody is notified.
- The Dagster control panel is reachable from the internet without a password.

### 2. Why it matters
Customers will see numbers (project counts, prices, supply, owner evidence) that are only as
good as these pipelines. Today a pipeline can:

- fail silently;
- publish half a dataset;
- duplicate data;
- stop because a laptop is closed.

None of these would be visible until a customer notices a wrong number. Some sources are
collected in ways that may not be permitted, which is a business risk no code change fixes.

### 3. What the target architecture should accomplish
Every dataset has one owner job in Dagster. That job:

1. Downloads data and keeps an untouched copy (this already happens).
2. Checks the new data (enough rows, no duplicate IDs, required fields present).
3. Swaps it in all at once, so users never see a half-updated table.
4. Writes down what it published.
5. Raises an alert if anything goes wrong or data gets too old.

Anyone should be able to rebuild the whole warehouse from the code and the saved raw files.

### 4. What to fix first
1. **Close the control panel to the internet** (one line in `holocron/docker-compose.yml`).
2. **Stop the fact-building script from destroying its own inputs** on a second run (a small guard).
3. **Run the Holocron tests automatically on every pull request** (one GitHub workflow).
4. **Look at the real production database (read-only)** and answer the open questions:
   - Are there duplicates already?
   - Who created the unknown tables?
   - Which schedules are actually on?
   - Is the CRM evidence even in production?
5. **Get the business decision** on which data sources we are allowed to collect automatically.

### 5. Why the migration order makes sense
- We first remove the dangerous exposures and measure reality (cheap, no risk).
- Then we build the few shared tools once: safe swap, checks, retries, alerts.
- We prove those tools on the most important and simplest source (DLD), which also fixes the duplicate bug.
- We watch it for two weeks.
- Only then do we copy the pattern to the other sources.
- We delete old scripts last, after their replacements have proven themselves. That way we never have a period with no working pipeline.

### 6. What should NOT be changed yet
- Do not rewrite Holocron, switch orchestrators, or add Kafka, dbt, queues or a new warehouse.
- Do not refactor the big extractor files unless a migration step needs it.
- Do not switch the backend from bronze to silver tables until silver has been validated against known totals.
- Do not turn on the seven affected DLD schedules, run `bronze_replay --initial-load` on production, or run the fact builder twice, until the relevant fixes land.
- Do not delete the systemd or laptop scripts until the Dagster replacement has run in parallel and matched their output.

### 7. What risks remain after the plan
- **Source licences and access:** if a source is not permitted or gets blocked, its features lose data. The plan makes this visible but cannot prevent it.
- **Single ingestion host:** by design (reference §10), host failure pauses freshness, but users keep seeing the last good data.
- **Unknowns until Phase 1.2:** the real extent of duplicates and hand-made tables could enlarge Phase 3 and 5A.
- **Scale ceiling of the swap approach:** copying a table per publish is fine at today's likely sizes. If a table grows much larger, it needs partition-level swaps (flagged in the standard).
- **Platform-level P0s** (identity, tenancy, AI policy, backups) are tracked in the reference doc and remain launch blockers independent of this plan.

---

The **AI Handoff** block for future agents is at the end of [MIGRATION_PLAN.md](../migration/MIGRATION_PLAN.md#ai-handoff).
