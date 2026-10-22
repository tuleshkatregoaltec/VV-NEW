# Data Pipeline Migration Plan

**Audience:** the engineer or coding agent implementing the data-pipeline hardening.
**Inputs:** [PRODUCTION_READINESS_AUDIT.md](../audits/PRODUCTION_READINESS_AUDIT.md) (findings F01–F25),
[PIPELINE_AUDITS.md](../audits/PIPELINE_AUDITS.md), [PIPELINE_STANDARDS.md](../data-pipelines/PIPELINE_STANDARDS.md),
[TARGET_ARCHITECTURE.md](../architecture/TARGET_ARCHITECTURE.md).
**Platform context:** [production-readiness-2026-09-29.md](../../production-readiness-2026-09-29.md)
owns identity, tenancy, AI, cloud provisioning and CI/CD for the API and frontend. This plan
covers **data ingestion, publication, and the warehouse serving contract** only, and it slots
into that document's launch sequence at steps 3, 9, 14 and 15.

## Rules for executing this plan

1. Execute phases in order. Inside Phase 5, follow the listed order unless a dependency note allows otherwise.
2. Every phase leaves `main` deployable. Behaviour changes ship behind the old path until validation passes.
3. Follow `AGENTS.md` and `holocron/AGENTS.md` (surgical changes, `just` recipes, tests encode intent, fail loud).
4. After each phase, update **Migration status** (bottom of this file) and the pipeline's row in [PIPELINE_INVENTORY.md](../data-pipelines/PIPELINE_INVENTORY.md).
5. Anything marked *Requires human confirmation* is a blocking input. Record the answer in the [Decision register](#decision-register) at the end of this file before starting work that depends on it.

## Migration order and rationale

```text
Phase 0  Audit (this document set)                                   DONE
Phase 1  Baseline, guardrails, decisions      ── no data-behaviour change; closes exposure; measures unknowns
Phase 2  Shared infrastructure                ── publish_rebuild, checks, retries, ledger, run control, CH test harness
Phase 3  Pilot: DLD Open Data (P01, 9 units)  ── highest consumer impact, simplest source (HTTP API), one shared code path
Phase 4  Validate the pattern                 ── 14 days in staging, measure, adjust standards
Phase 5  Migrate the rest, in order:
         5A Serving contract + unknown producers (P20, P23, P18 schedules)
         5B Reelly (P11, P12)
         5C DXBI evidence into Dagster (P05, P06)      [gated on licence decision D1]
         5D Property Finder (P08, P09, P10)
         5E News single writer (P14, P15)
         5F DDA (P13)
         5G DXBI map (P04)                              [gated on D1]
         5H Derived automation: silver + geography (P18, P19)
Phase 6  Remove legacy paths and structural debt
Phase 7  Final production hardening (least privilege, migrator, secrets, TLS, backups, drills)
```

Why this order:

- **Phase 1 first** because two issues are cheap and dangerous: the public Dagster UI (F05) and the fact builder that destroys its own source views (F13). It also turns the audit's UNKNOWNs (live duplicates, missing producers, which schedules run, business-key uniqueness) into facts, and Phases 3 and 5A depend on those facts.
- **Shared infrastructure before any source**, so the pilot proves the shared pieces instead of inventing them inline, and later sources copy a working pattern.
- **DLD as the pilot** because:
  - it is the backbone of analytics (`ch_transactions` alone has 58 backend references);
  - it contains a confirmed data-integrity defect (F03), so the pilot delivers value;
  - it uses a plain HTTP API with no browser, so it is low-risk;
  - one shared implementation migrates nine units at once.
- **5A before other sources:** the warehouse must be rebuildable from the repo (F02) before more tables are moved. Reelly (5B) depends on it (`silver_reelly_projects`).
- **DXBI evidence (5C) is P0 but gated:** the laptop publication (F01) must end before launch, but whether DXBI may be automated at all (F04/D1) is a business decision. If D1 is "no", 5C becomes "disable CRM features that depend on it" instead of "migrate".
- **Legacy removal (6) only after replacements are proven**, per the ROLE incremental rule.
- **Least privilege (7) last** because it requires every writer to stop issuing runtime DDL (done progressively in 2–5).

---

## Phase 0 — Repository audit (DONE)

- **Objective:** establish complete coverage.
- **Output:** these documents.
- **Small safe changes made:**
  - `holocron/.dockerignore`: added `var/`, so runtime scratch cannot enter images.
  - `holocron/holocron/sources/dld_open_data.md`: schedule timezone corrected from UTC to Asia/Dubai to match `factory.py`.
- **Checks run:** `holocron: uv run pytest`, 469 passed. Backend tests were not run (they need services and run migrations; `just test-backend` must target disposable databases).

---

## Phase 1 — Baseline, guardrails, and decisions

**Objective:** remove immediate exposure, stop known self-destructive tooling, add Holocron CI, and replace UNKNOWNs with measured facts. **No change to how data is published.**

**Dependencies:** none. Tasks 1.1–1.4 are independent and can be separate PRs.

### 1.1 Make the Dagster UI private (F05)

- **Modify** `holocron/docker-compose.yml`: `ports: - "127.0.0.1:${DAGSTER_PORT:-3000}:3000"`.
- **Modify** `holocron/README.md` "VPS Flow": replace "available on `http://<vps-ip>:3000`" with SSH tunnel instructions (`ssh -L 3000:127.0.0.1:3000 <host>`). Also fix the two drift items in CURRENT_STATE §9 (external Postgres claim; missing `docker-compose.local.yml`).
- **Validation:** from a machine outside the host, `curl -m5 http://<host>:3000` fails; via the tunnel the UI loads.
- **Rollback:** revert the port line.
- **Acceptance:** port 3000 is not reachable on the public interface (`ss -ltnp | grep 3000` shows `127.0.0.1`).

### 1.2 Live warehouse and orchestration baseline (read-only; resolves UNKNOWNs)

Run read-only queries with a read-only user and record the results in a new file,
**`docs/audits/LIVE_BASELINE.md`**, with the date and host. Do not paste secrets or personal data;
aggregate counts only.

| Question | How |
|----------|-----|
| Which host runs Dagster; is it ph-box? | Operator answer + `docker ps` on hosts |
| Which schedules are RUNNING? | `docker compose exec dagster-webserver dagster schedule list -w /app/workspace.yaml` |
| When did each job last succeed? | Dagster UI run history, per job |
| Live DDL of every object the backend reads | `SHOW CREATE TABLE <t>` for all `ch_*`, `dld_od_*`, `dxbi_*`, `pf_*`, `reelly_*`, `dda_*`, `geo_*`, `silver_*`, `offplan_*` |
| Who creates `silver_reelly_projects`, `reelly_project_unit_types_bronze`, extra `reelly_projects_bronze` columns (P23)? | `SELECT event_time, user, query FROM system.query_log WHERE query ILIKE '%silver_reelly_projects%' AND query_kind IN ('Create','Insert','Alter') ORDER BY event_time DESC LIMIT 20` (if retention allows); ask the team |
| Is `ch_project_fact` a view or a table? | `SELECT name, engine FROM system.tables WHERE name LIKE 'ch_%fact%'` |
| F03 duplicates present? | For each of projects/developers/buildings/lands/brokers: `SELECT count() - uniqExact(<key>) FROM dld_od_<c>_bronze WHERE source_system='dld_open_data'`; units: `uniqExact(property_id)` |
| Business-key uniqueness for window tables | `SELECT count(), uniqExact(transaction_number), uniqExact((transaction_number, property_id)) FROM dld_od_transactions_bronze`; same for rents `(contract_number, line_number)`, valuations `(procedure_year, procedure_number)` |
| Table sizes (for the publish-copy ceiling) | `SELECT table, sum(rows), formatReadableSize(sum(bytes_on_disk)) FROM system.parts WHERE active GROUP BY table ORDER BY 2 DESC` |
| Are P05 tables populated in production, and by whom? | `SELECT count(), max(loaded_at) FROM dxbi_rental_events_v2 FINAL` on production vs developer Mac; operator answer |
| Which systemd timers are enabled on ph-box? | `systemctl list-timers 'dxbi-*'` |
| Does the repair service run the repo code? | `diff` `/root/data/dxbi-rental-repair-20260915/*.py` against `holocron/holocron/platform/rental_repair_queue.py` and `holocron/scripts/dxbi_rental_backfill.py` |

- **Acceptance:** every row answered, or explicitly marked "cannot determine" with the reason. P23's producer identified or declared lost.

### 1.3 Holocron CI (F15)

- **Create** `.github/workflows/holocron.yml`:
  - Trigger: `pull_request` on paths `holocron/**`, `.github/workflows/holocron.yml`.
  - Steps: `actions/checkout`, `astral-sh/setup-uv` (pin by SHA as `ai-evaluation.yml` does, Python 3.11), `extractions/setup-just`, `uv sync --project holocron --locked --dev`, then in `holocron/`: `uv run ruff format --check`, `uv run ruff check`, `uv run ty check --error-on-warning`, `uv run pytest -q`.
- **Validation:** a PR touching `holocron/` shows the job green. Deliberately failing a test shows red.
- **Acceptance:** required status check on the default branch (repo settings: *requires human confirmation*, since only an admin can set this).

### 1.4 Guard the fact builder (F13)

> **Superseded (2026-10-03):** #5 fixes F13 by a repeatable rebuild from the source view instead of refusing a second run. Prefer it over the guard below. Still open: a changed-input rebuild on a real ClickHouse (Prompt 05) and the `vitevue_dev` password default.

- **Modify** `backend/scripts/clickhouse/build_fact_tables.py`:
  - Before any DROP, query `system.tables` for `ch_project_fact` and `ch_area_fact`. If either `engine != 'View'`, abort with exit code 2 and print "facts already materialised; recreate views with create_compatibility_views.py first". This stops the second run from dropping `*__source_view`.
  - Replace the `vitevue_dev` password default with a required env var (fail if unset).
- **Create** `backend/tests/unit/test_build_fact_tables.py`: with a fake client returning `engine='MergeTree'` the builder aborts and issues no DROP. With `View` it proceeds.
- **Rollback:** revert the file.
- **Acceptance:** running the builder twice in a row against the dev ClickHouse leaves a working `ch_project_fact` and the second run exits 2.

### 1.5 Decision register

- **Create** the decisions listed in the [Decision register](#decision-register) as open items.
- Owners are assigned by the engineering lead.
- Phases 5C, 5G and 7 cannot start until the relevant decisions are recorded.

**Phase 1 files**

- **Modify:** `holocron/docker-compose.yml`, `holocron/README.md`, `backend/scripts/clickhouse/build_fact_tables.py`.
- **Create:** `.github/workflows/holocron.yml`, `docs/audits/LIVE_BASELINE.md`, `backend/tests/unit/test_build_fact_tables.py`.
- **Remove:** none.

**Phase 1 acceptance:**

- 1.1–1.4 merged.
- `LIVE_BASELINE.md` complete.
- The decision register has owners.

---

## Phase 2 — Shared pipeline infrastructure

**Objective:** build the shared pieces every migrated pipeline uses. Nothing switches over yet except where noted. Each task is a separate PR with its own tests.

**Dependencies:** Phase 1.3 (CI exists to run the new tests).

### 2.1 ClickHouse integration test harness + serving contract

- **Create** `holocron/tests/integration/conftest.py`:
  - A `clickhouse` fixture connecting to `CLICKHOUSE_TEST_URL` (skip, **with a visible reason**, when unset).
  - Creates a unique database per test session and applies `clickhouse_schema.sql` via `iter_sql_statements`.
- **Create** `holocron/holocron/lib/sql/serving_contract.py`:
  - `SERVING_CONTRACT: dict[str, tuple[str, ...]]` mapping each ClickHouse object the backend reads to the columns it uses.
  - Seed it by grepping `backend/app/**/queries.py` for `FROM <object>` plus the selected columns. Use `LIVE_BASELINE.md` DDL for objects the repo does not create; those objects are expected to fail initially (see 5A).
- **Create** `holocron/tests/integration/test_serving_contract.py`: on a freshly schema-initialised database, every contract object exists with its columns.
  - Mark the known gaps (`silver_reelly_projects`, `reelly_project_unit_types_bronze`, the backend-owned `ch_*` views, extra Reelly columns) with `pytest.mark.xfail(strict=True, reason="F02: fixed in Phase 5A")`. Strict xfail makes the test fail loudly once they are fixed, prompting removal.
- **Modify** `.github/workflows/holocron.yml`: add a `services: clickhouse: image: clickhouse/clickhouse-server:24.8` (same version as `docker-compose.dev.yml`) and set `CLICKHOUSE_TEST_URL`.
- **Acceptance:** CI runs the integration suite, reports 0 skipped, and the xfails are listed.

### 2.2 `publish_rebuild` primitive

- **Create** `holocron/holocron/platform/publish.py` implementing [TARGET_ARCHITECTURE §6.2](../architecture/TARGET_ARCHITECTURE.md): `publish_rebuild`, `keep_outside_window`, `keep_other_keys`, `PublishResult`, `PublicationRejected`, `StagingStats` (`rows_before`, `rows_after`, `rows_carried`, `rows_new`, `duplicate_keys`).
  - The staging name is `<table>_staging_<safe run_id>`, via the existing `ClickHouseClient._staging_name` logic.
  - For `keep_other_keys`, insert the batch keys into a `Memory`/`MergeTree` temp table `<table>_keys_<run>` and use `key NOT IN (SELECT key FROM …)`. Drop it in `finally`.
  - `validate` runs **before** `EXCHANGE`. Any exception drops staging and re-raises.
- **Modify** `holocron/holocron/platform/clickhouse.py`: make `replace_table_rows` delegate to `publish_rebuild(keep_where="0", validate=noop)`, so existing callers keep working with one implementation.
- **Create** `holocron/tests/test_publish.py` (fakes: SQL sequence, staging dropped on validation failure) and `holocron/tests/integration/test_publish_integration.py`:
  - a concurrent reader thread polling `count()` during publish never sees a value outside {before, after};
  - an exception injected in `validate` leaves the live table byte-identical (`cityHash64` fingerprint, as in `platform/sync_dld.py::fingerprint`);
  - re-running with the same batch is idempotent.
- **Acceptance:** all new tests pass. Existing 469 unit tests unchanged and green.

### 2.3 Publication ledger on every publish

- **Modify** `holocron/holocron/lib/sql/clickhouse_schema.sql`: append `ALTER TABLE bronze_load_ledger ADD COLUMN IF NOT EXISTS …` for `rows_in UInt64 DEFAULT 0`, `rows_rejected UInt64 DEFAULT 0`, `previous_rows UInt64 DEFAULT 0`, `publish_mode LowCardinality(String) DEFAULT ''`, `duration_ms UInt64 DEFAULT 0`, `dagster_run_id String DEFAULT ''`.
- **Modify** `platform/publish.py`: write one ledger row on success and on `PublicationRejected`.
- **Modify** `platform/bronze_replay.py`: keep its ledger writes; add the new columns.
- **Test:** the integration test asserts one ledger row per publish, with correct counts.

### 2.4 Table metadata and generated asset checks

- **Modify** `holocron/holocron/contracts.py`: extend `BronzeTable` with `publish_mode`, `min_row_ratio`, `freshness_column`, `freshness_sla_hours`, `required_columns` (defaults preserve current behaviour). Add `SourceSpec.production_approved: bool = False` and `SourceSpec.run_tags: Mapping[str, str]`.
- **Modify** `holocron/holocron/sources/registry.py`: validate `publish_mode` values; require `source_key` when `publish_mode == "key_replace"`.
- **Create** `holocron/holocron/orchestration/checks.py`: `build_checks_for_source(source)`, which returns `@asset_check`s on `<source>_bronze` for:
  - unique business key (per table with `source_key`);
  - required non-default ratio;
  - freshness (from the ledger or `freshness_column`).

  The row floor is enforced inside the publisher, not as a check, so it can block.
- **Modify** `holocron/holocron/orchestration/definitions.py`: add `asset_checks=[...]` from the factory.
- **Create** `holocron/tests/test_checks.py` (one failing and one passing fixture per check).
- **Acceptance:** `Definitions` loads (`test_definitions.py`); checks appear in the Dagster UI; no behaviour change to loads.

### 2.5 Retry helper

- **Create** `holocron/holocron/platform/http_retry.py` (`is_retryable`, `backoff_seconds` with full jitter and `Retry-After`, `retry_call`) per [TARGET_ARCHITECTURE §6.4](../architecture/TARGET_ARCHITECTURE.md).
- **Create** `holocron/tests/test_http_retry.py` (408/429/5xx/timeouts retried; 400/401/403/404 not; `Retry-After` honoured; sleeps bounded).
- Not wired into sources yet; each source adopts it in its migration phase.

### 2.6 Run control: queue, concurrency, retries, alerts, durable hand-off

- **Modify** `holocron/dagster.yaml`:
  ```yaml
  run_coordinator:
    module: dagster.core.run_coordinator
    class: QueuedRunCoordinator
    config:
      max_concurrent_runs: 4
      tag_concurrency_limits:
        - {key: "holocron/table", value: {applyLimitPerUniqueValue: true}, limit: 1}
        - {key: "holocron/resource", value: "browser", limit: 1}
  run_monitoring:
    enabled: true
  ```
  Keep the starting limits conservative. The browser limit of 1 matches reference §10.
- **Modify** `holocron/holocron/orchestration/factory.py`:
  - Every job built by `_build_source_asset_job` / `define_asset_job` gets tags `holocron/table=<primary bronze table or source>`, plus `holocron/resource=browser` for `dxbi_transactions`, `pf_listings`, `bayut_listings`, and `dagster/max_runtime` (seconds; defaults: 4 h browser, 1 h API).
  - Raw assets get `retry_policy=RetryPolicy(max_retries=2, delay=60, backoff=Backoff.EXPONENTIAL, jitter=Jitter.PLUS_MINUS)`. Bronze assets get `max_retries=1`.
- **Durable hand-off (X5, S2.7):** modify `build_assets_for_source` so the bronze asset no longer depends on the IO-manager pickle.
  - Declare `deps=[raw]` instead of `ins`.
  - Read the latest raw materialisation's `manifest_s3_key` metadata via `context.instance.get_latest_materialization_event(AssetKey(raw_name))`.
  - Load the manifest with `holocron.platform.raw_files.read_and_validate_manifest(s3=…, source_name=…, manifest_key=…)`.
  - Allow an explicit `manifest_key` in bronze asset config to override (this makes "re-run bronze for an older release" a supported operation).
- **Create** `holocron/holocron/orchestration/sensors.py`: `run_failure_sensor` and an asset-check failure sensor posting JSON to `ALERT_WEBHOOK_URL` (env; skip with a startup warning when unset). The channel is decision D3.
- **Modify** `holocron/holocron/platform/settings.py`: add `alert_webhook_url` (`ALERT_WEBHOOK_URL`) and `holocron_environment` (`HOLOCRON_ENVIRONMENT`, default `local`).
- **Tests:** `test_definitions.py` asserts every job has a `holocron/table` tag, raw assets have a retry policy, and the bronze asset has no `ins`. A test loads a manifest by key from `tests/fakes.py` storage.
- **Validation (staging):** launch the same DLD job twice quickly; the second queues. Kill a bronze step; re-run bronze alone in a fresh container; it succeeds.

### 2.7 Production-approval gate

- **Modify** `factory.py::build_operational_schedules`: when `settings.holocron_environment == "production"`, schedules for sources with `production_approved=False` are not created (log a warning). Schedules for approved sources default to `RUNNING` in production and `STOPPED` elsewhere.
- **Test:** unit test over a fake registry for both environments.

**Phase 2 files**

- **Create:** `holocron/holocron/platform/publish.py`, `platform/http_retry.py`, `orchestration/checks.py`, `orchestration/sensors.py`, `lib/sql/serving_contract.py`; tests `test_publish.py`, `test_http_retry.py`, `test_checks.py`, `integration/conftest.py`, `integration/test_publish_integration.py`, `integration/test_serving_contract.py`.
- **Modify:** `holocron/dagster.yaml`, `contracts.py`, `sources/registry.py`, `platform/clickhouse.py`, `platform/settings.py`, `platform/bronze_replay.py`, `orchestration/factory.py`, `orchestration/definitions.py`, `lib/sql/clickhouse_schema.sql`, `.github/workflows/holocron.yml`, `holocron/tests/test_definitions.py`.
- **Remove:** none.

**Dependencies (new):** none required. `QueuedRunCoordinator`, `RetryPolicy`, asset checks and sensors are in `dagster` 1.13.3 (locked).

**Rollback:** each task is an isolated PR. `dagster.yaml` reverts to `DefaultRunCoordinator`. `replace_table_rows` delegation reverts independently.

**Acceptance:**

- CI green with integration tests running (0 unexplained skips).
- A deliberately failing check or run produces a webhook alert in staging.
- All existing jobs still materialise in staging with unchanged row counts (compare the ledger/metadata before and after).

---

## Phase 3 — Pilot: DLD Open Data (P01)

**Objective:** bring all nine DLD units to the standard. Fix F03 (duplicates) and F24 (transactions gap). Make publication atomic and checked.

**Dependencies:** Phase 2 complete; `LIVE_BASELINE.md` business-key measurements (1.2).

### 3.1 Declare keys, modes and SLAs

**Modify** each `holocron/holocron/sources/dld_od_<c>/source.py` (via
`build_open_data_endpoint`/`build_open_data_source` in `dld_open_data_shared.py`) to pass table
metadata. Proposed values; **confirm the keys with 1.2 measurements before merging**:

| Unit | publish_mode | Key / window column | Freshness |
|------|--------------|---------------------|-----------|
| transactions | window_replace | `instance_date` | 36 h |
| rents | window_replace | `registration_date` (as configured today) | 36 h |
| valuations | window_replace | `instance_date` (add `row_date_field="INSTANCE_DATE"`, `request_to_date_offset_days=1` like transactions, only if the provider's `P_TO_DATE` is exclusive; verify with one manual backfill) | 72 h |
| projects | key_replace | `project_number` | 72 h |
| developers | key_replace | `developer_number` | 7 d |
| buildings | key_replace | `building_number` (or `property_id` if 1.2 shows `building_number` is not unique) | 7 d |
| units | key_replace | `property_id` | 7 d |
| lands | key_replace | `land_number` | 7 d |
| brokers | key_replace | `broker_number` | 7 d |

If 1.2 shows a key is not unique, stop and choose a composite key with the data owner. Do not
silently collapse rows.

### 3.2 Switch the loader to `publish_rebuild`

- **Modify** `holocron/holocron/sources/dld_open_data_bronze.py::load_open_data_release_to_clickhouse`:
  - `window_replace`: `keep_where = keep_outside_window(scope_sql="source_system = 'dld_open_data'", window_sql=<existing date predicate from _open_data_delete_predicate>)`.
  - `key_replace`: `keep_where = keep_other_keys(scope_sql=…, key_column=<key>, …)`.
  - Snapshot releases (`metadata.stage == "snapshot"` and `completed_result_set`) with `full_replace` semantics keep rows **outside** `source_system='dld_open_data'` only, which preserves Pulse history.
  - Validation: `rows_after >= min_row_ratio * rows_before` for snapshots; `duplicate_keys == 0` for `key_replace` tables.
  - Remove the calls to `replace_where_rows` / `replace_keyed_rows` from this module.
- **Keep** `ensure_schema` for now (removed in Phase 7).
- **Bronze row key:** keep `row_key` as a provenance column but stop using it for identity. Identity is the declared business key.

### 3.3 Checkpointed incremental for all date-window units (F24)

- **Modify** `holocron/holocron/sources/dld_open_data_orchestration.py`: set `checkpointed_incremental=True` and `incremental_overlap_days=7` in `DLD_OPEN_DATA_INCREMENTAL_DEFAULTS`, as rents already does. Keep `lookback_days` as the first-run fallback.
- **Modify** `factory.py::_DLD_OPEN_DATA_JOB_SPECS` if the per-endpoint defaults need to diverge.

### 3.4 Retries and rate limits

- **Modify** `DldOpenDataApiClient.fetch` to use `platform/http_retry.retry_call` (adds 429, jitter, `Retry-After`).
- Set `request_pause_seconds` default to `0.5` (S3.2). Record the value and the reason in `sources/dld_open_data.md`.

### 3.5 One-time duplicate clean-up (only if 1.2 found duplicates)

- **Create** `holocron/holocron/platform/dld_dedupe.py` (CLI, `--dry-run` default):
  - For each affected table, `publish_rebuild(keep_where="0")` with rows `SELECT * FROM t WHERE source_system='dld_open_data' ORDER BY scraped_at DESC LIMIT 1 BY <key>` UNION ALL non-open-data rows.
  - Validation: `rows_after == uniqExact(key) + other_source_rows`.
- Run in staging, compare counts, then production during a quiet window.
- **Alternative:** replay the latest complete snapshot dataset version via `just replay-bronze prod dld_od_<c> --dataset-version latest_complete --initial-load`. **Do not use this until 3.6 is done**, because initial-load truncates live first.

### 3.6 Make replay safe (F06)

- **Modify** `holocron/holocron/platform/bronze_replay.py`: replace the `--initial-load` TRUNCATE path.
  - Replay into `<table>_replay_<ts>` staging tables: point the loader at a `ClickHouseWriter` wrapper that rewrites table names, or give loaders a `target_table` parameter.
  - Validate row counts against the manifests.
  - `EXCHANGE` all tables only if **zero** manifests failed. Otherwise leave live untouched and report.
- **Test** in `holocron/tests/integration/test_replay_integration.py`: a failing manifest leaves live tables unchanged.

### 3.7 Tests

- **Modify** `holocron/tests/test_dld_open_data_bronze.py`. The existing assertion `date_window_start = …` encodes the defect; replace it with assertions on the keep predicate per mode.
- **Create** `holocron/tests/integration/test_dld_open_data_publish.py`, with intent-focused tests:
  - two daily incrementals with overlapping windows produce `count() == uniqExact(key)`;
  - rerunning the same manifest is a no-op on counts;
  - Pulse rows survive every mode;
  - a snapshot below the floor is rejected and live is unchanged.
- **Create** a pipeline test: recorded two-page fixture → `dagster.materialize([dld_od_projects_raw, dld_od_projects_bronze])` with fake object storage and real ClickHouse.

**Phase 3 files**

- **Modify:** `holocron/holocron/sources/dld_open_data_shared.py`, `dld_open_data_bronze.py`, `dld_open_data_orchestration.py`, `sources/dld_od_*/source.py` (9), `platform/bronze_replay.py`, `sources/dld_open_data.md`, `holocron/tests/test_dld_open_data_bronze.py`, `test_dld_open_data_sources.py`.
- **Create:** `platform/dld_dedupe.py`, integration tests above.
- **Remove:** none. `replace_where_rows`/`replace_keyed_rows` stay until Phase 6 because other sources still use them.

**Validation:**

- In staging, enable the nine schedules for 3 days; checks green; ledger rows daily.
- `count() == uniqExact(key)` for key tables.
- Transactions daily counts are within ±5 % of the DLD website totals for 5 sampled days (manual comparison recorded in `LIVE_BASELINE.md`).
- Backend smoke: analytics, radar and transactions pages load against staging with no errors.

**Rollback:** revert the loader PR. Data published by the new path is valid for the old path (same tables and columns). A dedupe run is not reversible, but the pre-dedupe table is kept as `<table>_pre_dedupe_<date>` for 14 days (make the CLI do an `EXCHANGE` plus rename, not a drop).

**Acceptance:**

- P01 scores: Reliability PASS, Data Quality PASS, Recoverability PASS, Testing PASS.
- Observability is at least PARTIAL, with alerts wired.
- No duplicate keys; zero delete mutations issued by DLD loads (`system.mutations` shows none from DLD tables after cut-over).

---

## Phase 4 — Validate the architecture

**Objective:** prove the pattern before copying it 15 times.

- **Run** the Phase 3 configuration in staging for 14 consecutive days with schedules RUNNING.
- **Measure:**
  - publish duration per table (the ledger `duration_ms`);
  - peak ClickHouse memory for `publish_rebuild` (`system.query_log`);
  - check false-positive rate;
  - alert noise;
  - run queue wait times.
- **Decide** (record as ADRs in `docs/architecture/decisions/000N-<title>.md`):
  - Keep the copy-and-exchange publish, or move specific tables to partition replace (trigger: >10 min or memory pressure).
  - Tune the `min_row_ratio` and freshness SLAs.
  - Confirm the concurrency limits.
- **Update** `PIPELINE_STANDARDS.md` with any changes and bump its "Status" date.
- **Acceptance:**
  - 14 days with no missed publication, no unexplained check failure, and no manual intervention.
  - ADRs merged.

---

## Phase 5 — Migrate remaining pipelines

Each sub-phase follows the same template:

1. Declare metadata (keys, mode, SLA, `production_approved`, tags).
2. Adopt `http_retry` and rate caps.
3. Switch the loader to `publish_rebuild`.
4. Add integration and pipeline tests.
5. Run 7 days in staging.
6. Update the inventory and audits.

Only deviations are listed.

### 5A Serving contract and unknown producers (P20, P23, P18 schedules) — F02, F13, F07

> **Status (2026-10-03): partial, code ready in #5.** Done: DDL for `silver_reelly_projects` and `reelly_project_unit_types_bronze`; F13 rebuild; `ch_transactions` / `ch_rent_contracts` now read the canonical market layer. Not done: Reelly producers, serving views and facts moved into Holocron, cache invalidation asset, silver schedule changes (F07), backend fixtures on canonical DDL.

- **Codify P23:**
  - Using `LIVE_BASELINE.md`, add DDL for `silver_reelly_projects`, `reelly_project_unit_types_bronze` and the extra `reelly_projects_bronze` columns to `clickhouse_schema.sql`.
  - Implement their producers as Holocron assets: `holocron/holocron/silver/reelly.py` (derived from `reelly_projects_bronze.raw_json`), registered in `build_silver_assets`.
  - If the original producer is lost, derive them from raw JSON and validate against the live tables (row counts, key overlap ≥ 99 %).
- **Move serving views into Holocron:**
  - **Create** `holocron/holocron/lib/sql/serving_views.sql`, copied from `backend/scripts/clickhouse/create_compatibility_views.py` `VIEW_DDL`.
  - Apply it with `ensure_schema` (Phase 7 moves it to the migrator).
- **Facts as a Dagster asset:**
  - **Create** `holocron/holocron/assets/serving_facts.py`, with assets `ch_project_fact_table` and `ch_area_fact_table`.
  - Keep the canonical definitions as views named `ch_project_fact_source` / `ch_area_fact_source`.
  - Materialise via `publish_rebuild(keep_where="0")` into `ch_project_fact` / `ch_area_fact` **tables**, then point the backend at those tables.
  - Deps: `dld_od_transactions_bronze`, `dld_od_rents_bronze`, `dld_od_projects_bronze`.
- **Cache invalidation asset:**
  - **Create** `holocron/holocron/assets/cache_invalidation.py`.
  - It depends on the facts and on the P01, P09 and P11 bronze assets, and deletes Redis prefixes (`transactions:*`, `analytics:*`, …) using a new `REDIS_URL` setting.
  - This replaces the Mac-only invalidation in `sync_market_data.py`.
- **Silver schedules (F07):**
  - Remove the `silver_refresh` schedule and the separate 06:00/06:10 silver schedules from `factory.py::build_operational_schedules`.
  - Give silver assets `automation_condition=AutomationCondition.eager()` so they run after their bronze inputs publish.
  - Port `sync_silver.validate_replacement`'s date-coverage check into the silver publish `validate`.
- **Backend:**
  - Mark `backend/scripts/clickhouse/create_compatibility_views.py` and `build_fact_tables.py` deprecated (module docstring pointing to Holocron). Deletion happens in Phase 6.
  - **Modify** backend ClickHouse integration fixtures (`backend/tests/integration/clickhouse/*.py`) to create tables from Holocron's schema and view files instead of hand-written DDL (import via path; both are in the monorepo).
- **Acceptance:**
  - `test_serving_contract.py` passes with **no xfails**.
  - A fresh warehouse built from the repo serves every backend page in staging (Playwright smoke `e2e/`).

### 5B Reelly (P11, P12)

- `ReellyApiClient` requests go through `retry_call`. Set a `request_pause_seconds` default of `0.5`.
- Remove `email` and `password` fields from `ReellySupplyConfig` (S3.4). Credentials come from env only. Update `holocron/tests/test_reelly_supply.py`.
- Bronze: `key_replace` on `project_key`, `document_key`, `availability_key` via `publish_rebuild`. Add `last_seen_at`. Backend queries keep `LIMIT 1 BY project_id` until the history rows are compacted.
- **Acceptance:**
  - Supply pages in staging match production for 20 sampled projects.
  - The contract test is green.

### 5C DXBI evidence into Dagster (P05, P06) — F01, F14 — **gated on D1**

> **Status (2026-10-03): partial, code ready in #5.** The evidence-loading half exists as `holocron/holocron/sources/dxbi/evidence.py` (asset `dxbi_evidence_bronze`, job `dxbi_evidence_load`). It loads finished scraper runs with a raw archive and `dxbi_evidence_load_ledger`, and replaces the Mac `.loaded` markers. It reuses the existing loader scripts rather than the `dxbi_reports` package below. Extraction, the repair queue (P06), host-path removal and cut-over remain. External dependencies: scraper on ph-box, production host D4, licence D1.

**If D1 = "automation not permitted":**

- Disable the ph-box timers.
- Mark the CRM features that depend on DXBI as unavailable. Coordinate with product; the reference doc §15 covers disabling deferred features.
- Stop here.

**If D1 = "permitted":**

- **Create** `holocron/holocron/sources/dxbi_reports/` (new source package; do not overload `dxbi_transactions`):
  - `source.py`: units `dxbi_rentals` and `dxbi_sales`; provider `dxbinteract`; tags `holocron/resource=browser`.
  - `extract.py`: wraps the logic of `holocron/scripts/dxbi_rental_backfill.py`. Move the reusable functions into the package and keep the script as a thin CLI. It writes the run folder into the scratch dir and returns a `RawRelease`, so raw goes to object storage, not host disk.
  - `bronze.py`: calls the existing loading logic from `scripts/load_dxbi_rental_events.py` and `load_dxbi_sales_units.py`, moved into `holocron/holocron/sources/dxbi_reports/load_*.py`. Keep their reconciliation and staging+EXCHANGE, and delete their inline DDL in favour of `clickhouse_schema.sql`.
  - `orchestration.py`: config mappings reproducing `dxbi-report-daily.sh` windows (rentals daily 14→0 days, weekly 180→15, monthly 365→181; sales daily 7→0).
- **Modify** `factory.py`: jobs `dxbi_rentals_daily|weekly|monthly`, `dxbi_sales_daily` with Asia/Dubai schedules equivalent to the systemd timers, and `dagster/max_runtime` 3 h/12 h/24 h. Add the downstream `geography_crosswalk_refresh` via automation. Add a CRM refresh asset that runs `backend/scripts/crm/refresh_rental_imports.py` logic.
  - Preferred: expose that logic as an importable function the asset calls.
  - Otherwise: a subprocess with the backend venv, as `sync_market_data.py` does today.
- **Credentials:** `DXBI_ACCOUNT_A_*` / `DXBI_ACCOUNT_B_*` env vars plus storage-state files on an encrypted, run-user-only path from `Settings` (no `/root` paths).
- **Repair queue (P06):** port the `/root/data/...` copy into the repo first (diff from 1.2). Then run it as a finite Dagster job `dxbi_rentals_repair` with the plan file in object storage.
- **Cut-over:**
  1. Run the Dagster jobs in staging for 7 days in parallel with systemd, writing to staging tables.
  2. Compare the reconciliation JSON with the systemd outputs.
  3. Switch production.
  4. Disable the systemd timers (`systemctl disable --now dxbi-*.timer`).
- **Acceptance:**
  - Production `dxbi_rental_events_v2` is fed only by Dagster.
  - Freshness checks green.
  - Laptop sync disabled for production purposes.
  - The CRM owner report for a test organisation matches pre-cut-over output.

### 5D Property Finder (P08, P09, P10) — F18

- `pf_locations`: `full_replace` with `min_row_ratio=0.95`; add `retry_call` to extract.
- `pf_listings`: `key_replace` on `property_key`. Add a `last_seen_at` column and an `is_active` derivation: a listing not seen in the latest *complete* full crawl dataset version is inactive.
  - **Create** a weekly scheduled full-crawl job chain (plan → search chunks → details), so withdrawal detection has a data source.
  - **Modify** `backend/app/listings/queries.py` to filter `is_active = 1` and to take freshness from the ledger (`GET /api/v1/data/freshness` or a query on `bronze_load_ledger`).
  - Regenerate the frontend client if response models change (`just generate-client`).
- **Acceptance:** a listing removed from a fixture crawl disappears from the API after the next full crawl.

### 5E News single writer (P14, P15) — F12

- **Backend (owner of the schema):**
  - **Create** an Alembic migration in `backend/migrations/versions/` that adds the Holocron columns (`article_key`, `canonical_url`, `summary`, `source_name`, `source_feed_url`, `discovered_at`, `topic_tags`, `raw_manifest_key`, `raw_run_id`, `is_active`, `updated_at`), widens `url`/`headline`/`thumbnail_url` to `text`, and adds `UNIQUE (article_key)`.
  - The migration must be idempotent against databases where Holocron already added columns (`IF NOT EXISTS` via `op.execute`), and must de-duplicate existing rows by `article_key`/`url` before creating the unique index.
  - Update `backend/app/news/models.py`.
- **Holocron:**
  - **Modify** `sources/news_feeds/assets.py::PlatformNewsArticleWriter` to remove `_ensure_news_articles_schema` (and the `ensure_schema` config) and use a single `INSERT … ON CONFLICT (article_key) DO UPDATE`. Use one transaction with per-row savepoints (`connection.transaction()` nested) so one bad row doesn't abort the batch.
  - Report counts from committed results only.
  - **Modify** `factory.py`: the scheduled job becomes `news_feeds_poll_hydrate_publish` (raw → thumbnails → publish) every 15 min, with tag `holocron/table=news_articles` so runs cannot overlap. Keep the backfill job.
  - Set a default thumbnail host allowlist, or rely on the DNS-resolving SSRF guard from Phase 7.4 plus size and content-type caps.
- **Remove** in Phase 6: `backend/scripts/news/import_from_s3.py` and `POST /api/v1/upload/news/article`, **only if** 1.2 confirmed no external caller (decision D5).
- **Tests:**
  - Backend: `backend/tests/integration/postgres/test_news_ingestion.py` extended for the unique key.
  - Holocron: `holocron/tests/test_news_feeds_assets.py` has a failing row mid-batch, and the other rows commit.
- **Acceptance:** the frontend news feed updates within 60 minutes without manual action. The freshness check is green.

### 5F DDA (P13) — F08

- **Modify** `sources/dda/extract.py::_fetch_plot_info`: after retries, return a failure marker instead of a stub row. Write failed plot numbers to `dda_plot_failures.csv` (raw file) and record `failed_plot_count` in manifest metadata.
- Bronze validate: reject publication when `failed_plot_count / total > 0.01` (configurable) or when the row floor fails.
- Default `concurrency` 20 → 4 (S3.2). Measure the duration in Phase 4 style, and confirm against the quota in the DDA README (D2).
- `max_plots` present means a smoke run: the factory refuses the bronze step (`no_capped_run`).

### 5G DXBI map (P04) — F11 — **gated on D1**

- Set `headless=True` in the scheduled config, or install `xvfb` in `holocron/Dockerfile` if headful is required (verify which works; record it in the README).
- Row floor on heatmap tables. `max_heatmap_buildings` means a smoke run, which blocks bronze.
- Split discovery and hydration into chunked runs (as the README proposes) only if Phase 4 measurements show runs exceed 2 h.

### 5H Derived automation: silver and geography (P18, P19)

- Geography assets get `automation_condition=AutomationCondition.eager()`. Replace `holocron/scripts/refresh_geography_crosswalk.py` usage in the Mac sync with Dagster.
- Add blocking validation: link-rate regression (`linked_count / signature_count` must not drop more than 2 percentage points versus the previous build) before exchange.
- Back up override tables: a daily asset that exports `geo_entity_link_overrides` and `offplan_reelly_project_link_overrides` to `backups/overrides/<date>/*.jsonl` in object storage.
- `holocron/apps/entity_admin/app.py` and `lib/entities/*.yaml`: decide (D6) whether the YAML aliases should feed the resolver. If not, remove the YAML-editing tabs.

---

## Phase 6 — Remove legacy paths and structural debt

**Objective:** delete what the migrated pipelines replaced. Each removal is its own PR, and only after the replacing phase's acceptance criteria are met.

| Remove | Condition | Files |
|--------|-----------|-------|
| DXBI systemd units and runner script | 5C accepted | `holocron/deploy/systemd/*` (keep in git history) |
| Laptop production role | 5A, 5C, 5H accepted | Keep `holocron/holocron/platform/sync_*.py`, `scripts/sync_dxbi_daily_runs.sh` and `deploy/launchd/*` **only** as documented developer mirror tooling. Remove the CRM refresh and cache invalidation stages from `sync_market_data.py`. Replace hard-coded `/Users/eier`, `root@<ip>` defaults with required env vars |
| Legacy JS backfills | Coverage of their date ranges confirmed in production tables | `holocron/scripts/dxbi_*.js`, `dxbi_sales_*supervisor*.sh`, `dxbi_sales_watchdog.sh`, `dxbi_rental_area_supervisor.sh`, `load_dxbi_normalized_sales_csv.py` |
| Delete+insert helpers | All loaders on `publish_rebuild` | `replace_run_rows`, `replace_where_rows`, `replace_keyed_rows` in `holocron/holocron/platform/bronze.py` and their tests |
| Backend serving scripts | 5A accepted | `backend/scripts/clickhouse/create_compatibility_views.py`, `build_fact_tables.py`; root `justfile` recipes `build-clickhouse-facts`, `load-clickhouse-local` (the latter already references a missing script) |
| Demo imports | Decision D6/D7 | `backend/scripts/radar/import_mashrooi_coordinates_demo.py`, `backend/scripts/listings/import_pf_listings_demo.py` |
| Chunked upload ingress | D5 = no external caller | `backend/app/upload/*` chunked endpoint and public upload health (keep the news route only if D5 says so), frontend generated client regenerated, related tests |
| Bayut handoff copies | Always | `holocron/handoff/` (move to a private archive if still needed), `holocron/.playwright-mcp/` snapshots |
| Factory source-name branches (F22) | Phase 2 hooks exist | Replace `if source.name == …` blocks in `orchestration/factory.py::_run_raw_extractor` with `SourceSpec.resolve_config` / `after_manifest` / `uses_state_store` hooks set in each `source.py` |
| Duplicate DDL in loader scripts | 5C accepted | Inline `CREATE TABLE` in DXBI loader modules |

**Acceptance:**

- `grep -rn "/Users/eier\|/root/\|188\.\|vitevue_dev" holocron/holocron backend/app` returns nothing in production code paths.
- The factory has no source-name conditionals.
- CI green.

---

## Phase 7 — Final production hardening

Aligns with reference §4, §7, §8, §12 and §15 (steps 9, 14, 17, 18).

### 7.1 ClickHouse migrator and least privilege (S4.2, S7.1)

- **Create** `holocron/holocron/lib/sql/migrations/0001_baseline.sql` (current schema + views) and `holocron/holocron/platform/clickhouse_migrate.py` (applies files in order, records them in `schema_migrations`, with a lock via a `schema_migrations_lock` row, or a single-runner job).
- **Modify** all loaders to stop calling `ensure_schema`. Turn `clickhouse_schema.py::ensure_schema` into a test-only helper.
- **Create** ClickHouse users and roles (in the migration or IaC): `holocron_writer`, `holocron_migrator`, `api_reader`. The backend `CLICKHOUSE_USER` switches to `api_reader`.
- **Acceptance:**
  - Loads succeed with `holocron_writer`, which has no `CREATE VIEW` or `ALTER` except on staging.
  - The backend works with `api_reader`.
  - A DDL attempt by `api_reader` fails.

### 7.2 Secrets and network

- Provider credentials come from the secret manager into the ingestion host env. Storage-state files are encrypted at rest with mode 0600, owned by the run user.
- `CLICKHOUSE_SECURE=true` wherever traffic leaves the host. Remove the plaintext default from `holocron/docker-compose.yml`.
- Object storage uses an IAM role when on AWS. **Modify** `holocron/holocron/platform/object_storage.py` to omit static keys when unset, so boto3 uses its default credential chain (the reference notes the backend has the same gap).

### 7.3 Backups and recovery drills

- Object storage lifecycle and versioning for `raw/`, `state/`, `media/`, `backups/`.
- A monthly drill:
  1. Restore the warehouse from backup, or rebuild it from the repo schema plus replay of `latest_complete` dataset versions into a scratch service.
  2. Run `test_serving_contract` and sample-query parity against production.
  3. Record elapsed time against RTO (reference §7: ≤ 4 h).
- Restore override tables from `backups/overrides/`.

### 7.4 Outbound fetch hardening (F17)

- **Modify** `holocron/holocron/platform/http_download.py`: resolve the host (`socket.getaddrinfo`) and reject if any resolved address is unsafe. Pin the connection to the validated IP (httpx transport with a custom resolver, or connect by IP with a `Host` header and SNI).
- **Test** in `holocron/tests/test_http_download.py`: a hostname resolving to `127.0.0.1` or `169.254.169.254` is rejected.

### 7.5 Operations

- **Runbooks** in `holocron/docs/runbooks/`:
  - `alert-response.md` (per alert: meaning, first checks, replay command);
  - `source-credential-rotation.md`;
  - `replay-and-restore.md`.
- **Named primary/backup data operator** (D3).
- **Load test:** reference §15 step 17 includes ingestion running concurrently with user traffic. Confirm publish swaps do not spike API p95.

**Phase 7 acceptance:**

- Every pipeline in the inventory is "migrated" per [PIPELINE_STANDARDS §11](../data-pipelines/PIPELINE_STANDARDS.md), or explicitly retired.
- A restore drill has been completed.
- Least-privilege roles are in force.

---

## Decision register

Open decisions that block specific phases. Record the answer, the owner and the date in this table when resolved.

| ID | Decision | Blocks | Owner | Answer |
|----|----------|--------|-------|--------|
| D1 | Are automated collection of DXB Interact (licensed accounts, CAPTCHA solving, client-signature generation), Property Finder, Reelly and Bayut permitted under contract/terms? Which are required for launch? | 5C, 5G; `production_approved` for P04, P05, P09, P11, P17 | Product + legal | *open* |
| D2 | DDA ArcGIS quota and acceptable request rate | 5F | Data owner | *open* |
| D3 | Alert channel and named primary/backup data operators | 2.6, 7.5 | Eng lead | *open* |
| D4 | Production ingestion host (ph-box vs new host; AWS target per reference §10) | 5C cut-over, 7.2 | Platform | *open* |
| D5 | Does any external system call `POST /api/v1/upload/chunked` or `/upload/news/article`? | 5E, 6 | Backend owner | *open* |
| D6 | Keep entity YAML aliases / `entity_admin` Streamlit app? Keep Mashrooi radar demo? | 5H, 6 | Data owner | *open* |
| D7 | Is the PF listings demo export (`demo_pf_listings`) used by anyone? | 6 | Product | *open* |
| D8 | News content rights (headline, summary, thumbnail mirroring) per publisher | 5E production enablement | Legal | *open* |

---

## Migration status

Update this table when a phase or pipeline changes state.

| Phase / pipeline | State | Date | Notes |
|------------------|-------|------|-------|
| Phase 0 audit | done | 2026-09-29 | This document set; `var/` dockerignore; DLD doc timezone fix |
| Phase 1 | partial (code ready) | 2026-10-03 | 1.2: census tool and local-snapshot results (#3); a live production census is still pending. 1.4: F13 fixed by repeatable rebuild (#5). 1.1, 1.3 and 1.5 not started (the `dagster-local` recipe binds loopback for local use only; Compose is unchanged). |
| Phase 2 | not started | | |
| P01 DLD pilot (Phase 3) | not started | 2026-10-03 | Precursor only: three date-column replace fixes (#4). F03 is not proven closed. |
| Phase 4 | not started | | |
| 5A | partial (code ready) | 2026-10-03 | #5: DDL, F13, serving views on the canonical layer. Reelly producers are missing. |
| 5C | partial (code ready) | 2026-10-03 | #5: evidence loader and ledger. Scraper, repair queue and cut-over are open; gated on D1 and D4. |
| 5B, 5D–5H | not started | | Canonical DXBI/DLD market layer (#5) is new work outside 5A–5H; its checks do not gate publication yet (Phase 2 S4.1). |
| Phase 6 | not started | | |
| Phase 7 | not started | | |

---

# AI Handoff

```text
Current architecture:
  Monorepo: backend/ (FastAPI, reads ClickHouse via ch_* views + direct bronze; Postgres via Alembic),
  frontend/ (SvelteKit SPA via typed API wrappers), holocron/ (Dagster ingestion: 18 source units,
  raw → R2 manifests → ClickHouse bronze → silver/geography). Significant data also flows OUTSIDE
  Dagster: DXBI rental/sales scrapes via root systemd timers on ph-box, published only through a
  developer Mac (launchd + rsync); backend scripts create serving views/facts manually. Details:
  docs/architecture/CURRENT_STATE.md, docs/data-pipelines/PIPELINE_INVENTORY.md.

Production standard:
  docs/data-pipelines/PIPELINE_STANDARDS.md. Core rules: one Dagster owner per table; atomic
  publish via staging → validate → EXCHANGE (publish_rebuild); business keys declared; asset checks
  (row floor, key uniqueness, required fields, freshness); retries with backoff+jitter incl. 429;
  QueuedRunCoordinator with per-table concurrency 1; alerts on failure; no secrets in run config;
  private Dagster UI; licensed sources only.

Completed migrations:
  None. Phase 0 (audit) complete. Safe fixes: holocron/.dockerignore excludes var/;
  holocron/holocron/sources/dld_open_data.md timezone corrected.

In-progress migrations:
  None.

Remaining migrations:
  Phase 1 (guardrails + live baseline), Phase 2 (shared infra), Phase 3 (DLD pilot), Phase 4
  (validate), Phase 5A–5H (serving contract, Reelly, DXBI evidence, PF, news, DDA, DXBI map,
  derived automation), Phase 6 (legacy removal), Phase 7 (hardening).

Current recommended next phase:
  Phase 1: tasks 1.1 (private Dagster UI), 1.3 (Holocron CI), 1.4 (fact builder guard) are
  small independent PRs; 1.2 (live baseline) needs an operator with read-only warehouse access.

Important constraints:
  - Follow AGENTS.md and holocron/AGENTS.md; use just recipes; surgical changes.
  - Do NOT enable Dagster schedules for dld_od_{projects,developers,buildings,units,lands,brokers,
    valuations} before Phase 3 lands (F03 duplicates).
  - Do NOT run `bronze_replay --initial-load` on production before Phase 3.6 (truncates first).
  - Do NOT run backend/scripts/clickhouse/build_fact_tables.py twice before Phase 1.4.
  - Do NOT schedule DXBI/PF/Reelly/Bayut in production until decision D1 is recorded.
  - Backend tests run migrations; only point them at disposable databases.
  - Treat production-readiness-2026-09-29.md as the platform reference; where it conflicts with
    code (news publication), code is the source of truth — see CURRENT_STATE.md §9.

Files that define the architecture:
  holocron/holocron/contracts.py                     source/table contracts
  holocron/holocron/sources/__init__.py              source registry
  holocron/holocron/orchestration/factory.py         assets, jobs, schedules
  holocron/holocron/orchestration/definitions.py     Dagster Definitions
  holocron/holocron/platform/{bronze,clickhouse,raw_files,manifests,checkpoints}.py   shared stages
  holocron/holocron/platform/{bronze_replay,dataset_versions}.py   recovery
  holocron/holocron/lib/sql/clickhouse_schema.sql    warehouse DDL (canonical)
  holocron/dagster.yaml, holocron/docker-compose.yml  runtime
  backend/scripts/clickhouse/create_compatibility_views.py   current serving views (to move, 5A)
  backend/app/clickhouse/client.py                   backend warehouse access
  holocron/deploy/systemd/, holocron/scripts/sync_dxbi_daily_runs.sh   off-Dagster DXBI path
  docs/ (this set) + production-readiness-2026-09-29.md

How to verify a phase:
  Each phase lists Acceptance criteria. Minimum for any holocron change: `just holocron check`
  (ruff format --check, ruff check, ty, pytest) + integration suite once Phase 2.1 exists.
```
