# Current State: Repository and Data Architecture

**Describes:** the repository as it exists at commit `71cf611` (branch
`docs/production-first-readiness`, 2026-09-29). This is a description, not a recommendation. For
the proposed future state see [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md). For platform-wide
launch topics (enterprise identity, tenancy, AI policy, cloud sizing, cost) the authoritative
reference is [production-readiness-2026-09-29.md](../../production-readiness-2026-09-29.md). This
document does not repeat it and focuses on **how data enters, moves through, and leaves the
system**.

## 1. Agent instructions discovered

| File | Scope | Key rules affecting data work |
|------|-------|-------------------------------|
| `AGENTS.md` (root) | Whole repo | 10 engineering rules (think first, simplicity, surgical changes, fail loud…); domain layering for backend/frontend; `just` is canonical; use `production-readiness-2026-09-29.md` as the production reference; Phase 1 = one enterprise / ~150 users |
| `holocron/AGENTS.md` | `holocron/` | One package per source (`source.py` + `extract.py`); no shared scraper registry; **one asset per durable ClickHouse table; data quality as asset checks**; generate assets from source metadata; ClickHouse DDL lives in `holocron/holocron/lib/sql/clickhouse_schema.sql`; Python 3.11, uv, ruff (100 cols), ty, pytest |
| `holocron/CLAUDE.md` | `holocron/` | Same layout rules; VPS workflow via `just env/up/deploy` |
| `backend/.venv/.../temporalio/.../AGENTS.md` | third-party dependency | Ignored (vendored package) |

## 2. Languages, frameworks, tooling

| Area | Technology | Evidence |
|------|------------|----------|
| Backend API | Python 3.13, FastAPI, SQLModel/SQLAlchemy async + asyncpg, Alembic, clickhouse-connect (async), Redis, Pydantic AI → OpenRouter, Stripe, Logfire (optional), Honeybadger (optional) | `backend/pyproject.toml`, `backend/app/main.py` |
| Frontend | SvelteKit 2 / Svelte 5, Vite, Bun, Tailwind, TanStack Query, MapLibre, Chart.js; browser-rendered (`ssr=false`) | `frontend/package.json`, `frontend/src/routes/+layout.ts` |
| Ingestion runtime | Python 3.11, Dagster 1.13.3 (+ dagster-postgres), httpx, Playwright/Chromium, boto3, clickhouse-connect 0.15.1, psycopg 3, 2captcha-python | `holocron/pyproject.toml`, `holocron/uv.lock` |
| Legacy scraping | Node.js scripts, bash/zsh supervisors | `holocron/scripts/*.js`, `*.sh` |
| Stores | PostgreSQL 16 (app + GoTrue + Dagster metadata), ClickHouse 24.8 (dev image), Redis 7, S3-compatible object storage (Cloudflare R2 in prod; MinIO in dev) | `docker-compose*.yml`, `holocron/docker-compose.yml` |
| Auth | Self-hosted Supabase GoTrue | `docker-compose.yml` |
| Tests | pytest (backend + Holocron), Vitest (frontend), Playwright (`e2e/`) | — |
| Command interface | `just` (root `justfile`, `holocron/justfile`) | — |
| CI/CD | GitHub Actions: `docker-publish.yml` (backend image → ph-box over SSH on push to `production`), `ai-evaluation.yml` (backend evaluation subset on PR). **No Holocron CI.** | `.github/workflows/` |
| Secrets | Bitwarden Secrets Manager → `.env` files (`scripts/pull-bitwarden-env.sh`, `holocron/scripts/pull-bitwarden-env.sh`) | — |

## 3. Component map

```text
                         ┌──────────────────────── External sources ────────────────────────┐
                         │ DLD open-data API · DLD Mashrooi · Dubai Pulse files · DXB Interact │
                         │ Property Finder · Reelly/Xano · DDA ArcGIS/DIS · RSS feeds · Bayut  │
                         └───────┬──────────────────────────────┬───────────────────────────┘
                                 │ Dagster runs                 │ systemd timers (root) on ph-box
                                 ▼                              ▼
   ┌───────────── Holocron (Dagster: webserver + daemon, own Postgres) ─────┐   ┌── dxbi_rental_backfill.py ──┐
   │ sources/<source>/extract.py → factory._run_raw_extractor              │   │ run folders on host disk     │
   │   → R2 raw/source=…/  + manifests/ + state/ checkpoints               │   └──────────────┬───────────────┘
   │ sources/<source>/bronze.py → ClickHouse *_bronze (delete+insert or    │                  │ rsync (laptop pulls)
   │   EXCHANGE)                                                           │                  ▼
   │ silver/, assets/geography.py → silver_*, geo_*, offplan_*             │   ┌─ developer Mac (launchd 09:00) ─┐
   │ news_publish → PostgreSQL news_articles (manual job)                  │   │ sync_dld / sync_silver / DXBI    │
   └───────────────┬─────────────────────────────────────┬────────────────┘   │ loaders → Mac ClickHouse; CRM    │
                   │                                     │                    │ refresh → Mac Postgres; Redis    │
                   ▼                                     ▼                    └──────────────────────────────────┘
          ClickHouse (ph-box host)              PostgreSQL (app)
          bronze / silver / geo / dxbi_*        news_articles, CRM, users, orgs,
          + backend-created ch_* views/facts    chats, studies, billing, usage
                   │                                     │
                   └────────────┬────────────────────────┘
                                ▼
            FastAPI backend (Gunicorn 2 workers, ph-box container)
            domains: analytics, area, chat, crm, feasibility, filters, listings, news,
            organization, project, supply, transaction, upload, billing, admin
            Redis cache (@cached_endpoint) · OpenRouter LLM tools over ClickHouse
                                ▼
            SvelteKit SPA (typed API wrappers in frontend/src/lib/api, TanStack queries)
```

## 4. Data sources and pipelines

The canonical list with IDs, triggers, storage and status is
[PIPELINE_INVENTORY.md](../data-pipelines/PIPELINE_INVENTORY.md) (24 entries).

- **Dagster-managed sources (18 registered units):** 9 DLD open-data endpoints, DLD Pulse historic, DLD Mashrooi, DXBI (map), PF locations, PF listings, Reelly supply, DDA planning layers, news feeds, Bayut. Registry: `holocron/holocron/sources/__init__.py`.
- **Dagster-managed derived assets:** PF media, Reelly documents and project media, news thumbnails, news publish, silver (2), geography (5), Pulse bronze.
- **Outside Dagster:**
  - DXBI rental/sales report scrapes and the repair queue (systemd on ph-box);
  - the local mirror and DXBI loading (launchd on a developer Mac);
  - legacy Node backfills;
  - backend scripts for serving views/facts, demo imports and CRM refresh;
  - backend HTTP ingress (`/api/v1/upload/chunked`, `/api/v1/upload/news/article`);
  - user spreadsheet uploads (CRM).
- **Unknown producers:** `silver_reelly_projects`, `reelly_project_unit_types_bronze`, and extra columns on `reelly_projects_bronze` are read by the backend but created by nothing in the repo.

## 5. Holocron internals (the ingestion platform)

| Concern | Implementation |
|---------|----------------|
| Source contract | `SourceSpec(name, provider, extractor, bronze_loader, bronze_tables, checks, checkpoint_strategy, cadence, description)`, `holocron/holocron/contracts.py`. `checks`, `checkpoint_strategy` and `cadence` are informational; nothing reads `checks`. |
| Registry | `SourceRegistry` validates non-blank names and unique table names (`sources/registry.py`) |
| Asset generation | `build_assets_for_source` → `<source>_raw` (+ `<source>_bronze` if tables declared), `orchestration/factory.py` |
| Raw release | Extractor writes files into a temp dir and returns a `RawRelease`. The factory uploads each file to `raw/source=<s>/extract_date=<d>/run_id=<r>/<file>`, then writes `raw/source=<s>/manifests/<run_id>.json` (sha256, size, row_count, metadata with redacted config), then checkpoints and `_state_updates` |
| Checkpoints | `state/source=<s>/operation=<op>.json` (`platform/checkpoints.py`). Source-specific resolvers in `sources/*/orchestration.py` and `dld_open_data_orchestration.py` |
| Bronze | Per-source `load_release_to_clickhouse(raw_manifest, s3, clickhouse)`. Helpers in `platform/bronze.py`. Every load runs `ensure_schema` (full DDL file) |
| Replay / versions | `platform/bronze_replay.py` (CLI; `--initial-load` truncates then replays; `--dataset-version` uses ordered descriptors from `platform/dataset_versions.py`; writes `bronze_load_ledger` only in dataset-version mode) |
| Hydration | `platform/hydration.py` + `http_download.py` (allowlist, size cap, redirect re-validation, inventory manifests) |
| Settings | `platform/settings.py` (`OBJECT_STORAGE_*`, `CLICKHOUSE_*`, `PLATFORM_POSTGRES_URL`, `NEWS_*`). Source credentials read directly from env in extractors (`REELLY_*`, `TWOCAPTCHA_API_KEY`/`CAPTCHA_API_KEY`) |
| Resources | `ObjectStorageResource`, `ClickHouseResource` construct a new client per call (`platform/resources.py`) |
| Schedules | 18 schedules, all `STOPPED` by default, Asia/Dubai (`factory.py:877-904`) |
| Instance | `holocron/dagster.yaml`: Postgres storage, `DefaultRunCoordinator`, `DefaultRunLauncher`. `holocron/docker-compose.yml`: webserver on `0.0.0.0:3000`, daemon, local `postgres-dagster` volume |
| Ops tooling | `platform/doctor.py` (env and connection checks), `run_locked.py`, `sync_*` (local mirror), `apps/entity_admin/app.py` (Streamlit) |

## 6. Storage layout

### Object storage (R2, bucket from `OBJECT_STORAGE_BUCKET`)
```text
raw/source=<source>/extract_date=<YYYY-MM-DD>/run_id=<run_id>/<files>   immutable raw
raw/source=<source>/manifests/<run_id>.json                           release manifest
raw/source=<source>/dataset_versions/<version>.json | latest_complete.json
raw/source=<source>/<kind>_asset_manifests/…                          media inventories
state/source=<source>/operation=<op>.json                             checkpoints / delta state
media/{pf_listings,reelly_supply,news}/…                              mirrored media
etl/uploads/<session>/chunk_* , metadata.json                         backend chunk ingress (P16)
```

### ClickHouse
The central schema is `holocron/holocron/lib/sql/clickhouse_schema.sql` (1,724 lines, 53 tables
plus views). Groups:

- `dld_od_*_bronze` (9)
- `dda_*` (11)
- `dxbi_*` (map bronze + rental/sales evidence)
- `pf_*`
- `reelly_*`
- `bronze_load_ledger`
- `silver_*`
- `geo_*`, `offplan_*` (including manual override tables)
- views such as `geo_mapped_source_facts`

Other properties:

- No `PARTITION BY` or `TTL`.
- Backend-owned views (`ch_*`) and facts are created by `backend/scripts/clickhouse/`, not by this file.
- The DXBI loader scripts also embed their own DDL.

### PostgreSQL (application)
Managed by Alembic (`backend/migrations/versions/`, 11 revisions). `news_articles` is
**also** altered at runtime by Holocron (`news_feeds/assets.py::_ensure_news_articles_schema`).

## 7. How the backend consumes pipeline data

- ClickHouse access goes through a global async client with 10 s connect, 300 s receive, 2 retries (`backend/app/clickhouse/client.py`). Queries use server-side parameter binding.
- The read surface uses `ch_*` compatibility views over **bronze** (`ch_transactions` is the most-referenced object, 58 references), plus direct reads of `pf_listings_bronze`, `reelly_projects_bronze`, `dxbi_*` (CRM), `geo_*`, `dda_*`, `silver_reelly_projects`.
- `silver_transactions` and `silver_rent_contracts` are **not** read by the backend.
- Caching: `@cached_endpoint` stores JSON in Redis with a TTL (default 600 s) (`backend/app/core/decorators.py`). Prefixes are invalidated only by the Mac sync's `invalidate_prefixes`.
- Freshness shown to users: e.g. listings `max(scraped_at)` (`backend/app/listings/queries.py:66`), supply `max(scraped_at)` (`backend/app/supply/queries.py:81`).
- LLM/chat: Pydantic AI agents call predefined ClickHouse query tools (no arbitrary SQL). CRM uses direct OpenRouter calls for header mapping and enquiry extraction. See the reference doc §11.
- Frontend: calls typed wrappers (`frontend/src/lib/api/*.ts`) and TanStack query builders (`frontend/src/lib/queries/*.ts`). It never reads stores directly.

## 8. Deployment model (as coded)

| Component | Where it runs | How it is deployed |
|-----------|---------------|--------------------|
| Backend API, GoTrue, Redis | ph-box (Docker Compose, `docker-compose.yml`) | GitHub Actions on push to `production`: build + cosign sign → SSH → `git pull`, `alembic upgrade head`, `compose up` (`.github/workflows/docker-publish.yml`) |
| PostgreSQL, ClickHouse | ph-box host services | Manual |
| Holocron Dagster | "VPS" (`holocron/docker-compose.yml`); whether this is ph-box or a separate host is **UNKNOWN** | `just deploy` (git pull + rebuild) on the host |
| DXBI report scrapers | ph-box systemd, `User=root`, paths under `/root/repos/vitevue-platform` and `/root/data` | Manual install of units from `holocron/deploy/systemd/` |
| Market-data mirror | Developer Mac launchd | Manual install of `holocron/deploy/launchd/com.vitevue.dxbi-sync.plist` |
| Frontend | Not deployed by CI; `adapter-auto` | Reference §3 |
| Local development | Host processes + `docker-compose.dev.yml` (Postgres, GoTrue, Redis, ClickHouse 24.8 + schema init from the Holocron SQL file, MinIO, Dagster) | `just up`, `just dev` |

The target deployment (AWS me-central-1, ECS, RDS, ClickHouse Cloud, S3, separate ingestion host)
is proposed in the reference doc §1 and §4. It is **not** implemented.

## 9. Known documentation drift

| Document says | Code does | Resolution |
|---------------|-----------|------------|
| `production-readiness-2026-09-29.md` §10: "News … every 15 minutes … existing `news_feeds_poll_and_hydrate`" publishes to PostgreSQL | The scheduled job polls and hydrates only; publication is the manual `news_publish_backfill` (`factory.py:799-826`) | Code is truth; fix in Phase 5E, then correct the reference |
| `production-readiness-2026-09-29.md` §2: "this directory has no `.git`" | Repository now has git history (`71cf611`) | Reference predates the import; no action |
| `holocron/ARCHITECTURE.md`: "Silver/domain layers are intentionally deferred" | Silver and geography assets and schedules exist | Update the architecture doc when Phase 5F lands |
| `holocron/README.md`: "Dagster run/event/schedule metadata lives in external Postgres"; "use `docker-compose.local.yml`" | Compose uses a local `postgres-dagster` volume; `docker-compose.local.yml` does not exist | Phase 1 doc fix |
| `holocron/sources/dld_open_data.md` said schedules run in UTC | `execution_timezone="Asia/Dubai"` | **Fixed in this audit** |
| `holocron/sources/dld_open_data.md`: "Bronze loaders reject incomplete DLD raw chunks" | Only incremental releases are rejected; incomplete snapshot/backfill chunks are loadable (`dld_open_data_bronze.py:205-216`) | Clarify in Phase 3 |
| Root `justfile` `load-clickhouse-local` | Calls `backend/scripts/data/insert/load_local_clickhouse.py`, which does not exist | Phase 6 cleanup |
| `development.md` says hosts use `.env.dev` | `just dev-*` sources `.env` (reference §3) | Owned by the reference doc |

## 10. Pipeline boundaries (where one pipeline ends and another begins)

1. **Raw boundary:** the R2 manifest. Everything before it is source-specific and discardable. Everything after it is replayable.
2. **Bronze boundary:** ClickHouse `*_bronze` tables. These are source-shaped, one per durable table, owned by a source package.
3. **Derived boundary:** `silver_*`, `geo_*`, `offplan_*`, `ch_*` facts. Cross-source, owned by `holocron/holocron/{silver,assets,domain}` plus backend scripts (split ownership).
4. **Serving boundary:** `ch_*` views and direct bronze reads by `backend/app/*/queries.py`. The contract is implicit; there is no test that it exists on a fresh warehouse.
5. **Application boundary:** PostgreSQL `news_articles` and CRM claim tables written by pipelines, read by the API.
