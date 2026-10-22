# Backend architecture and migration

The backend remains a modular monolith organized by business responsibility.
The approved Phase 0–7 migration preserves endpoint paths, operation IDs, request
and response schemas, SSE events, stored JSON, source identifiers, query rules,
transaction boundaries, provider arguments, and frontend behavior.

The migration checkpoints below end at `9b3303f`. The subsequent evaluation and
observability implementation is documented in [EVALUATION.md](EVALUATION.md).
Its developer CLI lives outside the application domains in `scripts/evaluation`;
optional observation hooks live in infrastructure. It introduces no evaluation
HTTP domain and does not change the placement or dependency rules below.

## Placement and dependency rules

- HTTP endpoints belong to the owning `router.py`: parsing, guards and serialization.
- Application operations belong to the owning service or named capability module.
- Pure rules, identity, geometry, scoring and mapping have no database/provider I/O.
- PostgreSQL SQL belongs to the owning repository; use the caller's session.
  Application operations retain the existing commit/rollback sequence.
- ClickHouse SQL construction and execution belong to the owning query module.
  `app/clickhouse` contains the client, not business query templates.
- Cross-domain calls use public application operations or contracts. Do not import
  another domain's repository, private helper, router or internal query module.
- `market/reference.py` owns shared market names/units and community name terms;
  `market/scope.py` owns canonical transaction/project scope and location predicates;
  `market/periods.py` owns Asia/Dubai calendar periods. Supply owns its public Reelly
  eligibility predicate. These pure helpers are explicit cross-domain exceptions.
- Chat owns conversation/turn lifecycle, agent setup, thin tools, artifact rendering
  and events. Tools call domain capabilities; domains never import chat.
- CRM separates imports, identity, owners/ownership, inbound, matching,
  opportunities, workspaces and reports. Owners never call import orchestration.
- Project owns catalogue and radar. Plots owns GIS/DDA geometry, evidence and
  valuation. Feasibility consumes public plot/project operations through research.
- Feasibility separates saved studies, research, generation, deterministic
  assumptions and agents. Generation calls the HBU agent, never the reverse.
- Admin composes account capabilities and owns no repository. Auth profile
  composition is separate from identity to avoid an organization/auth cycle.
- Provider adapters own SDK/HTTP mechanics. Domain prompts remain in their domain.
- `core/dependencies.py` and authentication middleware are entry adapters allowed
  to compose domain operations. Other core/infrastructure modules are domain-free.
- The PostgreSQL model registry is for migration registration, not runtime imports.
- Add files only for meaningful boundaries. No generic repository framework,
  factories, microservices, queues or interfaces are introduced by this migration.

## Verification

Use `just` with supported host Node/Bun/Python runtimes. `just lint-backend` is
non-mutating. `just test-backend` runs the fast suite and accepts exact pytest
arguments. Database suites run separately against generated test databases;
required unavailable services fail rather than silently skip. Never point an
integration fixture at production. Unit SQL mocks do not prove database behavior.

Tests live in `tests/unit`, `tests/api`, `tests/contracts`, `tests/architecture`,
`tests/agents`, `tests/streaming`, and `tests/integration/{postgres,clickhouse,infrastructure}`.
Run frontend tests/builds and relevant Playwright flows throughout the migration.
Capture expectations before extraction; never refresh a contract fixture merely
because the refactored implementation differs.

## Compatibility exceptions

Existing HTTPException behavior may remain at application boundaries initially.
Pure rules/repositories must not acquire HTTP dependencies. Preserve current
multi-commit workflows, user/org scoping, read-triggered CRM matching refresh,
and quota coverage. Do not enable unregistered chat tools. Keep both existing
workbook implementations and their current callers. Holocron remains a separate
runtime; its news writer/schema-bootstrap behavior is an explicit compatibility
exception, not authorization to alter ingestion contracts.

## Ordered migration checkpoints

0. Characterization, isolated verification and dependency baseline.
1. Analytics, organization and admin router workflows.
2. Reusable analytics and chat orchestration.
3. Account persistence and provider adapters.
4. CRM capabilities and inventory ownership.
5. Project/radar/plots/area ownership.
6. Feasibility, then transactions/facets, listings, and supply as separate slices.
7. News/upload/infrastructure, ingestion contracts, and removal of temporary shims.

Complete and commit each phase before proceeding. A temporary compatibility
function delegates to one implementation; it never duplicates business logic.
The architecture-test exception list must only shrink. Evaluation, new tools,
quota expansion, concurrency/atomicity fixes, changed market formulas, and new
background workers are outside this migration.

## Phase 0 baseline discoveries

The existing migration chain omitted `conversations.organization_id`, although
the model, application and running local database already contain it. Revision
`a34d19c0b621` restores fresh-database setup without rewriting old revisions or
backfilling ownership from current membership. It preserves an existing column
and fails before changing a populated legacy table missing that column. This is
a prerequisite repair discovered by real PostgreSQL tests, not a tenancy change.
The new non-mutating lint recipe also exposed an unused news exception binding;
removing that binding does not change fallback behavior.

Phase 0 verification: 263 fast backend tests, 3 isolated PostgreSQL tests,
1 ClickHouse execution test, 1 Redis integration test, 68 frontend unit tests,
frontend production build, 31 Playwright tests, and backend lint/format checks
passed. Seven pre-existing formatting failures were normalized with identical
Python ASTs. Subsequent phases add capability-specific cases before extraction.

## Phase 1 checkpoint

Analytics routes now delegate workflows to the service, deterministic configuration
metrics to `metrics.py`, and warehouse execution to `queries.py`. Organization
routes delegate account workflows and invitations to their owners; persistence
is isolated in the organization repository. Admin composes public organization,
billing and usage reads. Existing HTTP errors and transaction boundaries remain.

Verification: 265 backend tests (including Bun workbook parity), 5 isolated
PostgreSQL tests, 1 actual ClickHouse test, 68 frontend tests, 31 Playwright tests,
OpenAPI/model/tool/JSON contracts, lint/format and acyclic imports passed. Six
legacy dependency exceptions were removed; no exceptions were added.

## Phase 2 checkpoint

Chat now owns conversation/turn orchestration, its repository, agent registration,
thin tool adapters, artifact envelopes and SSE encoding. Analytics owns reusable
chart results, chart policies and warehouse SQL. Project owns similar-project
lookup; listings owns listing-area resolution. Shared market names, units and
scope predicates live under `market`. Temporary old helper modules only re-export
the single implementation and will be removed in Phase 7.

Verification: 266 backend tests, 5 isolated PostgreSQL tests, 3 actual ClickHouse
tests, 68 frontend tests, production frontend build and 33 Playwright tests passed.
Browser additions cover real conversation create/rename/reload/delete and fixed
SSE/artifact rendering plus message reload; the latter deliberately mocks the
stream, while backend streaming tests exercise the agent lifecycle. Query execution
covers pulse, rankings, sales/project charts and similar projects. The historical
pulse inclusion of non-sales remains distinct from sales-chart filtering.
The graph is acyclic, 13 further legacy edges disappeared, and chat cannot import
ClickHouse or contain warehouse statements. Provider/tool schemas remain frozen.

## Phase 3 checkpoint

Identity delegates GoTrue mechanics to `providers/auth/client.py`; `/me` composition
lives in `auth/profile.py`. Billing owns a Stripe gateway and subscription repository,
and dispatches webhook application work from its service. Organization exposes
customer and owner-membership reads. Usage persistence lives in its repository.
The dependency adapter uses public account reads and retains the latest-subscription
selection (separate from the strict single-subscription administrative read).

Verification: 269 backend tests plus the added account-boundary check, 8 isolated
PostgreSQL tests, 68 frontend tests and all 33 Playwright tests passed, with clean
lint/format and acyclic imports. New tests preserve auth metadata and redirect
arguments, webhook event filtering, active-subscription preference, idempotent
subscription persistence, checkout failure commits, and per-user token accumulation.
No quota coverage, concurrency, transaction atomicity, SDK call mode or HTTP contract
was changed. Both remaining runtime model-registry imports were removed.

## Phase 4 checkpoint

CRM now has explicit owners for contact parsing/identity, import orchestration,
AI mapping/extraction, ownership evidence, owner registry, inbound enquiries and
imports, matching policies/workflows, opportunities, agent workspaces, and property
reports. PostgreSQL operations live in capability repositories and return records
or rows; ClickHouse operations live in owner/opportunity/report query modules.
The three old service facades were removed after all runtime, script and test
callers moved. Inventory freshness and Reelly eligibility are public listings and
supply capabilities; neither inventory domain imports CRM.

Verification: 271 backend tests, 11 isolated PostgreSQL tests, 5 ClickHouse tests,
68 frontend tests, production frontend build, clean lint and all 34 Playwright
tests passed. New cases execute owner-registry DISTINCT ON deduplication, import
deletion retaining agent notes/workspaces/saved JSON, match score/state transitions,
legacy rental identity fallback, latest sale evidence, Dubai source eligibility,
and contact/note/consent persistence through the real browser/API. Existing import
failure commits, list-summary ordering, read-triggered matching, scoring thresholds
and reports remain unchanged. Three Reelly-scope exceptions were removed; the graph
is acyclic and CRM SQL/inventory boundaries are now enforced.

The rental refresh script now fingerprints the extracted rule/query modules in a
fixed order. This changes its fingerprint once on migration and preserves detection
of subsequent matching-rule changes, marker format, locking and dry-run rollback.

## Phase 5 checkpoint

Project catalogue and radar now own their application operations, response mapping
and warehouse queries. Plots owns DDA/GIS geometry, plot lookup, land evidence and
valuation; area warehouse access has its own query module. Existing HTTP routes
remain in project and feasibility. Old model modules re-export the identical plot
classes for import compatibility. Map rings and feasibility rings retain their
separate closing-point behavior, and bulk geometry still defers valuation.

Verification: 273 fast backend tests, 11 PostgreSQL tests, 9 ClickHouse tests,
68 frontend tests, production build, all 35 Playwright tests, lint and acyclic
imports passed. New tests cover the one-hour geometry cache, leading-zero plot
identifiers, land-evidence fallback and conversions, both synthetic project-ID
ranges, shared geographic resolution, area sorting, and browser plot selection.
Unreachable legacy radar code following an unconditional return was removed;
the active radar queries and response contracts remain unchanged.

## Phase 6 checkpoint

Feasibility now separates saved studies/repository, generation, deterministic
assumptions, research/queries, agent declarations/tools, and streaming lifecycle.
The generation path calls the HBU agent through its invocation operation; prompts,
registered tools, request limits and underwriting defaults remain unchanged. The
workbook uses pure normalization, removing the final legacy dependency exception.

Transactions own facets, warehouse queries and export formatting; the existing
filters URLs remain adapters and their model import re-exports the same class.
Listings separates source mapping, benchmark/urgency policy and warehouse queries.
Supply separates source mapping, scoring, rankings and queries. Catalogue response
composition stays in its application service to avoid a mapping/scoring cycle.
Existing empty developer-logo/score lookups and pending financial fields remain.

Verification: 277 fast backend tests, 12 PostgreSQL tests, 15 ClickHouse tests,
68 frontend tests, production build, all 37 Playwright tests, lint and acyclic
imports passed. New coverage includes provider selection/request limits, committed
study JSON and ownership scopes, rental evidence sample thresholds, transaction
paging/facets/CSV sanitization, listings benchmarks, supply catalogue/rankings,
and both browser CSV downloads. All frozen contracts still match.

### Future improvement — outside this migration

The pre-existing monthly listing-price SQL mixes Int64 and Float64 branches in
`multiIf` and raises `NO_COMMON_TYPE` on local ClickHouse 24.8. This was reproduced
with the identical pre-migration expression. A characterization test records the
failure; annual-price inventory and benchmark execution pass. The expression has
not been changed. Explicit-ID transaction exports also retain their existing
uncapped selection path, separate from the 50,000-row filtered-export limit.

## Capability map for future changes

| Capability | Application owner | Persistence / warehouse | Pure rules or provider boundary |
| --- | --- | --- | --- |
| Conversations and turns | `chat/service.py` | `chat/repository.py` | `agent.py`, thin `tools.py`, `artifacts.py`, `events.py` |
| Analytics and charts | `analytics/service.py`, `charts.py` | `queries.py`, `chart_queries.py` | `metrics.py` |
| Market metrics (sales/rentals) | `analytics/market_metrics.py` | `market_metric_queries.py` | `market/periods.py`, `market/reference.py` community names, `market/scope.py` location predicate |
| Project delivery | `project/delivery.py` | `project/queries.py` | Shared `market` period and location helpers |
| Identity and profile | `auth/service.py`, `profile.py` | Public account operations | `providers/auth/client.py` |
| Organization and invites | `organization/service.py`, `invitations.py` | `repository.py` | Request/response contracts in `models.py` |
| Billing and usage | `billing/service.py`, `token_usage/service.py` | Each domain's `repository.py` | `billing/stripe_gateway.py` |
| Administrative workflows | `admin/service.py` | Public organization/billing/usage reads | No admin repository |
| CRM | `imports.py`, `ownership.py`, `owners.py`, `inbound_imports.py`, `inbound.py`, `matching.py`, `opportunities.py`, `workspaces.py`, `reports.py` | Capability-specific repositories and query modules | `identity.py`, `import_parsing.py`, `inbound_policy.py`, `matching_policy.py`, `ai.py` |
| Projects and radar | `project/service.py`, `radar.py` | `queries.py`, `radar_queries.py` | `mapping.py` |
| GIS and land evidence | `plots/service.py`, `evidence.py`, `valuation.py` | `queries.py` | `geometry.py` |
| Area evidence | `area/service.py` | `queries.py` | Existing area contracts |
| Feasibility | `studies.py`, `generation.py`, `research.py`, `chat.py`; read adapters in `service.py` | `repository.py`, `queries.py` | `assumptions.py`, `assumption_defaults.py`, `normalization.py`, agents, existing workbook/engine |
| Transactions and facets | `transaction/service.py`, `facets.py`, `exports.py` | `queries.py` | Export sanitization stays with exports; `/filters` remains a route adapter |
| Listings | `listings/service.py` | `queries.py` | `mapping.py`, `policy.py` |
| Supply and developer rankings | `supply/service.py`, `rankings.py` | `queries.py` | `mapping.py`, `scoring.py`, public `scope.py` |
| News | `news/service.py`, `ingestion.py` | `repository.py` | `agent.py`; signing through `core/object_storage.py` |
| Chunk uploads | `upload/service.py` | Object storage and upload-state Redis | Existing chunk/metadata protocol |

Every domain keeps its HTTP parsing/guards in `router.py` and canonical contracts
in its existing model modules. Only add a named policy, query or repository module
when the capability needs it. Large cohesive SQL, deterministic assumption and
workbook modules are intentional; line count alone is not an extraction trigger.

A new evaluation capability would get `evaluation/router.py`, `service.py` and
`models.py`, tests under the existing test categories, and only the repository,
query or provider adapter that its actual persistence/integration requires. Register
its router in `main.py`; register a PostgreSQL table and add a migration only if
needed. It may call public domain operations and provider adapters. It must not
import chat lifecycle, tools, repositories or private helpers. A later chat tool
can call the evaluation operation without making evaluation depend on chat. No
evaluation implementation or new tool registration is part of this migration.

Debug a request from router to its application operation, then the owning query,
repository or provider. Keep failure/commit ordering visible in the application
operation. Add focused policy tests for rules, API tests for contracts, real database
tests for query semantics/commits, streaming/tool tests for agents, and browser tests
for affected user flows. The dependency tests enforce acyclic imports, public
cross-domain operations, SQL ownership, pure policies, and removal of old shims.

## Phase 7 and final checkpoint

News owns article ingestion, normalization, repository access and summary agents.
Upload retains chunk transport/state and calls news ingestion through its existing
HTTP route. The backend news-import CLI retains arguments, environment/storage
selection, dry-run behavior, and engine lifecycle; its normalization and batch
writes use news operations. Upload deduplication still leaves an existing article
untouched; batch imports still update only when requested. Holocron's separate
news writer and schema-bootstrap behavior are unchanged.

Thumbnail SDK signing is infrastructure-owned. Legacy news environment lookup and
SDK arguments remain distinct from upload's configured path-style client. Public
cache invalidation preserves Holocron's required connection check and prefix list.
All temporary shared-query/reference and moved-model shims were removed after
callers adopted canonical owners. The explicit PostgreSQL registry includes
organization memberships/invitations and inbound activity, with no schema changes.

Final verification:

- 286 fast backend tests, including Bun workbook parity and frozen API/model/tool/
  JSON/streaming contracts; 13 isolated PostgreSQL tests; 15 real ClickHouse tests;
  2 Redis/MinIO integration tests. No test is skipped in the final run.
- 68 frontend unit tests, production build, 10 dedicated guard tests and all
  37 Playwright tests pass. Browser export rendering uses mocked download payloads;
  backend integration independently executes actual exports against ClickHouse.
- Backend lint/format, acyclic imports, zero legacy dependency exceptions and the
  public cross-domain access rule pass. An independent AST comparison confirms all
  36 extracted feasibility assumption functions, 19 supply scoring functions and
  9 listings policy functions are identical to the original implementation.
- News CLI `--help` succeeds. All 469 Holocron tests pass. Its changed cache-refresh
  module and test pass Ruff check/format and `ty`; the whole runtime passes Ruff
  lint. Holocron emits one existing collection warning for a helper exception class.

Unrelated baseline checks are recorded rather than silently treated as passing:
frontend `bun run check` reports eight errors in the unchanged listings API wrapper
and page (optional intelligence fields versus the generated types). Frontend source
and generated client files are unchanged from the migration baseline. Holocron's
baseline `just check` stops on 14 pre-existing formatting failures; its separate
broad type check reports 62 diagnostics, including missing optional admin dependencies
and existing handoff/source typing issues. These checks were run, and no assertions,
type rules or contracts were weakened. The known local monthly-price SQL failure
recorded above also remains outside this structural migration.

The migration itself introduced no evaluation capability, new chat tools, quota
expansion, concurrency/atomicity changes, market-formula changes, new workers, or
deployment. Subsequent evaluation tooling observes these preserved capabilities.
