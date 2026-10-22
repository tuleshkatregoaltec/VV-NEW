# VV production readiness and chatbot implementation prompts

Prepared on 2 October 2026 for the canonical repository `/home/maan3/vv/VV-git` (Windows: `\\wsl$\Ubuntu\home\maan3\vv\VV-git`). This document validates the supplied progress summary and provides ordered prompts for Claude Opus 5.5. It plans implementation; it does not claim the platform is ready to launch.

The priorities are reliable and efficient data pipelines, a database-grounded MCP chatbot, and Telegram access through the same authorized user account and chat runtime. Complete the work as small reviewed PRs, with evidence for each acceptance criterion. Existing architecture and domain boundaries remain the starting point.

## Verified state and corrections

| Item | Evidence in the canonical checkout | Assessment |
| --- | --- | --- |
| Backend domain migration | Git history includes PR 1 merge `5ae9767`, domain commits, `backend/ARCHITECTURE.md`, and architecture tests | Committed. Preserve these boundaries when adding tools and channels. |
| Production architecture | Commit `71cf611`, `production-readiness-2026-09-29.md`, root instructions | Committed architecture recommendation. AWS UAE, ECS/Fargate, RDS, ClickHouse Cloud, S3, Cognito and a separate ingestion host are the agreed target. Procurement and provisioning remain outstanding. |
| Evaluation pipeline | Commits `dc90446`, `81dfef2`, `f4bdf23`, `9dc7b9e`, `76b08bd`; `backend/scripts/evaluation`; `backend/EVALUATION.md`; `.github/workflows/ai-evaluation.yml` | Implemented and committed. Reuse it. Remote LangSmith ingestion is documented as unverified without credentials. |
| Pipeline audit and migration docs | `docs/architecture`, `docs/audits`, `docs/data-pipelines`, `docs/migration` | Present but untracked at inspection. The phase/status tables still say work that now exists has not started. |
| DXBI canonical sales and rentals | Untracked `holocron/holocron/market`, evidence loader/assets/tests; changed serving views/schema/fact builder | Implemented in the working tree. This is local preproduction progress, not proof of production cutover. |
| Fact builder F13 | `_materialize_fact` preserves the source view and rebuilds from it; three targeted tests pass | Fixed in uncommitted code. Prefer this implementation over the older migration plan's proposal to merely refuse a second run. Still verify changed input on a real database. |
| Warehouse DDL | Added Reelly and active DXBI definitions; schema tests | Progress toward reproducibility. Defining missing tables does not establish that all their producers or required fields exist. |
| Deduplication and latency | `DXBI_RECONCILIATION.md` section 10 records zero duplicate sale IDs and local measurements | Historical recorded evidence, not remeasured here. Correct latency attribution: rentals 609 to 64 ms; sales 200 to 66 ms. Avoid treating these single measurements as p95 load results. |
| Bayut | Registered `bayut_listings`, bounded collector, full-collection planner/checkpoints/tests, manual cadence; audit P17 | Experimental collection exists. A validated production ingestion-to-serving pipeline, ongoing freshness and approved access are still missing. Do not tell another agent to build a scraper from zero. |
| Chat database access | Three tools in `backend/app/chat/tools.py`, calling public analytics/project services | Existing database tools are local Pydantic AI functions. They are not an implemented MCP client/server flow. The dependency on FastMCP alone does not establish integration. |
| Chat question coverage | Fifteen exact canonical questions in `backend/scripts/evaluation/benchmark.json` | Current general chat tools reach transactions only. Rent, listings, delivery and combined-report requests need additional public capabilities and tools. |
| Evaluation quality | `backend/EVALUATION.md` repeat-run findings | Successful streams did not establish factual correctness. All fifteen full requests were documented as unsupported by the current tool contract; three require entity bindings. Assisted review flagged thirteen grounding failures; numerical correctness remained unassessed. |
| Chat organization isolation | `backend/app/chat/repository.py` filters get/list by user only | Current-organization predicates remain missing. Also inspect delete/message/job paths, not only router guards. |
| Canonical publication checks | `market/transforms.py::_rebuild` exchanges staging before asset checks in `market/assets.py` | New finding: the 22 checks currently observe already-published data. Validation must reject bad staging data before promotion. Fixed staging names and immediate disposal of the old table also require concurrency/recovery review. |
| Source licensing and operator decisions | Migration decision register D1 through D8 | Still open in the available documents. Mock development can proceed; production enablement cannot imply these decisions were resolved. |
| Branch selection | Current branch `docs/production-first-readiness`; local `origin/HEAD` points at `origin/kian-new`, while the summary names `origin/VV` | Do not assume the PR base is `main` or trust an old symbolic ref without checking the intended integration branch. |

**Update after Prompt 00 (3 October 2026).** The uncommitted work is now preserved as draft PRs against `VV`. PRs #1 and #2 merged into `VV`. The remote default `kian-new` is a May snapshot 117 commits behind, so `VV` is the verified base.

| PR | Scope | Base |
| --- | --- | --- |
| #6 | (a) pipeline docs with corrected status, this roadmap, evaluation walkthrough | `VV` |
| #3 | (b) census tooling/tests and sanitized health summaries | `VV` |
| #5 | (c) canonical market layer, DXBI evidence loader, schema/serving, F13 fact rebuild | stacked on #3 |
| #4 | DDA, Property Finder and DLD date-column fixes (outside scopes a–c) | `VV` |

Re-run results:
- Holocron: 497 passed on the combined tree. Lint passes. Formatting fails on 14 files and `ty` reports 62 diagnostics, both identical on untouched `VV`.
- Backend: 323 passed, with 3 skips because Bun is unavailable.

Prompt 01 owns the formatting, type and Bun baseline. `docs/bitwarden-platform.env` holds only blank values with placeholder comments, in its single historical commit `6c11eef` and now, so no secret remediation was needed.

The source-only `/home/maan3/vv/VV` snapshot was older and lacked Git metadata and the evaluation changes. It was removed at the user's request. Its 126 differing or old-only files plus two files from the redundant env-backup folder were hash-verified in a private archive under `.local-backups/2026-10-02-repo-consolidation/`. The archive is excluded through `.git/info/exclude`; it must never be committed. The 14 GB `/home/maan3/vv/handoff` was retained because it contains database dumps, warehouse backups and objects. The canonical working tree's existing changes were unchanged by cleanup.

**Update after the data-layer work (3 October 2026).** Code ready in draft PRs; nothing deployed.

| PR | Scope | Base | Prompt coverage |
| --- | --- | --- | --- |
| #9 | Chat repository scoped to user and organization; models aligned with the migrated schema (BIGINT CRM amounts, migration-owned indexes) with a drift test | `VV` | 02 (conversation column already fixed by `a34d19c0b621`), 03 chat part |
| #10 | Validate the staged canonical release before publishing; keep the previous release | #5 | 06 for the market layer |
| #11 | Shared sales/rental metrics with Dubai calendar periods and coverage, delivery summary, listing inventory tool; pulse/ranking/summary semantics corrected; `ch_projects` deduplicated | #5 | 19A/19B/19D in part, 19C q09; no MCP (18 open) |

The "Canonical publication checks" and "Chat organization isolation" rows above are addressed by #10 and #9.
Benchmark coverage per question, with data versus functionality gaps, is in `backend/EVALUATION.md` on #11.

## Checks executed for this review

| Check | Result on 2 October 2026 |
| --- | --- |
| `just --list` and Holocron recipe listing | Available after adding `/home/maan3/.local/bin` to this shell's PATH |
| `just holocron test` | 497 passed; one pytest collection warning in the standalone Bayut handoff |
| Holocron `uv run ruff check` | Passed |
| `just holocron check` | Failed at formatting: 14 files would be reformatted; later stages did not run in this invocation |
| `just holocron typecheck`, run separately | Failed with 62 diagnostics, including missing optional admin dependencies, handoff tests and runtime typing problems |
| Targeted backend fact-builder/evaluation/architecture/agent/streaming suite | 59 passed, zero skips |
| `just test-backend` | 323 passed, 3 skipped because Bun was unavailable for engine parity tests |
| `just ai-inventory`, `just ai-benchmark` | Completed; generated JSON validated; no model requests or telemetry export |
| Existing tracked diff whitespace | `git diff --check` passed |

No live warehouse census, fresh-database integration, browser suite, model evaluation, remote LangSmith upload, cloud deployment or restore drill was executed in this review. Docker was unavailable through this WSL shell. Historical checks in `DXBI_RECONCILIATION.md` are identified separately from today's checks. The current fast backend test recipe does **not** migrate the application's database; integration fixtures create isolated databases. This supersedes older guidance that all backend tests run application migrations.

## Decisions and completion gates

Keep migration decisions D1 through D8 and add the following decisions to the same maintained register instead of creating competing lists:

- Confirm the intended integration/release branch and PR base.
- Confirm customer identity provider, enterprise entitlement policy and deployment domain ownership.
- Agree which sources are required at launch and acceptable coverage if Bayut or another source is unavailable.
- Agree chatbot allowed/prohibited topics, sensitive fields, export/report permissions, acceptable uncertainty and examples. The user explicitly wants to decide specific restrictions together; they are pending, not already approved. Enforce authorization and secret protection immediately while keeping product-topic policy explicit and configurable.
- Confirm whether Telegram may carry private CRM/document data under the customer's approved processing policy. It introduces an additional external delivery channel relative to the regional architecture. Start with approved market-data responses and keep restricted capabilities disabled until decided.
- Agree registered-phone verification, account recovery and phone-change policy. A CRM contact/property owner's phone must never be treated as an application login identity.
- Confirm the runtime model/provider, supported fine-tuning method, permitted training data, residency/retention and a paid-training/live-evaluation budget. The coding model named by the user does not determine the runtime chatbot model.
- Choose reference answers and numeric tolerances with a property-domain reviewer, freeze benchmark data/date context and assign an evaluation owner.

Three completion gates apply:

1. **Code ready:** coherent commits/PRs, passing required offline and disposable-service checks, deployment artifacts and configuration templates prepared. Missing external credentials are recorded.
2. **Staging accepted:** real identity, licensed sources, MCP transport, provider calls, Telegram webhook, production-style browser flows, load/failure tests and restore/rollback drills verified with staging credentials.
3. **Launch accepted:** production provisioning, approved sources/channels/models, customer acceptance and the production-domain smoke tests complete. Credentials alone do not automatically satisfy this gate.

The architecture reference proposes Phase 1 targets: 150 users, 30–50 active browser sessions, 10–20 simultaneous AI turns, 20–30 HTTP requests/second in burst tests, CRUD p95 below 1 second, warm common analytics p95 below 3 seconds, daily market freshness within 36 hours and news within 60 minutes. These are proposed acceptance targets to confirm and measure, not current guarantees. Preserve the migration plan's 14-day DLD staging observation period unless an authorized owner explicitly revises it.

## How to use the prompts

Paste the common contract followed by **one** numbered prompt into Claude. Use a new Claude session for each PR, or continue with the prior PR's verified handoff. Do not ask an agent to execute the entire document as one giant PR. Where a step is a repeatable template or explicitly lists separate PRs, run it once per named scope.

Finish and review prerequisites before dependent work. Merge through the normal review policy; never auto-merge merely because a prompt says to create a PR. Credential-dependent steps remain pending while independent implementation continues. Do not recreate features already implemented in a previous PR.

### Common contract to paste with every prompt

```text
Work only in /home/maan3/vv/VV-git. Read AGENTS.md, applicable nested instructions, development.md, backend/ARCHITECTURE.md and the relevant sections of production-readiness-2026-09-29.md. Read exports, public operations, immediate callers and tests before editing. For pipeline changes also read holocron/AGENTS.md and the pipeline standards/audit/migration docs. Preserve domain ownership and acyclic imports.

Inspect git status, existing staged changes, current branch, remotes and intended PR base. Never reset, clean, stash, overwrite or indiscriminately stage someone else's work. For existing uncommitted work, identify its scope and preserve it first. After that baseline is protected, use one descriptive feature branch per PR. Verify the remote's current integration branch; do not assume main, VV or kian-new from stale docs. Carry dependencies explicitly if using stacked PRs.

State assumptions, scoped changes and concrete acceptance criteria before implementation. Keep changes surgical, production-safe and sized for one enterprise with about 150 users. Use existing public domain services; routers, chat tools and channel adapters must not acquire business SQL or bypass authorization. No new orchestration stack, unrestricted SQL agent, vector database or GPU deployment unless separately agreed.

Use just --list and the documented recipes. Run focused tests, relevant architecture/contracts checks and the necessary real disposable-service integrations. Fix failures within this PR's scope. Do not weaken checks, hide skips, rewrite business expectations to accept defects, or format unrelated files. Regenerate the API client and update typed wrappers/query builders when API schemas change. Browser/guarded-flow changes require appropriate Playwright checks.

Keep secrets, local backups, production data, raw traces, training data and browser session state out of Git, images and public CI. Do not enable unapproved provider schedules. Do not run migrations, replay, destructive cleanup or cloud apply against production as part of a code PR. Work that requires a recorded business decision stays disabled until the relevant answer exists.

Implement and verify the requested scope, then create short Conventional Commit commits containing only its files. Push the feature branch and create a PR against the verified base if GitHub access permits. Use a body file for multiline gh descriptions. The PR must state the problem/result, dependencies, checks and skips, migration/backfill/rollout/rollback needs, and remaining launch gates. Do not merge or deploy automatically. If push or PR creation is blocked, keep the commits and provide the exact branch, commit SHAs and ready-to-use PR text; never invent a PR URL.

Update the existing migration/inventory status for work actually implemented; distinguish code ready from staging accepted and production enabled. Finish with changed files, commit SHAs, PR URL or exact blocker, test evidence, remaining decisions and the next ready prompt. Persist through ordinary implementation/debugging; do not stop after a plan when the task is executable.
```

### Prompt 00 Preserve current work and reconcile stale status

Run before creating dependent feature branches. This step produces separate preservation PRs; it does not silently adopt all files into one commit.

```text
Protect and validate the current uncommitted work in VV-git. Review git diff and every untracked file before staging. Verify the supplied summary against implementation and DXBI_RECONCILIATION section 10.

Create three coherent preservation scopes: (a) pipeline architecture/audit/standards/migration docs, including corrected status and this roadmap; (b) census tooling/tests and sanitized health summaries; (c) canonical market sales/rentals, DXBI evidence loading, warehouse schema/serving integration and repeatable fact-builder tests. Identify cross-scope dependencies and use explicit stacked PRs if necessary. Review AI_EVALUATION_WALKTHROUGH.md against EVALUATION.md and either include a useful consistent walkthrough or remove only confirmed redundant content with an explanation. Inspect docs/bitwarden-platform.env without printing values; verify Git history/current contents contain no real secrets, and remediate any confirmed exposure through the established secret process.

Record that F13 is fixed by repeatable rebuild, 5C has an evidence-loading asset but still has external scraper/host/licensing dependencies, and 5A is partial while Reelly producers remain missing. Keep historical audit findings but annotate their current status. Do not claim the canonical 22 checks gate publication yet.

Run just holocron check and the relevant backend checks. Record the known format/type failures and three Bun skips honestly; do not discard working changes or call the branch fully green. Commit reviewed baseline scopes, marking blocked checks explicitly in draft PRs until Prompt 01 makes required gates green. Acceptance: no important work remains unprotected solely as untracked/dirty files, the status docs match code, and each preservation PR is identifiable and reviewable.
```

### Prompt 01 Establish a green and reproducible CI baseline

```text
Close the baseline verification gaps without changing pipeline business behavior. Reproduce the current 14-file Holocron format failure and 62 type diagnostics. Separate runtime defects from optional admin dependencies and archived handoff scope. Fix types surgically; make the documented admin-extra and typecheck scope intentional instead of silencing diagnostics wholesale. If handoff copies are archived, preserve required material privately and keep authoritative Bayut package tests running. Review formatting-only changes in a separate commit.

Add Holocron CI using locked dependencies and supported Python, and extend existing CI to required backend, frontend and isolated integration checks. Reuse ai-evaluation.yml; do not rebuild evaluation infrastructure. Add non-mutating frontend type/lint and CI recipes where missing. Keep live model execution and raw evidence uploads opt-in. Ensure engine parity has Bun available so it cannot silently skip. Correct .env versus .env.dev developer recipe drift while preserving production config precedence.

Acceptance: just holocron check passes, relevant backend/architecture/contracts/evaluation checks pass, required frontend checks run, generated drift is detected, and deliberately broken code makes CI red. Record branch-protection changes that need repo-admin access separately from implemented workflow code. PR scope: reproducible checks and necessary baseline fixes only.
```

### Prompt 02 Fix PostgreSQL migration and model contracts

```text
Audit the current Alembic graph and model metadata on an empty disposable PostgreSQL database and an upgrade fixture. First reproduce the conversation organization_id schema mismatch; confirm whether a later migration already fixed it before adding anything.

Repair confirmed drift with forward migrations. Preserve existing revisions. Define safe backfill behavior for legacy conversations: assign an organization only when historical ownership can be established; quarantine unresolved rows rather than guessing current membership. Use expand/backfill/validate/constraint stages when needed. Cover required columns, constraints, indexes and persisted JSON contracts; inspect news schema drift without mixing its full single-writer migration into this PR.

Acceptance: fresh upgrade reaches one expected head, upgrade preserves legacy data, model contracts pass using migration-created schema rather than create_all, and create/read/update/delete chat works against the migrated schema. Explain unresolved-row handling and rolling-version compatibility. Never apply this migration to the application's current data as a test.
```

### Prompt 03 Enforce organization and user authorization everywhere

```text
Fix the chat tenant boundary using trusted AuthContext and the existing repository/service layers. Require current organization_id and user_id for list/get/update/delete/messages/stream/artifacts. Review repository delete ordering so unauthorized operations cannot delete messages before an authorized conversation predicate is enforced. Do not rely on the router alone.

Create a negative access matrix covering two organizations, two users, transfer to another organization, disabled membership, stale sessions, forged resource IDs and owner/member permissions. Extend the same trusted-context contract to public operations that future MCP and Telegram adapters will call. Review caches, exports, private studies and CRM access for confirmed boundary gaps; keep unrelated fixes as named follow-up PRs rather than widening this change invisibly.

Acceptance: a transferred user cannot retrieve, change, stream or delete old-organization chats; failed access has no side effects; shared market data and private tenant data remain governed by explicit capabilities. Run real isolated PostgreSQL authorization tests and relevant guard/browser tests. Update API/generated contracts if signatures or responses change.
```

### Prompt 04 Make ingestion operational surfaces private

```text
Implement migration Phase 1.1: bind the current Dagster host port to loopback and document authenticated private access/tunneling. Inspect GraphQL and admin surfaces too. For the AWS target, define private access/security-group expectations without inventing a new auth stack or publishing port 3000.

Remove insecure production defaults and host-specific access assumptions only where this change needs them. Keep local operation usable. Add configuration verification and a smoke procedure proving access through the approved private path and rejection from outside the host/network.

Acceptance: repository configuration no longer exposes the UI/GraphQL publicly. Label runtime verification pending until an operator can apply and test it on the actual ingestion host. Record the source allow/disable configuration so unapproved schedules stay stopped. Do not claim changing Compose alone has secured an already-running remote host.
```

### Prompt 05 Make the warehouse serving contract reproducible

```text
Implement migration Phase 2.1 and the remaining serving-contract work from 5A, preserving the already-built canonical market layer. Add an isolated real ClickHouse test harness that installs repository-owned schema, serving views and required derived definitions. Trace backend public queries to every required table/column. Replace divergent integration-fixture DDL with the canonical definitions where practical.

Introduce a small versioned warehouse migration mechanism with checksums and serialized application. Separate migrator, writer and API-reader privileges; do not grant runtime readers DDL merely to pass startup. Remove runtime ensure_schema progressively as writers migrate.

For missing Reelly asset/payment-plan or other producers, document the exact consumer and field gap; implement a verified producer in a dedicated provider PR or expose honest unavailability. An empty table is not data completeness. Move serving/fact ownership into Holocron through a compatible transition, retaining existing scripts until replacement parity is verified.

Acceptance: a fresh warehouse serves the required backend queries with no xfail masking missing objects, rerunning schema setup is safe, API-reader DDL is rejected, and a changed-input fact rebuild updates results twice. Record schema/backfill and rollback compatibility.
```

### Prompt 06 Validate before publishing and retain recovery state

```text
Implement the smallest shared staging-to-validation-to-promotion primitive required by PIPELINE_STANDARDS S4.1. Reuse existing ClickHouse helpers; inspect all immediate callers before changing their contract. Apply it first to the canonical market layer, where _rebuild currently exchanges before the Dagster checks run.

Use unique run staging names, declared business keys, serialized table writers, required-field/row-floor/completeness checks, conservation/uniqueness checks and a durable publication ledger. Evaluate blocking checks against staged tables before any serving swap. Retain a previous good release according to a documented retention policy. Handle crashes around swap and ledger updates by reconciliation; do not pretend independent ClickHouse table swaps and ledger writes form one transaction. Preserve a consistent release across dependent sales/rental/match/fact outputs using a measured, minimal release strategy.

Acceptance on real ClickHouse: invalid/partial staging cannot change served counts, repeated releases do not duplicate data, concurrent attempts cannot collide, readers never see a delete-and-insert gap, and a crash before/after promotion recovers an identifiable release. Last-good release survives failed builds and recovery does not truncate live tables first. Add Dagster dependency/concurrency configuration, not clock offsets masquerading as dependencies.
```

### Prompt 07 Add retries and provider-wide throttling

```text
Implement the shared retry/throttle/run-control portions of migration Phase 2. Read existing clients first and apply a small reusable policy only at actual outbound-call boundaries. Separate outbound provider quotas from inbound application/chat rate limiting.

Handle transient network failures, timeouts, 429 Retry-After (both date and seconds forms) and retryable 5xx with bounded backoff/jitter and an overall deadline. Do not retry authorization/parse/permanent failures blindly. Enforce provider/account-wide request and concurrency limits across runs sharing credentials, with conservative configurable defaults until D1/D2 quotas are recorded. Reuse durable state and Dagster controls; add coordination only where more than one process actually shares a quota.

Make pagination checkpoints durable, count retries toward budgets and expose requests/rows/bytes/retry/429/run-lag metrics. Stop on access denial/challenge rather than optimizing by evasion. Acceptance: deterministic fake-clock tests prove quota enforcement across concurrent runs, retries honor provider feedback, exhausted retries preserve checkpoints, and no path can loop forever. Record throughput measurement methodology and pending provider-specific quota inputs.
```

### Prompt 08 Fix DLD overlapping-window duplication and outage recovery

```text
Implement the DLD pilot for all nine open-data units using the publication, ledger and retry contracts. Reproduce F03 against real ClickHouse with sliding overlapping windows and reordered pages. Review the three uncommitted date-column fixes already landed; do not assume they solve every unit.

Declare provider business identity separately from run IDs, row offsets and window metadata. Define deterministic handling of corrections/versions and compound keys, especially transactions and rental lines; preserve historic Pulse records according to the canonical cutover. Resume from the last successful watermark with controlled overlap and catch-up after outages longer than the current lookback. Distinguish legitimate corrected records from exact duplicates. Capped/incomplete runs must not advance a complete watermark or publish a full snapshot.

Acceptance: two reruns and overlapping/reordered/backfill windows preserve exact business cardinality, correction values update correctly, a seven-day outage is recovered without missing dates, failed extraction leaves last-good serving intact, and retry after uncertain insertion does not duplicate results. Keep the affected production schedules disabled until integration acceptance and source approval. Update P01/current F03 status with evidence.
```

### Prompt 09 Prove the DLD pattern and freshness operation

```text
Execute migration Phase 4 in staging before migrating every provider. Build scripts/reporting for the required 14-day observation period; with missing credentials, deliver executable checks and leave the observation gate pending. Do not compress fourteen days into a unit test.

Measure source coverage, duplicate rates, lag, row completeness, throughput, memory, scan bytes and publication/API latency under concurrent reads. Exercise 429, changed schema, interrupted pages and missed schedules. Add failure/check/freshness alerts and user-visible stale-data metadata, with a named owner/channel from D3. Verify alert delivery when credentials exist; otherwise use a local test sink and record the external gate.

Acceptance: daily publications stay complete and duplicate-free, failures retain last-good data, late/missing sources become visible, and measured staging results meet the agreed standards. Explain any threshold changes with evidence and owner approval. This PR establishes reusable checks/runbooks; the period's dated observations can be a subsequent evidence commit.
```

### Prompt 10 Complete each remaining source pipeline

Repeat this prompt as separate PRs in the existing plan's dependency order: Reelly; remaining DXBI evidence/scraper/repair cutover; Property Finder; news; DDA; DXBI map; derived geography/override backups. Specify the selected scope before pasting it.

```text
Selected scope: [replace with ONE source or derived pipeline]. Implement only its remaining migration work after reviewing current code, audit findings, DXBI_RECONCILIATION section 10 and already-merged PRs. Use the shared publication/retry/ledger contracts and one Dagster producer per durable table.

Apply these scope-specific acceptance requirements as relevant:
- Reelly: recover required project/unit/assets/payment-plan producers, credentials from env only, public-serving parity and supported missing-data responses.
- DXBI: retain the existing evidence loader/ledger/raw archive; bring extraction/repair into durable Dagster ownership, remove dependence on developer paths, preserve hidden-unit accounting and canonical DLD coverage. Production cutover needs D1/D4 and output parity, then disables replaced timers.
- Property Finder: complete-crawl versions, last_seen, source-ID deduplication and withdrawal detection only from a certified complete crawl. A partial crawl must not mark unseen listings withdrawn.
- News: Alembic owns schema; one scheduled publisher uses stable article identity and correct transactions. Handle duplicate articles, per-row failures, approved content/thumbnail rights and bounded SSRF-safe downloads.
- DDA: bounded concurrency, explicit failed-plot records and coverage gates; no success-shaped stub rows; capped smoke data cannot replace full planning data.
- DXBI map: supported browser/image setup, complete coverage gates and credential policy.
- Geography: explicit dependencies, link-rate regression gates and recoverable manual overrides.

Test replay, correction, deletion/withdrawal where relevant, failure/restart, freshness and real serving queries. Acceptance: this inventory row is code ready with passing integration evidence; production enabled is claimed only after approved cutover and monitoring. Remove old paths only after their replacements are proved.
```

### Prompt 11 Turn Bayut collection into an explicit production option

```text
Inspect and reuse the registered bayut_listings source, full_collection planner/SQLite checkpoint logic, raw outputs and existing tests. State its current gaps: manual/experimental collection, unverified live coverage and no accepted warehouse-serving/freshness path. Do not start a parallel scraper or assume it is wholly absent.

Record Bayut's approved acquisition route, source rights and whether it is required for launch under D1. Without that decision, implement/test adapters with fixtures and leave production collection disabled. With approved access, integrate extraction, durable raw releases/checkpoints, normalized sale/rental listings, coverage certification, source-ID updates, complete-crawl withdrawals, quality/freshness checks and backend-serving contracts into the existing source framework. Keep observed listing age distinct from source posting date and snapshot capture time. Do not merge cross-provider listings based only on title/price; preserve source provenance and explicit match confidence.

Acceptance: restart resumes progress, duplicate pages do not inflate counts, failed/capped/blocked runs cannot publish complete inventory, fixture withdrawals behave correctly, and shared listing APIs can report Bayut availability. Live smoke and licensed coverage remain explicit gates. If Bayut is deferred, disable it transparently and document which benchmark/customer features lack its data.
```

### Prompt 12 Optimize databases and pulling with measured budgets

```text
Profile representative PostgreSQL/ClickHouse reads, ingestion batches and canonical rebuilds at the data scale recorded in the census. Establish a reproducible before/after dataset, query IDs, concurrency and warm/cold conditions. Do not infer production performance from the recorded 64/66 ms local requests.

Fix the largest evidenced bottlenecks with the smallest changes: indexes/pool budgets and statement deadlines for PostgreSQL; sorting/partitioning/projections or bounded aggregates where query plans justify them in ClickHouse; bounded batches/streaming/pagination, incremental checkpoints and skipped unchanged payloads for ingestion. Review repeated full scans, forced OPTIMIZE FINAL costs and copy-rebuild ceilings. Do not add FINAL everywhere, delete valid history, lower quality thresholds or use tenant-unsafe caches to achieve a speed target.

Use release-aware/tenant-aware cache keys and invalidation. Account for API workers, background workers and migrations in connection/concurrency limits. Acceptance: a checked-in benchmark procedure and sanitized results show p50/p95/p99, rows/bytes scanned, CPU/memory, throughput and source request counts; correctness/deduplication tests remain green. Split schema/backfill changes requiring distinct rollout into separate PRs. Unmeasured targets stay unverified.
```

### Prompt 13 Implement enterprise identity and lifecycle

```text
Implement the identity migration required by production-readiness sections 6 and 15. Use the recorded customer IdP decision; prepare adapters and tests while customer credentials are missing. Keep stable internal user IDs and map trusted issuer/subject rather than automatically joining accounts by email or phone.

Integrate the approved Cognito enterprise federation flow, backend access-token verification and frontend login/session/callback behavior. Preserve user references across memberships, chats, studies, CRM and usage. Put application-owned admin/quotas in protected app state. Disable public enterprise enrollment, enforce organization connection mapping and membership suspension, and implement offboarding/revocation behavior. Preserve owner/member authorization and explicit break-glass policy.

Acceptance: signature/issuer/audience/token-type errors fail closed; removed or wrong-organization employees lose API/tool/job access; migrated references remain correct; real staging SSO and browser callback/deep-link tests pass when test credentials are present. Mock JWT tests alone do not complete this gate. Avoid altering the enterprise identity architecture just to simplify Telegram linking.
```

### Prompt 14 Make imports and reports durable

```text
Move large CRM imports/report/export generation out of HTTP request lifetime using one small durable job mechanism consistent with the platform reference. Reuse PostgreSQL and object storage; choose the minimal existing-compatible worker design and document it. Do not introduce competing queue frameworks.

Persist organization/user/resource ownership, input object references, idempotency keys, status, lease/retry/progress and result object references. Use bounded parsing/rows/file sizes, transaction boundaries and authorized status/download endpoints. Recover work after worker loss and handle duplicate submission/lease expiry without duplicating contacts, records or billable effects. Reuse this mechanism for later Telegram delivery where appropriate. Verify the ETL chunked ingress's real callers/assembler under D5; disable unused unproved ingress instead of treating metadata completion as a working pipeline.

Acceptance: process kill and retry preserve job/result identity, one user's/org's files are inaccessible to others, expired/revoked users cannot obtain results, and failures are explicit and actionable. Regenerate typed API/query layers and add the import/report browser flow checks. Keep existing synchronous compatibility only for an intentional transition.
```

### Prompt 15 Unify AI guardrails, privacy, rate limits and spend

```text
Apply one server-side policy to every model path: general chat, feasibility chat/generation, news, CRM mapping/extraction and future Telegram. Inventory direct provider calls as well as Pydantic AI agents. Resolve trusted user/organization/capability context in application code and never let model tool arguments select a tenant.

Implement distributed inbound user/org/channel rate and concurrency limits, bounded context/tool/result sizes, provider deadlines and an atomic usage/budget reservation ledger. Reconcile actual/partial/unknown usage after success, retries, failure or cancellation; concurrent web and Telegram turns must not bypass one budget. Keep unauthenticated abuse throttles separate from provider extraction quotas.

Implement provider/model allowlisting, restricted-data handling, redaction and prompt-injection resistance at tool boundaries. Product-topic prohibitions are awaiting the user's policy session: add versioned policy configuration and explicit examples for review, but do not label invented restrictions approved. Authorization, no secret disclosure and no unsupported factual claims are required regardless of topic policy. Disable restricted features when their provider/channel approval is absent.

Acceptance: races cannot exceed configured reservations, direct CRM routes cannot bypass spend policy, disabled membership fails before provider/tool calls, spoofed tenant IDs and malicious retrieved text cannot expand access, and logs/traces exclude sensitive payloads. Provide predictable rate/budget errors to clients.
```

### Prompt 16 Create one durable chat runtime for web and Telegram

```text
Refactor only the chat execution boundary needed to share a runtime across channels while preserving public domain services. Persist turn ID, conversation/context identity, idempotency key, execution state, authorized result/artifacts and accounted usage. Web SSE and Telegram will adapt the same execution/result rather than maintaining separate agents/prompts or scraping one another's output.

Fix streaming lifecycle: ownership validation before work begins, heartbeats, explicit success/error/cancelled states, deadlines, disconnect/cancellation policy, reconnect/resume behavior and cleanup of provider/database resources. Decide and document whether an accepted durable turn continues after web disconnect. Prevent duplicate assistant messages/model charges on retries and order turns within a conversation. EOF without a success event must not become successful completion in the frontend.

Acceptance: disconnect, duplicate POST, provider 429/timeout, task SIGTERM and recovery have explicit persisted outcomes and correct usage reconciliation. Stored artifacts/results remain tenant-scoped. Existing chat and feasibility behavior/tests remain intact; any shared change has appropriate browser coverage. The transport-neutral result can express text, supporting evidence and optional charts/reports.
```

### Prompt 17 Extend the existing evaluation pipeline with real oracles

```text
Reuse backend/scripts/evaluation, EVALUATION.md and the existing fifteen exact questions. Preserve canonical wording and IDs. Add versioned seeded database releases, explicit time anchors, authoritative reference values/tolerances and separate bound variants for q12/q13/q15. Unbound variants must test clarification; do not invent building/project names.

Evaluate source selection, exact filters/business semantics, tool arguments, numeric results, sample sizes/units, freshness, evidence grounding, completeness, refusal, privacy, latency and cost separately. Add held-out paraphrases, ambiguous names, missing/stale data, zero counts, unavailable sources, injection and tenant-negative cases. A completed stream, a relevant tool name or a judge's approval cannot establish numeric correctness. Unassessed/unsupported must never count as pass. Reconcile older contradictory EVALUATION.md passages with implemented later sections.

Capture code/data/prompt/tool/model/provider/policy versions in comparable runs. Offline CI stays deterministic; live runs and sanitized LangSmith export require explicit configuration and budget. Acceptance: deliberately wrong units, calendar windows, unsupported promises and unauthorized data fail; changed snapshots/rubrics cannot masquerade as improvement; coverage is visible per case. Produce a reviewed acceptance rubric for model/tool rollout.
```

### Prompt 18 Implement actual MCP database capability integration

```text
Implement a real MCP server/client path within the current modular application, reusing supported locked SDKs where possible. Start with schema/data-availability discovery and one existing market capability. Prove MCP initialization, tools/list and tools/call through the selected transport; renaming ordinary functions MCP is insufficient.

Expose typed, bounded public domain operations over canonical serving data. Keep SQL in its owning query/repository modules and use parameter binding. Include source/release/time/filter/units/sample-size metadata in structured results. Use a capability registry derived from actual registration; do not duplicate a hand-maintained tool list. Prefer read-only market tools initially and keep privileged/write tools absent until explicitly specified.

Document the smallest supported transport/deployment choice. HTTP MCP requires proper authenticated resource access, token audience validation and per-call authorization; do not pass a broad upstream token through blindly. Bind the tool context to a verified VV principal/organization, enforce allowed tables/operations and query/resource budgets, and prevent per-session or concurrent-user context leakage. Use an SDK-supported in-process/stdio design only with a demonstrated secure per-request context if it avoids an unnecessary service deployment.

Acceptance: the production chat agent invokes the tool through MCP, returns the same reference result as the public domain operation, rejects unauthorized/direct calls and spoofed identities, handles timeout/reconnect/schema failures, and emits the existing sanitized trace chain. No database credentials or unrestricted execute_sql tool reach the model.
```

### Prompt 19 Add missing database capabilities one family at a time

Repeat as separate PRs: 19A sales; 19B rentals; 19C listings/yield; 19D supply/project delivery; 19E combined reports. Each becomes a typed public operation and MCP tool with reference tests; the combined-report scope depends on all preceding families.

```text
Selected family: [19A, 19B, 19C, 19D or 19E]. Close its exact gaps in the canonical fifteen-question benchmark using public services, then register bounded MCP tools. Reuse available domain operations and extend their explicit schemas where necessary. Do not put SQL/business calculations into chat or choose filters only because they produce an attractive answer.

Required semantics by family:
19A q01–q03: sales-only scope; apartment/bedroom/area/building filters; calendar last quarter versus rolling 90 days; price brackets/share denominators; quartiles; correct sqm-to-sqft conversion and price per sqft; minimum sample disclosure.
19B q04–q06: new/renewed vocabulary; contract term versus annualized rent; leap-year/partial-term convention; studio definition; six complete months with missing months explicit; no future/invalid contracts; zero-denominator behavior.
19C q09–q11: active sale/rent inventory, asking versus achieved prices, capture-time age, source coverage and same-release deduplication; defensible registered-rent comparables and gross-yield estimates with samples/confidence. Never label gross yield net ROI.
19D q07–q08 and q15 inputs: developer/unit delivery concentration, active project status, missing/overdue/future dates, sizes/prices/payment plans/progress and source dates. Missing Reelly fields/producers remain unavailable instead of fabricated.
19E q12–q15: entity clarification, deterministic multi-source calculations and a structured evidence-backed report. Reuse public report builders, not private CRM SQL. General market appraisals cannot acquire a lead/property owner's private record without a separately authorized lead context.

Acceptance: seeded SQL/domain tests give reviewed reference numbers, MCP filters/results match them, live-snapshot evaluation shows supported scope, and no-data/stale/ambiguous cases return appropriate clarification or limitations. Preserve trace/reference versions and update actual registration tests intentionally.
```

### Prompt 20 Improve the prompt, then prepare and evaluate fine tuning

Execute 20A as a prompt/model-configuration PR, then 20B as a separate training/evaluation PR.

```text
Selected scope: [20A or 20B]. Use the existing evaluation pipeline and supported MCP tool contract. Do not change model, prompt, tools and data simultaneously in a comparison.

20A: version the runtime prompt and model settings. Require database tools for database facts, correct source/date/filter/unit interpretation, clarification of unresolved entities, supported calculations, source/freshness/sample disclosure and honest missing data. Retrieved content is data, not authorization or system instructions. Remove promises for unavailable capabilities. Support concise narrative and structured artifacts from one evidence-backed result. Incorporate only approved product guardrail decisions. Compare against baseline and held-out paraphrases; preserve normal text questions while distinguishing pasted text facts from live database facts.

20B: verify a candidate runtime provider's actual fine-tuning/tool-call support, contract/residency/retention and budget. Do not assume Opus or the current Gemini/OpenRouter route supports the required training operation. Prepare a reproducible, privacy-reviewed dataset of tool selection/arguments, grounded summaries, clarification/refusal and corrected failures. Train/test splits must avoid question/property/time leakage. Fine tuning teaches behavior, not a frozen copy of live prices or private CRM/phone records; the model must still query current data through MCP.

With provider credentials and an approved budget, run the supported training job and held-out evaluations, record dataset/base/job/artifact identifiers and compare the tuned model with the best prompted baseline on identical data. Without them, commit validated dataset-generation/configuration/evaluation tooling and mark training pending. Promote only if quality improves without privacy, correctness, latency or cost regression; otherwise keep the measured baseline and report the result. Include rollback to the prior model/prompt configuration. No GPU hosting stack is implied.
```

### Prompt 21 Link Telegram identities to verified VV accounts

```text
Implement persistent Telegram account linking in the accounts domain after trusted identity and tenant checks are established. Use immutable Telegram user ID plus bot identity to map to stable internal VV user ID; resolve active membership/organization server-side on every operation. Store Telegram IDs using an appropriate 64-bit-safe representation. Telegram chat ID is a delivery target, not proof of account ownership.

Use a short-lived, single-use link initiated by an authenticated VV account and completed in a private bot conversation, with explicit account/channel confirmation. If registered phone is required, normalize E.164 and use the recorded verification policy. A typed phone, username, forwarded contact or matching CRM owner/contact number must not grant account access. For request_contact, require the contact user ID to match the sender and still apply VV account-link confirmation. Reject shared/ambiguous/unverified phone matches rather than guessing.

Include expiry/replay/rate limits, unique linkage constraints, unlink/relink, phone changes, revoked membership and safe lost-account recovery. Keep group chats and private-data delivery disabled unless separately approved. Acceptance: forged contact/ID, stolen/replayed token, changed phone, duplicate link and cross-tenant attempts fail; unlink/offboarding blocks subsequent turns; phone and tokens never enter logs. No SMS service is invented if existing SSO linking can prove account ownership.
```

### Prompt 22 Add Telegram as a channel adapter to the same chat

```text
Implement a thin Telegram transport using the linked VV principal, shared turn runtime, MCP tools, prompt/model configuration and global quotas. Verify the webhook secret header, HTTPS ingress, bot secret storage and allowed update types; persist/deduplicate update_id before acknowledging accepted work durably. Reject unlinked or disallowed/group contexts. Return quickly and process accepted turns through the existing durable mechanism.

Serialize conversation turns, recover after webhook retries/worker failure, escape/split outbound messages to Telegram limits, handle outbound 429/retry_after, and record delivery progress so retries do not repeat completed work unnecessarily. External sending cannot be assumed exactly-once after an uncertain network response; document reconciliation/user-visible behavior. Include cancellation/status/help/unlink handling only as needed for the agreed channel flow.

Render the same structured answer/evidence in web and Telegram. Reuse approved reports/artifacts through short-lived, account-authorized links; never expose private S3 URLs or strip caveats to fit a message. Both channels consume one budget and authorization policy. Private CRM/document features remain disabled until the Telegram processing policy permits them.

Acceptance: fixed-model/fixed-release parity tests give the same substantive numbers, filters, source dates and limitations across channels; prose formatting may differ. Replayed updates produce one turn, failures recover, offboarding/unlink denies access, and a real staging bot/webhook smoke passes when credentials exist. A mocked Bot API alone is code readiness, not live channel acceptance.
```

### Prompt 23 Prepare the release artifacts and infrastructure

Use separate PRs for runtime/frontend packaging, infrastructure definitions and release workflows. Reuse any already-merged implementation.

```text
Selected scope: [runtime/frontend packaging, AWS infrastructure, or CI/CD release flow]. Implement the corresponding production-readiness sections without deploying or applying infrastructure as part of this code PR.

Packaging: hardened non-root API/Holocron images, locked dependencies, readiness versus liveness, bounded workers/resources, graceful SIGTERM/stream drains, supported browser dependencies and no .env/browser-state/private-data image content. Deterministic static SvelteKit artifact/config, CloudFront deep-link fallback and real production-style serving tests; remove adapter-auto/dynamic-config ambiguity intentionally.

Infrastructure: one reviewed IaC tool, protected state/locking configuration, separate staging/production and regional VPC/private storage/databases/cache, task roles/secrets, private ingestion operations, backups/lifecycle/monitoring, enterprise auth/domain inputs and bounded cost/capacity settings. Prepare ClickHouse Cloud/network/role requirements without pretending provisioning is complete. Do not invent credentials/customer IdP details.

CI/CD: reuse GitHub Actions, protected environments, GitHub OIDC, immutable image digests and recorded frontend configuration; one migration runner with lock; staging browser/tenant/MCP/evaluation smoke gates, promotion of tested artifacts and application rollback compatible with schema. Eliminate reliance on mutable latest and the legacy ph-box production path only after replacement acceptance.

Acceptance: lint/validate/build/security checks pass, runnable dry-run/preflight procedures identify missing inputs safely, release manifest links code/schema/data/prompt/model/policy versions, and a rollback procedure is testable. Credential-free validation is distinct from provider-backed plan/apply/deploy evidence. Provide an exact external-input checklist so credentials can be supplied without code redesign.
```

### Prompt 24 Run final end to end acceptance and launch rehearsal

```text
Prepare and then execute the production-readiness sections 14–16 acceptance campaign in staging. Use the agreed ~150-user capacity envelope and immutable release candidates. Do not call the platform production ready while mandatory evidence is absent.

Cover the complete licensed-source raw/checkpoint/bronze/canonical/serving/API/browser/chat/MCP/Telegram path, including duplicate/correction/withdrawal behavior and source freshness. Run real SSO/offboarding and two-tenant negative tests; private studies/CRM; durable imports/reports and downloads; all fifteen benchmark cases plus held-out/guardrail cases with references. Confirm all AI routes and both chat channels share spend/privacy rules.

Inject provider 429/timeouts, partial extraction, source schema drift, webhook duplicates, client disconnect, Redis outage, worker death, API SIGTERM and managed-database failover where supported. Run ingestion with dashboards/AI/imports concurrently; record latency/resource/scan/connection peaks and budget behavior.

Restore PostgreSQL, warehouse/raw releases, object versions, Dagster metadata and overrides into separate targets; reconcile release markers, IDs/memberships/contacts/conversations and served counts. Rehearse image/frontend/prompt/model rollback without unsafe automatic migration downgrade. Test alert delivery and operator runbooks.

Acceptance: publish a sanitized release checklist with every test result/skip, failures resolved or feature explicitly disabled, recovery time/RPO/RTO and domain-owner approval. No missing reference is a pass. List exact credential/procurement/customer-policy gates if execution is blocked. The go-live decision remains the release owner's after production provisioning and domain smoke tests; PR creation is not authorization to launch.
```

## Review questions after each PR

Use this optional review prompt against the concrete PR diff before moving on:

```text
Review this PR against its original prompt and AGENTS.md. Trace immediate callers and data/auth boundaries. Check migrations, replay/idempotency, tenant context, failures/cancellation/accounting, generated contracts and rollout/rollback. Check that tests verify business intent rather than reproduce implementation. Identify required evidence that is missing or skipped, and any claimed completion that exceeds the evidence. Give prioritized actionable findings with file/line references. Make fixes in this PR only if requested; never merge/deploy automatically.
```

## What to do next

The Codex-only repository cleanup is complete. Start Claude with the common contract and Prompt 00 to protect existing work, then Prompt 01 to make required verification green. Proceed through database/pipeline prerequisites before the MCP and Telegram changes. Source decisions and cloud/customer credentials can be gathered while independent PRs are built; explicitly blocked staging observation and launch evidence must remain visible.

The desired final chatbot answers the supported canonical questions and held-out equivalents from actual authorized database results, asks for unresolved entities, and explains missing data. “All questions” is a coverage goal bounded by approved topic/data access and available source facts; it cannot mean inventing unavailable market or private-account information. The user's pending guardrail decisions become a versioned release input.

## External implementation references

These are protocol references, not evidence that VV already implements them. Verify the current stable specification and locked SDK compatibility during implementation.

- [MCP authorization and security best practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices): resource authorization, token audience boundaries and token-passthrough risks.
- [Telegram Bot API](https://core.telegram.org/bots/api): webhook secret header, update IDs, contact user IDs, contact-request buttons, message constraints and retry responses.
