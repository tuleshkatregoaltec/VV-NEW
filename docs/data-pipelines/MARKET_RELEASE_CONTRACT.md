# Market release contract for database-grounded chat (PR #13)

**Status:** code ready in this stack (`feat/market-release-pinning`), tested against real
ClickHouse. Not wired into PR #13. That PR owns `backend/app/market/evidence.py`, and this
stack does not edit chat/MCP files. The adapter below is the only change it needs.

## What the consumer gets

`app.market.releases.pin_market_release(sources=("sales", "rentals"))` is an async context
manager that yields a `PinnedRelease`:

| Field | Meaning |
|---|---|
| `release_id` | One identity for the pin, e.g. `rentals=486bdd75ed70;sales=c46f60eaae0a`. It is built from Holocron release ids, which are immutable, not from timestamps. |
| `sources` | Sources with a published immutable version: `sales`, `rentals`. `listings` and `projects` have no releases yet, so they are absent and stay unavailable for cross-source conclusions. |
| `missing` | Requested sources without a published version. |
| `coverage()` | `(source, start, end, status)` day ranges from that release's ledger entry. `complete` means every day was read to the end of its source window by a recorded load (`source_load_windows`) or is an attested archive. `partial` means the load stopped inside the window. Days absent from the ranges are unverified. |
| `acquired_at`, `expires_at` | The pin lasts at most `MAX_LEASE` (30 minutes). |

Adapter for PR #13 (its `MarketRelease` / `SourceCoverage` shapes are unchanged):

```python
from app.market.releases import pin_market_release

@asynccontextmanager
async def acquire_market_release(context: MarketAccessContext):
    async with pin_market_release() as pinned:
        if not pinned.sources:
            yield None  # nothing published yet: stay in the "unavailable" path
            return
        yield MarketRelease(
            release_id=pinned.release_id,
            sources=pinned.sources,
            coverage=tuple(SourceCoverage(*item) for item in pinned.coverage()),
        )
```

## How reads stay pinned for the whole turn

1. Holocron publishes a release only after validation (`holocron.market.release`). Just before
   its events table goes live, the staged table is also kept as
   `market_sale_events__r<release_id>` / `market_rental_events__r<release_id>`. This is a
   hard-link `ATTACH PARTITION … FROM` of the staged parts (0.15 s for 10.8M rows, no data
   copy), and nothing ever writes to it again.
2. `pin_market_release` reads, once, the `published` entry of each source in
   `market_release_ledger` and checks that its `__r` table exists. The pin is a `ContextVar`,
   so it follows the turn's tasks and does not leak into other requests.
3. While the pin is active, every query in `app.analytics.market_metric_queries` (totals,
   groupings, comparisons, location matches, source breakdowns, release identity) reads
   `(SELECT * FROM market_*_events__r<id> WHERE is_served = 1)` instead of the mutable
   `ch_transactions` / `ch_rent_contracts` views. A source that is requested but not pinned
   raises `ReleaseUnavailable`. It never falls back to the views. After `expires_at`, queries
   raise `ReleaseLeaseExpired`.
4. Outside a pin, the API keeps serving the current release. The before/after release-id check
   in `get_market_metrics` remains as a safety net for unpinned calls only.

The test `test_a_pinned_operation_reads_one_immutable_release_throughout` publishes a new
release with an extra sale in the middle of a pinned operation. Both answers inside the pin
come from the old release, and a new pin sees the new one.

## How coverage is established

- Each DLD open-data bronze load records every date window it replaced
  (`source_load_windows`), using its page-file evidence: `complete` when pagination reached the
  end of the result set (including empty windows), `partial` when it stopped inside the
  window. Archives without page evidence (Dubai Pulse history) are recorded explicitly with
  `python -m holocron.platform.load_windows attest TABLE DATE_FIELD FROM TO EVIDENCE`.
- When a release starts building, it computes day ranges from the windows loaded before that
  moment. The latest window covering a day decides that day. The release stores the ranges in
  its ledger entry (`coverage_json`), and they never change afterwards.
- Coverage is per day on the date each dataset counts by: sales by `instance_date`, rentals by
  `registration_date`. Min/max event dates are never used as coverage.
- Existing data loaded before this stack has no windows and therefore reports as unverified,
  until it is re-loaded, replayed from raw manifests, or its archive is attested.

## How retained versions are released and cleaned up

There are no lease rows to forget or clean up. Safety comes from two bounds:

- a pin lasts at most `MAX_LEASE` = 30 minutes (backend `app.market.releases`);
- Holocron drops a superseded or rolled-back version only `RELEASE_RETENTION` = 24 hours after
  its ledger entry changed (`holocron.market.release.cleanup_versions`). The published version
  and the live table's release are never dropped.

A pin is taken only on a version that is published at that moment, so the version outlives
every pin on it by at least 23.5 hours. Versions of releases that never went live (failed,
rejected, interrupted) were never pinnable and are dropped at once. Cleanup runs under the
market release lock after every publication and in
`python -m holocron.market.release recover`. Rollback recreates the restored release's version
if it has already aged out. The two constants must stay equal across both packages: Holocron
`MAX_READER_LEASE`, backend `MAX_LEASE`. A Holocron test asserts that retention is more than
10× the lease.

## Deployment requirements

- The backend reader needs `SELECT` on `market_release_ledger` and on
  `market_sale_events__r*` / `market_rental_events__r*` (a wildcard grant or database-level
  `SELECT`), plus `system.tables` (visible to readers by default). It needs no DDL.
- After the schema adds `release_id` and the geography columns, recreate the serving views
  (`create_compatibility_views`), because ClickHouse expands `SELECT *` when a view is created.
- Storage: versions share parts with the live table through hard links. Space grows only as
  merges rewrite parts, for at most 24 hours of superseded releases.

## Limits

- Sources other than sales and rentals (listings, projects, DXBI evidence detail) have no
  immutable versions yet. Cross-source answers involving them stay unavailable.
- The pinned set pairs the current sales and rentals releases. The rentals entry records the
  sales release its area learning used (`depends_on_json`); the two are not forced to come from
  one publication run.
- Clock skew between the API and ClickHouse hosts must stay far below the 23.5-hour margin.
