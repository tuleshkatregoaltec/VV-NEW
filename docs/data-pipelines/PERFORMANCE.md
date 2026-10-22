# Pipeline performance measurements

Local measurements on 2026-10-03. They show where time goes and justify specific changes.
They are not production performance claims: production ClickHouse Cloud sizing, concurrency
and p95 under load are still to be measured in staging (Prompt 09/12).

## Setup

- ClickHouse 24.8.14 in Docker Desktop on a Windows/WSL2 laptop, shared with other work.
- `vv_bench`: a disposable database with copies of the local warehouse's
  `dld_od_transactions_bronze` (1,802,749 rows), `dld_od_rents_bronze` (10,493,181 rows, 2.08
  GiB), `dxbi_sales_unit_events` (986,930) and `dxbi_rental_events` (1,632,474). Copying took
  12 s, 58 s, 2 s and 6 s.
- Scripts: a timing wrapper around `holocron.market.release` that logs every statement over
  0.5 s, and a curl loop for the delete comparison. Both sit in the session scratchpad.
  They are not repository tooling.

## Bronze window replace (largest demonstrated bottleneck, fixed)

Every daily DLD load replaces its date window. The rents incremental replaces 14 days of
registrations in a 10.5M-row table. That used `ALTER TABLE … DELETE … mutations_sync = 1`, a
mutation that rewrites every part containing a matching row.

| Replace 2026-09-06..19 (48,916 rows) on fresh, merged copies | Run 1 | Run 2 |
|---|---|---|
| `ALTER TABLE … DELETE` (mutation, previous code) | 62.1 s | 117.8 s |
| `DELETE FROM … SETTINGS lightweight_deletes_sync = 2` (new) | 0.20 s | 0.33 s |

Both leave 10,444,265 rows. Run 2 overlapped other benchmark work, which explains its spread.
`holocron.platform.bronze.replace_where_rows` / `replace_run_rows` (all bronze loaders) now use
the lightweight delete. The masked rows are physically removed at the next merge.

## Canonical release rebuild (measured, not changed)

| Release (full local data) | Total | Largest statements |
|---|---|---|
| Sales (1,935,650 rows; 1,829,392 served) | 55.5–69.8 s | DXBI match ledger 25–28 s; DLD events insert 17–33 s; OPTIMIZE FINAL 3 s |
| Rentals (10,816,328 rows; 10,334,201 served) | 80.6–82.7 s | DLD events insert 33–36 s; OPTIMIZE FINAL 19–25 s; conservation check 5–6.5 s |

Canonical geography adds one join per crosswalk kind. Sales went from 69.8 s on the first
geography build to 55.5 s after the crosswalk changes. That is the same order as the census's
107.7 s for `market_events_refresh` before this work. A release's immutable version
(`__r<id>`, for pinned readers) is a hard-link attach: 0.15 s for 10.8M rental rows.

Serving every line of multi-line DLD transaction numbers (correctness fix, see
`DLD_SALES_BASE`) raised the sales release to 80.9 s (DXBI match ledger 46 s, events 21 s)
for 2,875 more served lines (1,832,267). It is accepted for correctness. The latest-extract
`IN` subquery is the first thing to optimise if the daily budget needs it.

Candidates not changed for lack of a demonstrated problem: `OPTIMIZE FINAL` on staged rentals
(19–25 s, needed so ordered LIMIT queries do not read dozens of parts), and the DXBI sale
match ledger's 61-day price-probe join.

## Not measured here

API p50/p95/p99 under concurrent load, PostgreSQL, provider request throughput (needs live
credentials and quotas, D1/D2), and ClickHouse Cloud.
