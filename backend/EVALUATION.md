# AI evaluation and observability

Discovery base: `9b3303f`, branch `refactor/backend-domain-boundaries`.
The completed migration and `ARCHITECTURE.md` remain authoritative.

## Implemented system and private execution evidence

The user explicitly approved the live OpenRouter question/tool-result flow on
2026-09-28, with full evidence restricted to local storage. All 15 cases have now
executed through real loopback HTTP, GoTrue authentication, FastAPI guards,
conversation creation, the existing SSE service, message reload and deletion of
only the evaluation-created conversations. The first run preceded production instrumentation; later changes add optional observation only.
Full evidence is ignored under `.evaluation/20260928T161314Z` and the corrected capture `.evaluation/20260928T164246Z` (directories 0700,
files 0600). No remote telemetry or LangSmith upload occurred.

The local API responds to health checks. The configured model is
`google/gemini-3.1-flash-lite` through OpenRouter. Pydantic AI is locked at 1.38.0,
Logfire at 4.19.0. LangSmith 0.14.1 and OpenTelemetry 1.39.1 are now locked. No LangSmith key is configured locally, so remote ingestion remains unverified.

## Actual AI capabilities, models, agents and tools

All currently found model calls select `settings.DEFAULT_CHAT_MODEL` and
`settings.OPENROUTER_API_KEY` (`app/config.py`). There is no application-level
multi-model fallback/routing mechanism. OpenRouter's downstream routing is
provider behavior and must be recorded from response metadata when available.

| Capability | Runtime entry and agent | Tools / model behavior | Data and downstream operations |
| --- | --- | --- | --- |
| Market chat | `app/chat/router.py` → `service.stream_chat_response` → `agent.agent.iter` | Three registered tools; low reasoning effort; text streaming | Conversation/history in chat PostgreSQL repository, usage through token usage service; tools below |
| Market pulse | `chat.tools.get_market_pulse` | `area`, `period_days` | `analytics.service.get_market_pulse` → `analytics.queries.fetch_market_pulse` → ClickHouse `ch_transactions` |
| Area rankings | `chat.tools.rank_areas` | `metric`, `limit`, `period_days` | `analytics.service.rank_areas` → `analytics.queries.fetch_rank_areas` → `ch_transactions` |
| Similar projects | `chat.tools.get_similar_projects` | `project_name`, `limit` | `project.service.get_similar_projects` → reference and comparable queries → `ch_transactions` |
| Feasibility workspace chat | `feasibility.router` → `chat.stream_chat_events` → `stream_feasibility_response` → `chat_agent.workspace_chat_agent.iter` | Research plus staged edits; temperature 0.1; supplied history/context | `research.get_area_market_research`, `rank_plot_comparables`, `plots.service.get_plot_details`, `plots.evidence.get_land_sale_evidence`; ClickHouse |
| HBU study generation | `feasibility.generation.generate_hbu_study` → `hbu_agent.run_seed_draft` | Selects `hbu_seed_agent_bts_prefetched` or `hbu_seed_agent_btr`; structured `HBUSeedDraft`; temperature 0.1; request limits 15 / 3 | Research is prefetched through public area, project, plots and feasibility operations; deterministic assumption composition follows model draft |
| Legacy HBU declaration | `feasibility.hbu_agent.hbu_seed_agent` | Seven registered tools, **not selected by production `run_seed_draft`** | Existing/off-plan pricing, area rents, rental/pricing/pipeline comparables, land sales |
| Dashboard news summary | `news.service.fetch_news_summary` → `news.agent.summarize_articles` → lazy summary agent | Structured `NewsSummaryDraft`; temperature 0.1; one request; deterministic fallback on error | `news.repository` PostgreSQL headlines; empty-input response does not invoke model |
| CRM owner import schema mapping | `crm.imports` → `crm.ai._llm_mapping` | Direct HTTP OpenRouter JSON response; headers only; heuristic fallback | CRM parsing/identity rules and owning import repositories; no autonomous tools |
| CRM enquiry extraction | `crm.inbound_imports.process_inbound_import` → `crm.ai._ai_extract` | Direct HTTP JSON extraction; deterministic extraction first; redacted input; empty fallback | Owning CRM PostgreSQL workflows and matching capabilities; no autonomous tools |
| CRM header mapping | `crm.inbound_imports.preview_inbound_import` → `crm.ai._ai_header_mapping` | Direct HTTP JSON; conservative validated header mapping | Supplied headers and campaign context; no autonomous tools |

Workspace tool set: `get_current_plot`, `get_area_market_data`,
`rank_pricing_comparables`, `rank_pipeline_comparables`, `get_plot_land_sales`,
`show_transactions`, `show_analytics`, `update_prices`, `update_unit_mix`,
`replace_unit_mix`, `update_assumptions`, `update_payment_plan`.
The show tools return UI instructions; chat orchestration calls
`research.fetch_chat_research`. Mutation tools propose `pending_action` events;
the frontend owns approval and the live workbook. They do not persist arbitrary
server mutations. This distinction must remain visible in traces and tests.

No additional model invocation was found in frontend or Holocron source searches.
Holocron remains a separate ingestion runtime, not an agent framework.

## Data ownership and important limitations

Chat authentication, active membership/subscription and token quota use existing
route dependencies. Conversation ownership is checked before streaming. Evaluation
must enter the authenticated HTTP application, without dependency overrides or
private repository reads. Evaluation conversations may be deleted via the same
API; token usage remains accounted for, just as in normal application use.

The three market chat tools currently reach only `ch_transactions`. They do not
reach registered rents, listings or supply tables. `get_market_pulse` and area
rankings preserve historical non-sales inclusion. Pulse aliases raw area and
price/area as sqft without conversion in its query. These are existing risks to
characterize, not behavior to silently fix in an evaluation project.

Other public application operations exist but are **not registered chat tools**:
`analytics.charts.{sales_chart,rental_chart,project_chart,custom_chart}`,
`transaction.service.{fetch_sales_transactions,fetch_rental_contracts}`,
`listings.service.{list_listings_from_clickhouse,listing_analytics_from_clickhouse}`,
`supply.service.{list_supply_catalogue_projects,get_supply_catalogue_project}`,
and `crm.reports.get_property_report`. Do not enable tools merely to pass tests.
CRM reports require an authorized lead context; a building name alone is not that
context.

Feasibility research reads `ch_transactions`, `ch_projects`, `ch_rent_contracts`,
`ch_units` and `ch_buildings`; plot evidence/geometry reads transactions and
`ch_gis_land_plots`; project/area operations add geographic and project facts.
Listings query modules read Property Finder inventory and supply modules read
Reelly inventory/project facts. Model calls go to OpenRouter; auth goes through
GoTrue. Redis caches and object storage are infrastructure, not additional LLMs.
PostgreSQL holds conversations/messages, token usage, organizations/subscriptions,
CRM imports/contacts/leads and saved feasibility studies under owning repositories.

## Canonical benchmark and execution matrix

The checked-in dataset preserves the user's exact 15 questions. Placeholders are
literal templates, not inferred entities. Unbound cases can later be run as
clarification tests; bound variants require explicitly supplied parameter values
and separate identity/fingerprints. There is no current chat-side entity resolver
that supplies those missing values automatically.

Every question enters the **same market chat route**. The table below describes
source-derived capability gaps, **not observed execution paths**. The observed-run section below supersedes this preliminary static matrix.

| ID | Requested behavior | Source-derived gap to investigate during execution |
| --- | --- | --- |
| 1 | JVC sales price brackets and shares | No bedroom/property/price-bracket filter in pulse |
| 2 | Business Bay building rankings, 2BR, last quarter | Area rank tool cannot rank buildings or restrict bedrooms/calendar quarter |
| 3 | Downtown 1BR interquartile range | No quartile output or bedroom filter |
| 4 | JLT new/renewed rents | No rental-contract tool |
| 5 | Marina multi-year rent normalization | No rental records/annualization tool |
| 6 | Monthly studio rents, six complete months | No rental trend tool |
| 7 | Dubai South delivery concentration | No developer/supply tool |
| 8 | JVC missing/overdue completion dates | No project-delivery status tool |
| 9 | Dubai Hills listing counts/asking prices | No listings tool |
| 10 | Marina listing age at capture | No listing age/capture tool |
| 11 | JVC listing yield with rental evidence | No listing-to-rent evidence operation reachable from chat |
| 12 | JLT building rental appraisal | Unresolved building; no rental/listing tool or report tool |
| 13 | Downtown seller competition | Unresolved building; no listing/comparable-report tool |
| 14 | Business Bay monthly multi-source report | Pulse can provide partial aggregate context only |
| 15 | Three-project off-plan comparison | Unresolved project names; similar-project tool lacks payment/progress data |

## Existing tests, observability and uncovered behavior

`tests/agents/test_chat_tools.py` freezes exactly three tools and their schemas.
`test_feasibility_tools.py` freezes workspace tools. `test_hbu_generation.py`
checks real routing choices, request limits and deterministic plot constraints
with mocked provider responses. Streaming tests cover missing conversations,
provider failure, usage/save ordering, invalid feasibility history and completion.
Unit tests cover OpenRouter service-tier compatibility, CRM redaction/extraction,
import identity/mapping, news fallback, listing/supply policies and query rules.
Contracts freeze OpenAPI, models and stored JSON; architecture tests enforce
acyclic imports and domain ownership.

Real-service integration suites use isolated PostgreSQL/ClickHouse databases.
`tests/integration/clickhouse/test_chat_capabilities.py` executes real pulse,
ranking, similar-project and chart SQL and explicitly preserves non-sales pulse
behavior. Such tests establish query contracts, not natural-language answer
quality. Existing Playwright `chat.spec.ts` tests conversation CRUD and mocked
SSE/artifact rendering; feasibility tests cover proposed edits and saved studies.

`app/main.py` configures Logfire/Pydantic AI/FastAPI instrumentation. There is no
benchmark dataset, experiment workflow, quality evaluator, quality baseline or
regression gate. Direct CRM HTTP model calls are not Pydantic AI spans. Token
accounting exists for market chat; do not assume that coverage extends to every
AI invocation. Cost and routed-provider identity are not uniformly captured.

## Integration decision and implementation sequence

1. Preserve canonical inputs and produce a source-derived capability inventory.
   Keep evaluation orchestration in `backend/scripts/evaluation`, outside domain
   modules. Generate tool declarations, callable edges, source/table references,
   model configuration and source fingerprints from code. Label unresolved dynamic
   calls and distinguish static reachability from observed execution. No parallel
   manually maintained tool registry.
2. With execution approval, run all 15 through local authenticated HTTP, collecting
   local OpenTelemetry/Pydantic AI evidence. Preserve exact production prompts,
   reasoning settings, streaming and authorization. Do not substitute direct
   `agent.run` for the streaming path. Record date/time, dataset digest, code SHA,
   configured/resolved model, prompt/tool fingerprints, warehouse snapshot label,
   errors, completion, latency and usage. Inspect each result before finalizing
   evaluators. Missing prerequisites and unresolved references remain explicit.
3. Add opt-in infrastructure observability. Prefer native Pydantic AI OpenTelemetry
   and LangSmith's OTLP ingestion over replacing agents or decorating domains with
   LangSmith APIs. Reuse one tracing configuration rather than double-instrumenting
   with Logfire. Small infrastructure spans should capture database system,
   operation/query fingerprint, source tables, row count, latency and status.
   Add explicit public-operation spans only where needed to explain the real path.
   Direct CRM provider calls need equivalent metadata-only spans, including fallback
   behavior. Preserve dependency direction and streaming cancellation behavior.
4. Finalize evaluator strategy **after the observed runs**. Separate transport and
   streaming contracts, tool failures, evidence-source/argument checks, reference
   value checks, and semantic/human review. A completed stream is not a correct
   answer. Do not award factual correctness without a trustworthy oracle. Missing
   references mean unassessed, not pass. Numeric tolerances/record expectations
   belong to curated reference metadata; do not duplicate production business SQL.
   Judge grounding/completeness only where deterministic evidence cannot establish
   quality; version the rubric and judge model. A judge is not an oracle for market
   facts. Human review handles ambiguous scope and unsupported requests.
5. Add local persisted results and explicit LangSmith dataset/experiment publishing.
   Use content-addressed dataset versions and stable example IDs. The SDK supports
   local evaluation without upload and evaluating existing experiments without
   rerunning models. Keep raw evidence locally by default; publish only reviewed,
   sanitized inputs/results. Comparison requires matching dataset/parameters,
   reference version and data/date context; otherwise report incomparable. Track
   per-case regressions and unassessed coverage rather than one misleading score.
6. Establish a real baseline, run targeted then broad checks, inspect diffs and
   commit coherent stages. Add deterministic offline CI checks without model keys;
   live/model runs are explicit cost-bearing workflows, not automatic pull-request
   jobs with production credentials. New questions append dataset examples.

## Privacy, single-trace debugging and operational tradeoffs

Default remote telemetry must exclude prompts, answers, tool arguments/results,
SQL text/parameters, user/org IDs, auth headers, error messages/stack contents and
CRM context. A field-name denylist alone is insufficient for free text and nested
content. Use allowlisted metadata at the exporter boundary, including exception
and resource/event handling. Do not assume LangSmith SDK input hiding also filters
arbitrary OpenTelemetry attributes. Hash code/prompts/query templates for version
identity; do not hash low-entropy PII as a substitute for removing it.

Full evidence is needed for grounding and numerical debugging. Keep it in explicit
private local artifacts (restricted permissions, ignored by Git), with separate
sanitized-fixture mode for remote content. Production/staging/local projects must
be separated and export disabled unless configured. Sampling affects operational
observability, never whether an offline benchmark case is considered evaluated.
A user must be able to follow root → model → tool → operation → database/provider
and see where an error occurred; metadata-only remote views link to local evidence
by opaque run ID, not secret filesystem paths or auth context.

Relative time questions cannot have an eternal numerical baseline on mutable
warehouse data. Record date/time and a declared data snapshot; use isolated seeded
integration data for deterministic SQL assertions. Do not alter `today()` or model
prompts silently to fake reproducibility. Provider cost is unknown unless returned
or derived from versioned, explicit pricing; unknown is not zero.

## Verification plan and frontend preservation

- Validate canonical dataset identity, stable case IDs, explicit parameter binding
  and refusal to invent/partially bind placeholders.
- Verify inventory updates when actual registrations/call paths change, separates
  inactive declarations, and reports uncertainty rather than inventing edges.
- Test privacy at the export boundary with nested tool payloads, SQL parameters,
  exception messages/events and adversarial PII; disabled export makes no requests.
- Test real nested spans and parent IDs with controlled Pydantic AI responses;
  cancellation/error paths must retain existing SSE and persistence semantics.
- Test evaluators against deliberately wrong evidence, date/filter errors and
  missing references, once trace-driven evaluator requirements are established.
- Test comparison rejects changed datasets/parameters/data contexts and detects
  per-case regression despite successful transport.
- Run `just test-backend` targeted then full; `just lint-backend`; isolated
  database suites where instrumentation touches those clients; frontend tests
  and build; relevant existing Playwright chat/feasibility/guard flows. Do not
  make all model questions browser tests. No UI change or API schema change is
  currently justified, so client regeneration is unnecessary unless scope changes.
- Smoke-test LangSmith ingestion/dataset/experiment links only with configured
  credentials and allowed sanitized content. Report absent credentials as a
  verification gap, never as successful integration.

## Current official guidance consulted (2026-09-28)

- [Pydantic AI tracing](https://docs.langchain.com/langsmith/trace-with-pydantic-ai)
- [OpenTelemetry ingestion and attribute mapping](https://docs.langchain.com/langsmith/trace-with-opentelemetry)
- [Evaluation SDK](https://docs.langchain.com/langsmith/evaluate-llm-application)
- [Local evaluation without upload](https://docs.langchain.com/langsmith/local)
- [Evaluate existing experiments](https://docs.langchain.com/langsmith/evaluate-existing-experiment)
- [Mask inputs and outputs](https://docs.langchain.com/langsmith/mask-inputs-outputs)
- [Compare experiments](https://docs.langchain.com/langsmith/compare-experiment-results)

The SDK signatures, serialization and OTLP encoding are tested against the installed versions. Actual local quality findings follow below; SDK tests do not prove remote ingestion.

## Implemented discovery commands

- `just ai-benchmark`: print validated canonical cases and dataset SHA-256.
- `just ai-inventory`: print generated static source evidence as JSON. No app
  import, database access, provider call or telemetry export is performed.
- `just test-evaluation`: offline dataset, tracing, evaluator, export and runner tests. These also run in the
  default `just test-backend` suite.

`Case.bind()` preserves an unbound question exactly. Explicit bindings must supply
all and only its declared parameters with nonempty concrete values. Case IDs can
extend beyond 15; dataset digests change with content, independent of JSON layout.
The inventory fingerprints actual prompt literals/declarations and code, lists
registered tools and source signatures, follows resolvable module-level calls to
public capabilities/query functions, and records unresolved calls separately.
Direct HTTP model endpoints/configuration references are included. Static reachability is distinguished from actual calls; private runtime evidence adds compiled tool schemas, selected tools, SQL and results.

The discovery plan above is historical context. Execution, local assessment, regression comparison and sanitized LangSmith adapters are now implemented. Remote ingestion is the remaining credential-dependent verification.

Discovery checkpoint verification: 292 backend tests passed in the default run;
three workbook parity tests initially skipped because Bun was absent from PATH,
then all three passed with the installed Bun runtime (295 verified in total,
including nine new discovery tests). All 68 frontend unit tests passed with the
installed Node 20.19.6/Bun paths; the first attempt with system Node 18 failed at
Vite startup. Backend Ruff checks and formatting checks passed. No production
runtime code changed. Database integration suites, frontend build and Playwright
were not rerun for this read-only tooling/documentation checkpoint. At that discovery checkpoint, live execution and evaluation had not yet run. The following sections record the subsequently completed work.


## Observed execution baseline, 2026-09-28

Run `3aea3e90-1905-4832-a6ca-2df124995702` is stored privately in `.evaluation/20260928T161314Z`.
All 15 streams completed and their assistant messages persisted; all 15 evaluation
conversations were subsequently deleted through the authorized API (204). Token
accounting was preserved. There are 621 local spans and 22 model requests, with
26,162 input / 8,411 output tokens reported on model spans. Downstream OpenRouter
vendor and cost are not returned by this streaming implementation: unknown, not
zero. Configured and returned model: `google/gemini-3.1-flash-lite`.

Seven cases selected `get_market_pulse`; none selected `rank_areas` or
`get_similar_projects`. Five pulse results had zero transactions, nonfinite SQL
aggregate values and epoch min/max dates, while the application returned
`success=true`. Native tool serialization converted those metrics to null. Two
Business Bay results contained transaction aggregates. None of the cases reached
registered-rent, listings or project-delivery sources. PostgreSQL calls served
auth/subscription/quota, conversation history, persistence and usage accounting.
There was no observed database exception; absent evidence must not be called a
query failure. Empty results do not prove the underlying warehouse lacks data:
exact area matching and relative date windows may exclude records.

| Case | Observed tools and arguments | Answer characteristics and failure boundary |
| --- | --- | --- |
| q01 | pulse: Jumeirah Village Circle, 90 days | Empty evidence; acknowledges bracket limitation, then ungrounded market commentary; no counts/shares |
| q02 | pulse: Business Bay, 90 days | Declines building/bedroom breakdown; uses area aggregates; reported unit price differs from tool value; trailing days are not last quarter |
| q03 | pulse: Downtown Dubai, 180 days | Empty evidence; acknowledges no quartiles but asserts a typical monetary range without supporting records |
| q04 | pulse: Jumeirah Lake Towers, 90 days | Empty transaction evidence; declines new/renewed split yet offers a rental/bedroom analysis the tools cannot perform |
| q05 | none | Declines contract-level request, then asserts rent, yield and rental-volume figures without retrieval |
| q06 | pulse: Business Bay, 180 days | Transaction count presented alongside rental commentary; no studio/rent/month buckets; rolling days differ from six complete months |
| q07 | none | Discloses no developer-delivery capability; no concentration answer |
| q08 | none | Discloses no completion-date capability; no project/unit counts |
| q09 | pulse: Dubai Hills Estate, default 90 days | Empty transaction evidence; declines listing split, adds unsupported broad market claims |
| q10 | none | Declines listing-age capability, then asserts pricing, premiums, discounts and yields without retrieval |
| q11 | pulse: Jumeirah Village Circle, 90 days | Empty evidence; declines listing/contract access, asserts yield range without rent evidence |
| q12 | none | Requests building name but supplies rental figures/yields without retrieval; explicit parameter remains unresolved |
| q13 | none | Requests building name but supplies a unit-price claim without retrieval and promises unavailable listing/report functionality |
| q14 | none | Produces a multi-source market report with quantitative claims despite no data retrieval; strongest grounding risk |
| q15 | none | Requests all three project names; no numeric evidence claims, but promises delivery/progress lookup not available in the toolset |

All cases used the same chat agent/model and completed. Full answers, exact tool
responses/arguments, SQL/bindings/results, nested operation/query spans, persisted
messages and per-case trace IDs are private artifacts, not committed fixtures.
The canonical dataset was not changed; no placeholder values were invented.

These are execution observations and assisted review, not an overall quality
score. The requested full analysis is unsupported by the present chat toolset
for every case; q12/q13/q15 additionally require user-supplied entities. Scope
limits do not excuse unsupported factual claims in fallback responses.

Deterministic evaluation can establish stream termination/errors, persistence,
registered schema validity, observed source coverage, nonfinite query values,
empty evidence, and known calendar-window mismatches. Grounding, accurate
limitation disclosure, promised capabilities, relevance and report completeness
require semantic review. Actual market truth and full numerical correctness
remain unassessed without authoritative reference values and a stable data/date
context. Inspecting the same tool output is not an independent truth oracle.

`just ai-evaluate` now starts a dedicated loopback server using the unchanged
application. It disables inherited telemetry in that process, captures native
Pydantic AI spans plus public-operation and database-client spans, and runs each
case sequentially through HTTP. It does not override FastAPI dependencies, models,
tool results, prompts, schema or reasoning settings. Native streaming behavior and
normal token-accounting side effects remain. Query instrumentation observes actual
client calls; it does not execute extra evaluation SQL. Private local PostgreSQL
spans omit bind values; credentials/auth headers are never deliberately recorded.

## Evaluators and first quality baseline

`just ai-assess --directory ../.evaluation/RUN` reads stored native spans and
persisted HTTP/SSE evidence; it does not call any provider. It writes a private
assessment, a per-case Markdown report and a human-review template. Optional
`--reviews FILE` applies explicitly attributed `human` or `assisted_review`
judgments. Blank templates are not accepted as completed reviews. The initial
review is labeled **Codex-assisted trace inspection**, not human sign-off.

Dimensions remain independent: execution, persistence, trace coverage, tool
schema, tool execution, query execution, finite query values, evidence availability,
source-family coverage, recognized calendar-window mismatch, parameter binding,
capability support, grounding, completeness, capability disclosure, semantic
quality and numerical correctness. No overall score is computed. Source-family
coverage is not proof of correct query filters. Semantic quality remains unassessed
until a reviewer evaluates it. A standalone LLM judge is deliberately deferred:
these initial traces contain pervasive missing capabilities/evidence, and a second
model would not supply an authoritative numerical oracle. The attributed review
path supports adjudicating grounding and disclosure without an extra provider call.

`criteria.json` is separate from the unchanged canonical inputs. Its capability
assessment is tied to the observed registered tool/schema fingerprint and becomes
unassessed when that contract changes. Source mapping is explicit and conservative;
unknown tables do not get credit. Review it when new source implementations appear.
Adding q16 requires a dataset case and, when known, criteria; absent criteria yield
unassessed coverage rather than a fabricated pass.

`--references FILE` supports exact structured evidence, numeric ranges and entity/
record sets with an authoritative source label. It requires matching dataset digest,
data context and execution date. It cannot exact-match free-form answer text or
turn a tool-result check into a claim about answer correctness. Reference format:

```json
{
  "dataset_sha256": "SHA256_FROM_RUN",
  "data_context": "PINNED_SNAPSHOT_ID",
  "date": "YYYY-MM-DD",
  "cases": {
    "q01": [{"path": ["tools", 0, "result", "transaction_count"],
             "kind": "range", "min": 10, "max": 12,
             "source": "curated authoritative snapshot reference"}]
  }
}
```

The values above illustrate the schema; they are not canonical expected answers.
Use `kind: exact` with `expected`, or `kind: records` with an expected list.

`just ai-compare --baseline FILE --candidate FILE` rejects incompatible datasets,
parameters/questions, criteria, evaluator/reference versions, dates and data
contexts. It detects per-dimension pass-to-fail and lost-assessment regressions;
method changes are explicit. A removed assessment or a changed review method
cannot hide the loss of a previous pass. Exit codes: 0 comparable/no regression,
1 regression, 2 incomparable. The initial run uses a mutable local warehouse, so it intentionally
cannot establish a controlled model-versus-model quality comparison. A future run
must use an actual pinned snapshot and document it with `--data-context`; that
label is an operator assertion, not an automatic database snapshot mechanism.

Original deterministic baseline: all 15 executions/persistence checks pass;
strict reassessment now fails all 15 trace-coverage checks because an intermediate
Logfire FastAPI parent was not captured. Historical assessments that passed this
check are superseded, not deleted. The remaining original findings hold:
7 selected tool schemas pass; 5 queries contain nonfinite evidence; 2 observed
rolling-day queries cannot answer the requested complete calendar windows; 12
cases lack one or more required source families; all 15 full requested analyses
are unsupported by the current chat tool contract; 3 retain unresolved parameters.
Assisted review flags grounding risks in 11 answers. Numerical correctness remains
unassessed for all 15. These are per-dimension baseline findings, not 15 answer-quality passes.


## Corrected trace capture and repeated baseline

Export validation exposed a real capture defect in the first run: Logfire's
FastAPI instrumentation used a different provider, leaving an unrecorded parent
between our HTTP root and native agent/database spans. No spans were fabricated
or reparented. The dedicated server now uses Logfire's public
`additional_span_processors` hook to collect its spans locally too. The evaluator
explicitly rejects missing parent IDs. The original evidence and assessments are
retained; strict reassessment is `assessment-c2bb745ab7ebd1d1.json` in the first run.

The authorized rerun is `609e22b7-8a90-447f-bc17-a7fe7d39b3ab`, directory
`.evaluation/20260928T164246Z`. All 15 authenticated streams, persisted responses,
conversation deletions and complete trace-parent chains verified. Its 677 spans
contain 21 model requests, 25,226 input and 8,377 output tokens. Costs/downstream
vendor remain unknown. All original questions, including placeholders, were sent
unchanged. Agent names gained explicit metadata labels; prompts, reasoning,
registered schemas, business queries and token accounting stayed unchanged.

| Case | Actual selected tools | Repeated-run finding |
| --- | --- | --- |
| q01 | pulse twice: Jumeirah Village Circle and JVC, 90 days | Both empty; admits missing bracket data but asserts high current activity |
| q02 | area volume rankings, limit 5, 90 days; Business Bay pulse, 90 days | Rank result BUSINESS BAY has 1,805 transactions; differently cased pulse has 36. Answer mixes results and changes raw unit price 27,818 to 2,781; no building/bedroom ranking or calendar quarter |
| q03 | Downtown Dubai pulse, 180 days | Empty; asserts high activity, demand and stable prices without support |
| q04 | Jumeirah Lake Towers pulse, 90 days | Empty transaction evidence; rental volume/demand claims without rent retrieval |
| q05 | none | Unsupported current rental price, yield, volume and growth |
| q06 | none | Unsupported studio rent/yield/price-band claims; no monthly series |
| q07 | none | Discloses no delivery rankings but promises unavailable rental analysis |
| q08 | none | Discloses missing project audit data, then supplies unsupported price/yield/growth |
| q09 | none | Claims analysis and unit-type prices without listings evidence or counts |
| q10 | none | Unsupported aged/new listing prices and premium |
| q11 | none | Unsupported purchase/rent/yield inputs; illustrative arithmetic does not validate market facts |
| q12 | none | Ignores unbound building; unsupported appraisal and asking-rent recommendation |
| q13 | none | Ignores unbound building; names comps and prices without retrieval |
| q14 | none | Quantitative multi-source report without retrieval, using 90 days rather than last month |
| q15 | none | Requests project names and discloses missing developer documents; proposed benchmarking needs review |

Five pulse calls and one rank call occurred across four cases. Four pulse calls
returned empty aggregates in three cases; q02 retrieved nonempty results. No
similar-project call occurred and no rental/listing/delivery source was reached.
All six argument schemas pass; three cases fail finite-evidence checks; twelve
lack required source families; q02 deterministically fails the recognized calendar
predicate. All fifteen full requests remain unsupported; three retain unbound
parameters. Assisted trace review flags thirteen grounding failures, with q07/q15
unassessed for grounding. Numerical correctness remains unassessed throughout.

The repeat differs from the first run despite unchanged AI behavior configuration.
It is evidence of nondeterministic tool selection and response behavior, not a
controlled model improvement/regression experiment on an immutable warehouse.
The reviewed assessment is `assessment-ff5cd4fae83521b9.json`; the complete private
per-case report is `report-441c5ff7859e470f.md` in the corrected run directory.

## LangSmith integration and privacy boundary

Two separate adapters serve different purposes:

- `app/core/observability.py` provides optional production OpenTelemetry export.
  Native Pydantic AI spans include agent/model/tool identity without content;
  small wrappers add public operation, ClickHouse query hash/row count, PostgreSQL
  statement hash and actual direct CRM provider request spans. CRM early returns
  do not create fictitious model calls. HTTP roots encompass streaming completion.
- `scripts/evaluation/langsmith.py` publishes an already assessed local experiment
  through the official SDK. A versioned dataset contains case IDs and question/
  parameter digests, not the questions. Examples link to nested recorded runs and
  categorical per-dimension feedback. Each assessment revision gets distinct run
  IDs/project identity; repeated uploads resume without duplicating completed IDs.

Both construct allowlisted records from scratch. They exclude prompts, answers,
arguments, tool results, SQL text, rows, events, exception descriptions, HTTP
headers, user/organization IDs, arbitrary resources and SDK environment metadata.
Only trusted labels, hashes, random run IDs, timing, token counts, error flags,
source-system names and assessment statuses/methods leave the process. Unknown
model identifiers are hashed. Full source/query/result evidence stays local.
Remote run inputs/outputs are intentionally empty; use the private report to
inspect an actual answer. Remote reviewers cannot infer semantic correctness from
sanitized metadata alone. Hashes are correlation identifiers, not anonymization
of low-entropy content. No raw feedback rationale or reviewer identity is exported.

`AI_TELEMETRY_ENABLED=false` is the default. Explicitly enabling it requires a
locally configured `LANGSMITH_API_KEY`; optional endpoint/workspace settings support
the account's region/workspace. Environment-specific projects are
`vitevue-local`, `vitevue-staging` or `vitevue-production`. Production observation
uses one HTTP provider path to avoid orphan parent spans. The existing Logfire
path remains when this optional adapter is disabled. The dedicated benchmark
server disables every remote integration regardless of inherited flags/keys.

The dependency minimum matches SDK APIs used (`langsmith[otel]>=0.14.1`), with exact
versions in `uv.lock`. Offline tests exercise real SDK serialization and OTLP
encoding as well as nested run/example/feedback linkage. The real 15-case evidence
successfully generated `langsmith-preview.json` locally with `uploaded: false`.
No remote upload, account UI comparison or end-to-end ingestion is claimed: no
LangSmith credential is available. Configure that key locally, then use the
explicit sanitized upload command below to complete account-side validation.

## Developer workflow and continuous checks

```sh
just ai-inventory
just ai-benchmark
just test-evaluation
just ai-evaluate
just ai-assess --directory /absolute/private/run
# Review the generated template; record method, reviewer and evidence for each judgment.
just ai-assess --directory /absolute/private/run --reviews /absolute/private/reviews.json
just ai-compare --baseline /absolute/baseline.json --candidate /absolute/candidate.json
just ai-export --directory /absolute/private/run --assessment /absolute/private/assessment.json
# Explicit opt-in, after inspecting the sanitized local preview and configuring a key:
just ai-export --directory /absolute/private/run --assessment /absolute/private/assessment.json --upload
```

`ai-evaluate --case q12 --bindings /absolute/private/bindings.json` accepts a JSON
map such as `{"q12":{"building":"YOUR EXPLICIT BUILDING NAME"}}`. Supply actual
operator-selected entities; these illustrative words are not benchmark bindings.
All declared parameters for a bound case are required and validated before any
login/model call. Without a map entry the canonical template remains unbound.
Bound question/parameter fingerprints prevent accidental comparison to unbound
cases. `--directory` must be new; `--data-context` must identify a real pinned data
snapshot before a comparison can be treated as controlled. Changing the configured
model in `.env.dev` affects normal execution; record it and run a fresh experiment.

Local runs are ignored by Git, directory mode 0700 and evidence mode 0600. They
contain sensitive authorized data; delete runs when no longer needed under the
operator's retention policy. Do not commit or attach full reports/traces to public
issues. Conversation deletion does not reverse normal model usage accounting.
Known credential values, Bearer tokens and JWTs are scrubbed from local evidence,
including nested JSON-escaped forms; this does not make raw evidence public-safe.

`.github/workflows/ai-evaluation.yml` runs offline infrastructure, architecture,
agent and streaming tests plus lint with dummy settings and no model/telemetry
credentials. It never runs billed live benchmarks or uploads artifacts. CI checks
infrastructure regressions; a quality regression requires an explicit live run,
compatible evidence and reviewed dimensions. There is no unattended judge cost,
new database table, queue, endpoint, UI, or duplicated business logic.

## Final verification checkpoint

- All 15 canonical cases ran twice through real authenticated local HTTP and the
  configured OpenRouter model. The second run has complete trace ancestry; both
  retain exact inputs, outputs, actual tool/query evidence and cleanup outcomes.
- 321 fast backend tests passed, including all 35 evaluation tests, frozen
  contracts, authorization/streaming behavior and architecture boundaries. No
  tests were skipped. The new CI test selection also passed locally using only
  dummy configuration (53 tests at that checkpoint, before the final export-label
  test was added); hosted GitHub Actions itself has not run in this local task.
- 13 isolated PostgreSQL tests, 15 real ClickHouse tests, 68 frontend unit tests,
  frontend production build and all 37 Playwright tests passed. The first browser
  attempt could not load `libnspr4.so`; rerunning with the existing local Chromium
  library directory in `LD_LIBRARY_PATH` resolved the environment issue.
- Backend Ruff lint/format and `git diff --check` pass. An AST comparison against
  `9b3303f` verifies unchanged agent prompts/model settings/tool declarations and
  market business queries, excluding only the added agent-name metadata.
- Both evidence directories are Git-ignored and have verified 0700/0600 modes.
  The complete real-run metadata preview passes; comparing the two mutable local
  runs correctly exits 2 rather than pretending to be a controlled regression gate.
- Remote LangSmith ingestion/UI remains unverified without credentials. No raw
  content or sanitized preview was uploaded. Independent numerical correctness
  remains unassessed without authoritative references; assisted judgments are
  explicitly distinct from human sign-off.

During the evaluation stage, no frontend/API schemas, generated client, migration,
business query or Holocron code changed. Holocron/infrastructure-only suites and
the unchanged frontend broad type check were not rerun at that checkpoint; the migration's existing type-check limitations remain
recorded in `ARCHITECTURE.md` and are not claimed resolved here.


## Combined PR verification

The branch combines the migration and evaluation checkpoints on top of `VV`
(`86b924d`). The existing commits remain separate, reviewable stages in one PR.
Wrap-up review fixed a regression-gate edge case: removing a passing assessment
or changing its method could previously hide the loss of that pass. Parameterized
tests reproduced both failures before the fix; method changes now remain visible
alongside status regressions. No model execution or business behavior changed.

Fresh verification on the combined implementation:

- 323 fast backend tests, including 37 evaluation tests, architectural boundaries,
  frozen contracts, auth/streaming behavior and Bun workbook parity; no skips.
- 56 offline CI-selection tests with dummy-only configuration.
- 13 PostgreSQL, 15 ClickHouse and 2 Redis/MinIO integration tests.
- 469 Holocron tests; full Holocron Ruff lint; format/type checks on both changed
  Holocron files. One existing helper-class collection warning remains.
- 68 frontend unit tests, production build, 37 Playwright tests and 10 guard tests.
- Backend Ruff lint/format, diff whitespace checks and applicable commit hooks.
  The frontend hook has no changed frontend source files to check.

The broader checks were rerun and reproduce the documented baseline limitations:
frontend type checking has 8 errors in 2 unchanged listings files; Holocron's
broad format check finds 14 unchanged files and its broad type check reports 62
diagnostics. These are not claimed passing or fixed. Existing live benchmark
artifacts were retained without another billed model run. LangSmith ingestion
still requires account credentials; raw execution evidence remains private and
excluded from the PR. The migration repair and its manual-backfill/downgrade
limitations remain documented in `ARCHITECTURE.md` and the migration itself.

## Database capability coverage, 2026-10-03

Three chat tools now call public domain operations that the API also serves:
`query_market_metrics` → `analytics/market_metrics.py` (`POST /api/v1/analytics/market-metrics`),
`get_project_delivery_summary` → `project/delivery.py` (`GET /api/v1/projects/delivery-summary`),
`get_listing_inventory` → the existing `listing_analytics_from_clickhouse`. They are local Pydantic AI
tools; no MCP client or server is connected. Results carry the resolved Dubai calendar period,
coverage (`complete`, `partial`, `unavailable`), eligible count, metric definitions, matched DLD names,
source/match-status counts, release build time and limitations. Real ClickHouse tests check
hand-computed values through the operation, the HTTP route and the registered chat agent.

The pulse tool no longer counts mortgages and gifts as sales and now reports real square feet
(it labelled square metres as sqft). Area ranking excludes non-sales.

| ID | Supported by | Remaining gap and its kind |
| --- | --- | --- |
| q01 | metrics: sales, JVC, apartment, `rolling_days` 90, `group_by=price_band` [1m, 2m] | None for counts/shares. |
| q02 | metrics: sales, Business Bay, bedrooms 2, `previous_quarter`, `group_by=building`, median AED and AED/sqft | Building names: DLD open data names fewer buildings than DXBI-matched rows (data). |
| q03 | metrics: sales, Downtown Dubai, apartment, 1 bed, p25/p75 | Downtown rows without a master project resolve to DLD area Burj Khalifa (data granularity, disclosed). |
| q04 | metrics: rentals, JLT, bedrooms 2, `rolling_days` 90, `group_by=contract_version` | Percentage difference is arithmetic on two returned medians. DLD open-data rentals carry no bedrooms; only DXBI-matched or Ejari "Studio" rows qualify, and the count excluded is reported (data). JLT rentals locate only to DLD area Al Thanyah Fifth (data). |
| q05 | metrics: rentals, Dubai Marina, `min_term_days` 367, annual versus whole-term amounts | Aggregates only; no contract-level list tool (functionality). |
| q06 | metrics: rentals, Business Bay, studio, `previous_months` 6, `group_by=month` | Same bedroom coverage limit as q04 (data). Uncovered months are null, empty months 0. |
| q07 | delivery: Dubai South, `scheduled_in_horizon`, 24 months, by developer | Raw DLD developer names; brand grouping is not applied (decision). |
| q08 | delivery: JVC, by delivery status | None for counts; DLD register only. |
| q09 | listings: sale, Dubai Hills Estate, one call per bedroom | Medians are t-digest approximations; asking prices from the latest Property Finder snapshot. |
| q10 | — | No listing-age split or per-listing comparison tool (functionality). |
| q11 | — | No listing-to-registered-rent comparable join exposed to chat (functionality). |
| q12–q13 | Partly: rental/sales metrics by building, listing inventory | Unbound `[building]` parameter; rentals name projects rather than buildings in DLD open data (data); no report composer (functionality). |
| q14 | Partly: last-month sales and rentals, delivery, listing inventory | Report composition and month-over-month narrative are not a tool (functionality); change is withheld when a compared month is partial. |
| q15 | Partly: delivery dates/units | Unbound project names; payment plans and unit prices need Reelly producers (F02, data); construction progress is DLD `percent_completed` only. |

Bayut: the collector writes raw JSONL evidence only. There is no ClickHouse table, loader, serving
view or backend consumer, and runs stop on CAPTCHA by design. Bayut inventory is therefore
unavailable to every capability above until a licensed, validated bronze-to-serving path exists.