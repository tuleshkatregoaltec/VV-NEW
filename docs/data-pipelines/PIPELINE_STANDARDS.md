# Data Pipeline Engineering Standard

**Status:** adopted baseline, proposed 2026-09-29. Every new pipeline, and every pipeline being
migrated, is evaluated against this document. Existing pipelines are scored in
[PIPELINE_AUDITS.md](../audits/PIPELINE_AUDITS.md).

This standard is deliberately sized for **Phase 1: one enterprise, about 150 users, one ingestion
host, Dagster + ClickHouse + PostgreSQL + S3-compatible object storage** (see
[production-readiness-2026-09-29.md](../../production-readiness-2026-09-29.md) §1, §10). It does not
require Kafka, a second warehouse, microservices, or a new workflow engine. It builds on patterns
the repository already has (immutable raw releases, manifests, checkpoints, `EXCHANGE TABLES`).
`holocron/docs/ingestion-patterns.md`, `bronze-patterns.md`, `logging.md` and
`scraping-investigation.md` remain the detailed how-to guides. Where they conflict with this
document, this document wins, and the guide should be updated.

Each standard gives:

- **Requirement**: what must be true.
- **Why**: the failure it prevents.
- **Applies to**: scope.
- **Pattern**: how to implement it in this repo.
- **Test**: the minimum check that proves it.

Levels: **MUST** (blocks production use), **SHOULD** (required before a pipeline is called
"migrated"; exceptions are documented in the source README), **MAY** (recommended).

---

## 1. Ownership and orchestration

### S1.1 One orchestrator per dataset (MUST)
- **Requirement:** each published table has exactly one scheduled producer, defined in Dagster (`holocron/holocron/orchestration/`). systemd, launchd, cron and laptop jobs must not publish production data.
- **Why:** P05/P06/P21 publish CRM evidence from a laptop and a root systemd timer (F01, F07). Nobody can see, retry or alert on them.
- **Applies to:** all scheduled ingestion and derived refreshes.
- **Pattern:** a Dagster job targeting the source's explicit asset set (holocron AGENTS.md "Dagster Modeling"). A long browser scrape runs as a Dagster run on the ingestion host, not as a detached script.
- **Test:** `holocron/tests/test_definitions.py` asserts every table in the ownership map (S1.2) has one producing asset.

### S1.2 Declared table ownership (MUST)
- **Requirement:** every ClickHouse table and view that the backend reads is declared in Holocron with its producer asset and schema. No table may exist only on a server.
- **Why:** `silver_reelly_projects`, `reelly_project_unit_types_bronze` and extra `reelly_projects_bronze` columns have no producer in the repo (F02). The warehouse cannot be rebuilt.
- **Pattern:** DDL in `holocron/holocron/lib/sql/clickhouse_schema.sql` (the canonical ClickHouse contract per holocron AGENTS.md). Serving views move from `backend/scripts/clickhouse/` into Holocron (Phase 5F). A `SERVING_TABLES` list is used by the contract test.
- **Test:** a warehouse contract test creates a fresh ClickHouse from the schema file and asserts every table and column the backend queries exists (MIGRATION_PLAN Phase 1.5).

### S1.3 Dependencies, not clock offsets (MUST)
- **Requirement:** derived assets (silver, geography, facts, cache invalidation) run *because* their inputs published successfully, not at a guessed later time.
- **Why:** `silver_transactions_refresh` at 06:00 assumes bronze at 04:00 finished. A delay publishes stale or partial derivatives (F07).
- **Pattern:** Dagster asset dependencies plus a declarative automation condition (eager-on-upstream-update), or a job that selects upstream and downstream assets together.
- **Test:** a definitions test asserts derived assets have no independent cron schedule.

### S1.4 Source-specific logic stays in the source package (SHOULD)
- **Requirement:** `orchestration/factory.py` must not branch on source names.
- **Why:** `_run_raw_extractor` hard-codes `if source.name == "reelly_supply" / "pf_listings" / startswith("dld_od_")` (F22). This is the "control plane knows source internals" coupling that `holocron/ARCHITECTURE.md` forbids.
- **Pattern:** optional hooks on `SourceSpec` (e.g. `resolve_config`, `after_manifest`, `needs_state_store`) with the defaults applied by the factory.
- **Test:** a new fake source with hooks works without factory edits.

---

## 2. Reliability

### S2.1 Timeouts on every network call (MUST)
- **Requirement:** every HTTP, browser navigation, database and object-store call has an explicit timeout. Browser runs also have a run-level deadline.
- **Why:** a hung call blocks the single ingestion host and all later runs.
- **Pattern:** `httpx.Client(timeout=…)`; Playwright `timeout=`; ClickHouse `connect_timeout`/`send_receive_timeout` (present); Dagster run-level `max_runtime` via run tags (`dagster/max_runtime`).
- **Test:** unit test with a stub transport that never responds asserts a timeout error.

### S2.2 Retries with exponential backoff and jitter; honour 429 (MUST for external APIs)
- **Requirement:** transient failures (timeouts, connection errors, 5xx, **429**) are retried a bounded number of times with exponential backoff plus jitter. `Retry-After` is honoured. 4xx other than 408/429 are not retried.
- **Why:** DLD retries linearly and fails on 429. Reelly and PF locations have no retry at all.
- **Pattern:** one small shared helper, `holocron/holocron/platform/http_retry.py` (a `retry_call(fn, *, attempts, base_seconds, max_seconds, retry_on)` plus a `retryable_http_error(exc)` classifier), used by source clients. Do **not** add a generic HTTP framework. Dagster `RetryPolicy(max_retries=2, delay=60, backoff=EXPONENTIAL, jitter=PLUS_MINUS)` on raw assets covers whole-run transient failures.
- **Test:** table-driven unit test of the classifier; a client test asserting the sequence of sleeps and that 400 is not retried.

### S2.3 Idempotent publication (MUST)
- **Requirement:** re-running any job for the same inputs produces the same published state. No duplicates and no losses.
- **Why:** seven DLD endpoints duplicate on overlapping windows (F03).
- **Pattern:** one of three publication modes (S4.1). The bronze row key must be derived from **business identity**, never from `row_index` or run position.
- **Test:** run the loader twice over overlapping windows against a real ClickHouse (S8.2) and assert `count() == uniqExact(business_key)`.

### S2.4 Resumability for long or chunked work (MUST when a run can exceed about 30 min)
- **Requirement:** long extractions checkpoint progress to object storage after each durable unit (page window, shard, chunk) and resume from it.
- **Why:** a DXBI comprehensive run is one browser session. A crash loses hours.
- **Pattern:** existing `platform/checkpoints.py` + `state/source=<s>/operation=<op>.json`, written **after** the manifest (as the factory already does). Chunk jobs are raw-only; publication happens once a dataset version is complete (`platform/dataset_versions.py`).
- **Test:** simulate failure after chunk N; the next run starts at N+1 and the final dataset equals an uninterrupted run.

### S2.5 Gap-free incremental windows (MUST)
- **Requirement:** incremental jobs start from the last *successful publication* (checkpoint) minus an overlap, not from a fixed lookback relative to today.
- **Why:** DLD transactions uses a 3-day lookback. A 4-day outage is a silent permanent gap (F24).
- **Pattern:** the `checkpointed_incremental=True` path already used by `dld_od_rents`.
- **Test:** checkpoint at D-10, run at D; assert the request window starts at D-10-overlap.

### S2.6 No overlapping runs of the same dataset (MUST)
- **Requirement:** at most one run per dataset publishes at a time. Browser-heavy runs are capped host-wide.
- **Why:** `DefaultRunCoordinator` allows overlaps (X4): news every 15 min, silver double schedule, chunk jobs racing on checkpoints.
- **Pattern:** `QueuedRunCoordinator` in `holocron/dagster.yaml` with `tag_concurrency_limits` (e.g. `dataset=<name>: 1`, `resource=browser: 1`). Each job carries those tags.
- **Test:** definitions test asserts every job has a `dataset` tag. Config test asserts the coordinator and limits.

### S2.7 Durable hand-off between steps (MUST)
- **Requirement:** anything a later step or retry needs is in object storage or a database, not a container filesystem.
- **Why:** the raw→bronze hand-off uses the default filesystem IO manager inside the container (X5). Rebuilds or cross-container retries lose it.
- **Pattern:** bronze reads the manifest from object storage by key (the manifest key is already in raw asset metadata), or `DAGSTER_HOME/storage` is placed on a volume shared by webserver and daemon. Prefer the manifest-by-key approach; it makes bronze replayable anywhere.
- **Test:** materialise bronze in a fresh process with only the manifest key available.

---

## 3. External sources and scraping

### S3.1 Authorised access only (MUST)
- **Requirement:** each external source has a recorded legal basis (licence, contract, or published terms permitting the use and redistribution) in its README before it feeds production. Collection must not depend on defeating access controls: CAPTCHA solving, forged client signatures, proxy rotation to evade blocks, or multiple accounts to exceed limits.
- **Why:** DXBI uses 2Captcha and signature generation. Bayut experiments use proxy rotation (F04). This is a legal and commercial risk no engineering control fixes.
- **Pattern:** a `licence:` section in `holocron/holocron/sources/<source>/README.md`, plus `SourceSpec.production_approved: bool` (added in Phase 2) that the factory uses to refuse scheduling unapproved sources in production.
- **Test:** definitions test: every scheduled job's source is `production_approved`.

### S3.2 Respectful request behaviour (MUST)
- **Requirement:** per-source request rate and concurrency caps are configured, not defaulted to 0 or unbounded. User-Agent and contact follow the source's terms.
- **Why:** `request_pause_seconds=0` defaults (DLD, PF, Reelly, Mashrooi); DDA runs 20 threads against a government site.
- **Pattern:** source config fields with non-zero conservative defaults; one `threading.Semaphore` where concurrency exists.
- **Test:** config test asserting non-zero defaults for scheduled jobs.

### S3.3 Source change detection (MUST)
- **Requirement:** a change in response structure fails loudly or is counted, rather than silently becoming zeros.
- **Why:** bronze coercion turns missing fields into 0, "" or 1970-01-01 (X8).
- **Pattern:** a Pydantic model (or required-key set) per source row at bronze-build time. Count rows with missing required fields, and fail if the ratio exceeds a threshold (S5.2).
- **Test:** fixture with a renamed field fails the loader or its check.

### S3.4 Credentials and sessions are secrets (MUST)
- **Requirement:** provider passwords, API keys and browser storage-state files come from the secret store at runtime. They are never in run config, images, repo files, or world-readable host paths. Each provider credential is scoped to the ingestion host only.
- **Why:** Reelly accepts passwords in run config (persisted in Dagster Postgres). DXBI credentials are JSON files under `/root/.config`.
- **Pattern:** environment variables injected from the secret manager (Bitwarden today, AWS Secrets Manager per the reference). Storage-state files live on an encrypted mounted path readable by the run user only. Remove `password`/`email` fields from run-config schemas.
- **Test:** config models reject secret-looking keys in run config (a unit test on each source config).

---

## 4. Publication and storage

### S4.1 Atomic, validated publication (MUST for anything the backend reads)
- **Requirement:** consumers never observe a partially published dataset. New data becomes visible only after validation passes (S5). Every table publishes through **one primitive: rebuild a staging copy, validate, `EXCHANGE TABLES`**. It has a per-table *keep rule* for which live rows carry forward:
  1. **`full_replace`:** keep nothing (snapshots: DDA, PF locations, DXBI map, derived tables). This is exactly today's `ClickHouseClient.replace_table_rows` plus a validation hook.
  2. **`window_replace`:** keep live rows outside the refreshed business-date window and outside other `source_system` scopes (DLD transactions, rents, valuations).
  3. **`key_replace`:** keep live rows whose business key is not in the new batch (incremental inventories, listings, catalogue).
- **Why:** delete-then-insert mutations expose gaps and leave holes on crash (X7). Initial-load replay truncates first. Staging + EXCHANGE is already the repo's most-used and most-tested pattern (DDA, silver, geography, Pulse, `sync_dld`, DXBI loaders). Choosing it over delete+insert follows AGENTS Rule 5 and needs no table-engine change.
- **Ceiling:** each publish copies the table. That is fine at current scale; datasets are daily and tables are likely in the low millions of rows (measure in MIGRATION_PLAN 1.2). If a measured publish exceeds ~10 minutes, move that one table to monthly partitions + `REPLACE PARTITION`.
- **Pattern:** `holocron/holocron/platform/publish.py::publish_rebuild(...)` (see [TARGET_ARCHITECTURE.md §6.2](../architecture/TARGET_ARCHITECTURE.md)). Loaders call it instead of `replace_where_rows` / `replace_keyed_rows`.
- **Test:** real ClickHouse test: a concurrent reader during publish never sees `count()` below the pre-publish or post-publish count; a failure injected before exchange leaves the live table unchanged.

### S4.2 Schema is versioned, not applied at runtime (SHOULD, MUST by Phase 7)
- **Requirement:** ClickHouse DDL is applied by a migration step with a privileged role, once per release. Runtime writers have `INSERT`/`SELECT` plus `EXCHANGE`/staging rights on their own tables only.
- **Why:** `ensure_schema` runs about 100 DDL statements on every load, which forces DDL privileges on runtime writers (X6), and the backend's view script needs DDL too.
- **Pattern:** keep `clickhouse_schema.sql` as the base. Add numbered forward-only files under `holocron/holocron/lib/sql/migrations/` and a `schema_migrations` table. Apply with `holocron.platform.clickhouse_schema` in a one-off job.
- **Test:** fresh-warehouse contract test (S1.2) runs the migrator, then asserts the full serving contract.

### S4.3 Business keys and uniqueness declared (MUST)
- **Requirement:** each table documents its business key and uniqueness expectation. A check verifies it (S5.2).
- **Why:** undeclared keys let duplicates pass unnoticed (F03).
- **Pattern:** `BronzeTable(name=…, source_key=…)` already has a `source_key` field. Populate it and use it in generated checks.

### S4.4 Retention and lifecycle (SHOULD)
- **Requirement:** raw releases and media have an object-storage lifecycle policy. Staging and `_previous` tables are cleaned up. Append-only bronze tables (PF, Reelly `(run_id, key)` ordered) have an explicit retention or compaction policy.
- **Why:** unbounded growth; licensed-data retention obligations.

---

## 5. Data quality

### S5.1 Validation at the bronze boundary (MUST)
- **Requirement:** each loader validates required fields and types for every row and records `rows_in`, `rows_published`, `rows_rejected_by_reason` in the load result and the bronze load ledger.
- **Why:** silent coercion (X8); DDA stubs (F08).
- **Pattern:** extend `bronze_load_result` with rejection counts. Rejected rows go to a `<source>_rejects` JSONL in R2, not into bronze.

### S5.2 Checks as Dagster asset checks (MUST)
- **Requirement:** every published table has asset checks for:
  - **(a)** row count versus the previous publication (floor, e.g. ≥ 90 % for full-replace inventories);
  - **(b)** business-key uniqueness;
  - **(c)** required-column non-null or non-default ratio;
  - **(d)** freshness (max business date or `scraped_at` within an SLA);
  - **(e)** for full-replace tables, "no smoke or capped run" (the run config has no `max_*` caps).
  Blocking checks (a, b, e) run **before** publication for full-replace tables.
- **Why:** holocron AGENTS.md already mandates "Express data quality as asset checks". There are none today (X3).
- **Pattern:** generate checks from `SourceSpec`/`BronzeTable` metadata in `orchestration/checks.py` (one factory, not per-table hand-written functions).
- **Test:** each generated check has a failing-fixture unit test.

### S5.3 Freshness SLAs (MUST)
- **Requirement:** each consumer-facing dataset has a freshness SLA. Starting values:
  - DLD daily: 36 h.
  - News: 60 min.
  - Listings: 36 h.
  - Reelly: 36 h.
  - DDA: 8 days.
- **Why:** reference §2 targets. `max(scraped_at)` shown to users hides staleness (F18).
- **Pattern:** freshness asset checks (S5.2d), plus the API returning a per-dataset `refreshed_at` read from the load ledger, not from `max(scraped_at)`.

### S5.4 Completeness for snapshot sources (SHOULD)
- **Requirement:** full-inventory sources detect withdrawn items (listings, projects) through `is_active`/`last_seen_at` semantics.
- **Why:** delisted PF listings remain available forever (F18).

---

## 6. Observability

### S6.1 Structured, correlated logs (MUST)
Every log line carries `source`, `run_id` (Dagster run id), `stage`, and counts per `holocron/docs/logging.md`. No secrets, cookies, or full payloads.

### S6.2 Run outcome recorded durably (MUST)
Each publication writes one row to `bronze_load_ledger` (it exists, but only replay writes it
today) with source, manifest key, table, row counts, status, error, and duration. This is the
single source for freshness and dashboards.

### S6.3 Alerting (MUST)
A Dagster run-failure sensor and asset-check failure sensor notify the data operator channel. The
channel is **UNKNOWN**: *requires human confirmation* (Slack, email, PagerDuty). Freshness breaches
notify too. Without S6.3 a stopped schedule looks identical to a healthy one.

### S6.4 Metrics (SHOULD)
Per-run duration, rows, bytes, request counts, retries and HTTP status histogram are exported as
Dagster metadata now, and to CloudWatch/OTel in the target (reference §11).

---

## 7. Security

### S7.1 Least privilege (MUST by Phase 7)
- Separate ClickHouse users: `ingest_writer` (own tables), `migrator` (DDL), `api_reader` (SELECT on serving objects).
- Separate PostgreSQL role for news publication limited to `news_articles`.
- Object-store credentials scoped per prefix. Prefer an IAM role over static keys (reference §9).

### S7.2 Private operational surfaces (MUST)
- The Dagster UI and GraphQL are never exposed publicly (X12). Bind to localhost or a private network. Operator access goes through SSH tunnel, VPN, or SSM.
- ClickHouse and Postgres are never exposed publicly. TLS is required whenever traffic leaves a host (`CLICKHOUSE_SECURE=true`).

### S7.3 Outbound fetch safety (MUST for any URL taken from source data)
Scheme and host allowlist; resolve DNS and reject private, loopback, link-local and metadata
addresses **after resolution** (current check is literal-host only, F17); re-validate every redirect
(present); size and time limits (present).

### S7.4 Dependency hygiene (SHOULD)
Locked dependencies (`uv.lock` present); CI runs `uv sync --locked`; a vulnerability scan (e.g.
`pip-audit`) in CI.

---

## 8. Testing

Not every test type is required for every component. This matrix sets the expectation:

| Test type | Required for | Why |
|-----------|--------------|-----|
| Unit (fakes) | Every parser, config model, predicate builder, checkpoint resolver | Fast feedback. Already strong (469 tests). |
| **Warehouse integration (real ClickHouse)** | Every publication mode, every loader's idempotency, overlap and re-run behaviour | Fakes cannot catch mutation, EXCHANGE, ReplacingMergeTree or duplicate semantics (F03 passed unit tests). |
| Contract / schema | Serving contract (backend-read tables and columns) on a fresh warehouse; source row models against recorded fixtures | Prevents F02-style drift and silent provider changes. |
| Pipeline (end-to-end, offline) | One per source: recorded raw fixture → raw asset → bronze → checks, in Dagster `materialize()` | Proves wiring, hand-off and checks together. |
| Failure-path | Timeout, 429, 5xx, malformed row, crash before exchange, duplicate execution | These are the incidents that happen in production. |
| Data validation | Generated asset checks with failing fixtures | Checks that never fail are useless (AGENTS Rule 7). |
| Live provider smoke | Manual, opt-in only, never in CI | Avoids hammering providers and leaking credentials. |

CI (S8.1, MUST) runs Holocron `ruff format --check`, `ruff check`, `ty check`, `pytest` and the
ClickHouse integration suite on every PR touching `holocron/**` or `backend/scripts/clickhouse/**`.
Tests that skip must be reported (AGENTS Rule 10).

---

## 9. Configuration

| Kind | Where | Example |
|------|-------|---------|
| Code defaults | Source `*Config` Pydantic models | page sizes, pauses, timeouts |
| Operational config | Dagster job config mappings in `orchestration/` + source `orchestration.py` | lookback, chunk sizes |
| Environment settings | `holocron/holocron/platform/settings.py` (`Settings`) from env | endpoints, bucket, CH host |
| Secrets | Secret manager → env at runtime only | provider passwords, keys, storage state |

- Host-specific paths (`/root/...`, `/Users/eier/...`) and IP addresses must not appear in production code or units (F14).
- Every setting that differs by environment comes from `Settings`.

---

## 10. Deployment and recovery

- **Reproducible images:** `uv sync --locked`, pinned base image, no `.env*` or local data in the build context (`.dockerignore` covers `.env*` and, as of this audit, `var/`).
- **Browser runtime:** the image contains every runtime a scheduled job needs (Xvfb if any job runs headful, F11).
- **Replay is the recovery path:** raw releases are immutable and ordered dataset versions exist. Recovery = replay into staging → validate → exchange. It is never "truncate live then load".
- **Restore drills** for manual override tables (`geo_*_overrides`, `offplan_*_overrides`) and the Dagster DB, per reference §7.
- **Health:** `holocron.platform.doctor` runs before `up` (present). Dagster daemon heartbeat is monitored.

---

## 11. Definition of "migrated"

A pipeline is **migrated** when it meets every MUST above, its README records licence, SLA,
business keys, publication mode and checks, and it scores PASS or PARTIAL (with documented
exceptions) on every dimension in PIPELINE_AUDITS.md. Record the migration in
[MIGRATION_PLAN.md](../migration/MIGRATION_PLAN.md) → "Migration status".
