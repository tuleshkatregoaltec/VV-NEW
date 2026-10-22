"""Report what the uniform DXBI-primary market layer changed, as Markdown.

Compares a frozen copy of the served sales/rentals tables taken before the change with the
current release: totals and value per year, which source supplies each row, how many values
DXBI replaced or DLD filled, and the vocabulary each page now shows. Aggregates only; no row
leaves the warehouse.

    uv run python scripts/report_uniform_layer_changes.py \\
        --before-sales uniform_baseline_sale_events --before-rentals uniform_baseline_rental_events \\
        --out ../docs/data-pipelines/UNIFORM_LAYER_CHANGES.md
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from holocron.market.transforms import RENTALS_DXBI_FROM, SALES_DXBI_FROM
from holocron.platform.clickhouse import ClickHouseClient
from holocron.platform.settings import Settings

_IDENT = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _table(name: str) -> str:
    if not name or set(name) - _IDENT:
        raise ValueError(f"not a table name: {name!r}")
    return f"`{name}`"


def _rows(client: ClickHouseClient, sql: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in client.query(sql).result_rows]


def _n(value: Any) -> str:
    return f"{int(value):,}" if value is not None else "–"


def _pct(new: Any, old: Any) -> str:
    if not old:
        return "–"
    return f"{(float(new) - float(old)) / float(old) * 100:+.2f}%"


def _share(part: Any, whole: Any) -> str:
    return f"{float(part) / float(whole) * 100:.1f}%" if whole else "–"


def _table_md(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _yearly(client, *, before: str, after: str, date: str, value: str, where: str, since: int):
    sql = f"""
        SELECT y, b.n, a.n, b.v, a.v FROM
            (SELECT toYear({date}) AS y, count() AS n, sum({value}) AS v
             FROM {before} WHERE is_served = 1 AND {where} GROUP BY y) AS b
        FULL OUTER JOIN
            (SELECT toYear({date}) AS y, count() AS n, sum({value}) AS v
             FROM {after} WHERE is_served = 1 AND {where} GROUP BY y) AS a USING y
        WHERE y >= {since} AND y <= toYear(today())
        ORDER BY y"""
    out = []
    for year, b_n, a_n, b_v, a_v in _rows(client, sql):
        out.append(
            [str(year), _n(b_n), _n(a_n), _n(a_n - b_n), _pct(a_n, b_n), _n(b_v), _n(a_v),
             _pct(a_v, b_v)]
        )  # fmt: skip
    return _table_md(
        ["Year", "Records before", "Records after", "Change", "Change %", "Value before (AED)",
         "Value after (AED)", "Value change %"],
        out,
    )  # fmt: skip


def _labels(client, *, table: str, where: str) -> str:
    rows = _rows(
        client,
        f"SELECT match_status, is_served, count() FROM {table} WHERE {where} "
        "GROUP BY match_status, is_served ORDER BY is_served DESC, count() DESC",
    )
    total = sum(r[2] for r in rows if r[1] == 1)
    meaning = {
        "matched": "DXBI and DLD records of the same event, one row",
        "dxbi_only": "only DXBI has it",
        "dxbi_preferred": "DXBI record served; its probable DLD duplicate hidden",
        "dld_only": "only DLD has it (DLD fills the gap)",
        "dld_probable_duplicate": "hidden: DLD copy of a served DXBI record",
        "dxbi_probable_duplicate": "hidden: DXBI copy of a served record",
    }
    return _table_md(
        ["Hidden label", "Served", "Rows", "Share of served", "Meaning"],
        [
            [status, "yes" if served else "no", _n(n), _share(n, total) if served else "–",
             meaning.get(status, "")]
            for status, served, n in rows
        ],
    )  # fmt: skip


def _value_changes(client, *, table: str, where: str) -> str:
    cond = f"WHERE is_served = 1 AND match_status = 'matched' AND {where}"
    (pairs,) = _rows(client, f"SELECT count() FROM {table} {cond}")[0]

    def per_column(array: str) -> dict:
        return dict(
            _rows(
                client,
                f"SELECT c, count() FROM {table} ARRAY JOIN {array} AS c {cond} GROUP BY c",
            )
        )

    replaced, filled = per_column("dld_disagrees"), per_column("filled_from_dld")
    columns = sorted(set(replaced) | set(filled))
    rows = [
        [column, _n(replaced.get(column, 0)), _share(replaced.get(column, 0), pairs),
         _n(filled.get(column, 0)), _share(filled.get(column, 0), pairs)]
        for column in columns
    ]  # fmt: skip
    return f"Matched rows in the DXBI period: **{_n(pairs)}**.\n\n" + _table_md(
        ["Column", "DXBI value replaced DLD's", "% of matched", "DLD filled empty DXBI", "% of matched"],
        rows,
    )  # fmt: skip


def _distribution(client, *, table: str, column: str, where: str, limit: int = 15) -> list:
    return _rows(
        client,
        f"SELECT {column}, count() FROM {table} WHERE is_served = 1 AND {where} "
        f"GROUP BY {column} ORDER BY count() DESC LIMIT {limit}",
    )


def build(client: ClickHouseClient, before_sales: str, before_rentals: str) -> str:
    bs, br = _table(before_sales), _table(before_rentals)
    s, r = "`market_sale_events`", "`market_rental_events`"
    sale_period = f"instance_date >= '{SALES_DXBI_FROM}'"
    rent_period = f"contract_start_date >= '{RENTALS_DXBI_FROM}'"
    totals = _rows(
        client,
        f"""SELECT
            (SELECT countIf(is_served = 1 AND trans_group_en = 'Sales') FROM {bs}),
            (SELECT countIf(is_served = 1 AND trans_group_en = 'Sales') FROM {s}),
            (SELECT countIf(is_served = 1) FROM {br}),
            (SELECT countIf(is_served = 1) FROM {r}),
            (SELECT countIf(is_served = 1 AND trans_group_en = 'Sales' AND {sale_period}
                            AND primary_source = 'dxbi') FROM {s}),
            (SELECT countIf(is_served = 1 AND trans_group_en = 'Sales' AND {sale_period}) FROM {s}),
            (SELECT countIf(is_served = 1 AND {rent_period} AND primary_source = 'dxbi') FROM {r}),
            (SELECT countIf(is_served = 1 AND {rent_period}) FROM {r})""",
    )[0]
    b_sales, a_sales, b_rent, a_rent, dx_sales, period_sales, dx_rent, period_rent = totals
    releases = dict(
        _rows(
            client,
            "SELECT dataset, release_id FROM market_release_ledger FINAL WHERE state = 'published'",
        )  # fmt: skip
    )
    date_shift = _rows(
        client,
        f"""SELECT dateDiff('day', toDate(dld_instance_date), toDate(instance_date)) AS d,
                   count() FROM {s}
            WHERE is_served = 1 AND match_status = 'matched' AND {sale_period}
              AND dld_instance_date IS NOT NULL
            GROUP BY d ORDER BY count() DESC LIMIT 5""",
    )
    shift_total = sum(n for _, n in date_shift)
    types_before = _distribution(client, table=bs, column="property_type_en",
                                 where="trans_group_en = 'Sales'")  # fmt: skip
    types_after = _distribution(client, table=s, column="property_type",
                                where="trans_group_en = 'Sales'")  # fmt: skip
    rtypes_before = _distribution(client, table=br, column="ejari_property_type_en", where="1")
    rtypes_after = _distribution(client, table=r, column="property_type", where="1")
    names = _rows(
        client,
        f"""SELECT
            (SELECT uniqExact(area_name_en) FROM {bs} WHERE is_served = 1),
            (SELECT uniqExact(location_name) FROM {s} WHERE is_served = 1),
            (SELECT uniqExact(area_name_en) FROM {br} WHERE is_served = 1),
            (SELECT uniqExact(location_name) FROM {r} WHERE is_served = 1),
            (SELECT countIf(rooms_en != '') / count() FROM {br} WHERE is_served = 1),
            (SELECT countIf(rooms_en != '') / count() FROM {r} WHERE is_served = 1),
            (SELECT countIf(rooms_en != '') / count() FROM {bs}
             WHERE is_served = 1 AND trans_group_en = 'Sales'),
            (SELECT countIf(rooms_en != '') / count() FROM {s}
             WHERE is_served = 1 AND trans_group_en = 'Sales')""",
    )[0]

    def pairs(before: list, after: list) -> str:
        width = max(len(before), len(after))
        return _table_md(
            ["Before (DLD words)", "Records", "After (uniform list)", "Records"],
            [
                [*(map(str, (before[i][0] or "(blank)", _n(before[i][1]))) if i < len(before)
                   else ("", "")),
                 *(map(str, (after[i][0] or "(blank)", _n(after[i][1]))) if i < len(after)
                   else ("", ""))]
                for i in range(width)
            ],
        )  # fmt: skip

    multi_unit = _rows(
        client,
        f"""SELECT toYear(contract_start_date) AS y, countIf(no_of_prop > 1), count(),
                   sumIf(annual_amount, no_of_prop > 1) / sum(annual_amount)
            FROM {r} WHERE is_served = 1 AND match_status = 'dld_only'
              AND contract_start_date >= '2021-01-01' AND contract_start_date <= today()
            GROUP BY y ORDER BY y""",
    )
    tier2 = _rows(
        client,
        f"""SELECT count(), countIf(b.no_of_prop > 1), sum(b.annual_amount), sum(a.annual_amount)
            FROM {r} AS a
            INNER JOIN {br} AS b ON b.row_key = a.row_key
            INNER JOIN market_rental_matches AS m ON m.dxbi_contract_key = a.dxbi_contract_key
            WHERE a.match_status = 'matched' AND m.match_tier = 2
              AND {rent_period.replace("contract_start_date", "a.contract_start_date")}""",
    )[0]
    land_villa = _rows(
        client,
        f"""SELECT count(), countIf(procedure_name_en = 'Delayed Sell') FROM {s}
            WHERE is_served = 1 AND match_status = 'matched' AND property_type_en = 'Land'
              AND property_type = 'Villa'""",
    )[0]
    findings = f"""## Data errors found while building the layer

| Finding | Evidence | Handling |
|---|---|---|
| DLD repeats a multi-unit rental contract's whole rent on every unit line | Matched tier-2 rentals (same lease and floor area): {_n(tier2[0])} rows, {_n(tier2[1])} multi-unit; DLD rent on them AED {_n(tier2[2])} vs DXBI's per-unit AED {_n(tier2[3])} | Each DLD row now carries its unit's share (contract rent / units; decided 2026-10-06), and DXBI's own per-unit rent wins where DXBI holds the unit. This is most of the rental value change above: the old totals counted each contract's rent once per unit. |
| Off-plan villas registered by DLD against the plot ("Land") | {_n(land_villa[0])} matched sales typed Land by DLD and Villa by DXBI ({_n(land_villa[1])} of them "Delayed Sell") | DXBI's Villa wins; sales typed Plot fall and Villa rises accordingly. |
| DXBI dates 21% of matched sales one business day before DLD registration | table above | DXBI's date served; DLD's kept hidden for validation. |
| DLD's bedroom column holds type words ('Office', 'Shop', 'NA', 'PENTHOUSE') | sales bedroom coverage before counts them | Treated as unknown bedrooms (coverage drops from the "before" figure for that reason). |
| DLD rentals carry no bedroom count; DXBI-only rentals lost theirs | rentals with a bedroom label: 0.3% before | DXBI's bedrooms used; DXBI-only rows fixed. |
| Property types in different words per source; DLD hides the type in a sub-type ("Unit" / "Labor Camps") | type tables above | One list in DXBI's words; generic or blank values filled from the matched record. |
| One place spelled several ways ('DAMAC HILLS 2' / 'Damac Hills 2', 'Dubai Land' / 'Dubailand', all-capitals DLD labels) | 7 colliding names before | One spelling per place (DXBI's), all-capitals DLD labels title-cased. |
| Analytics read DLD `actual_area` as sales size (0 on 91% of sales) and DLD "Unit" (offices, shops) as residential | backend queries | Pages read the uniform size and type columns. |
| Transactions rentals "last N days" included leases starting in the future | 164 of 34,170 JVC residential leases (365 days) | Window bounded at today, as Analytics already was; the two pages now agree. |
| Summary medians were sampled (changed between refreshes) | 70.79 vs 70.61 m² on identical requests | Exact medians on the summaries. |

### DLD-only multi-unit rentals (rent now split per unit)

{_table_md(["Year", "Multi-unit DLD rows", "All DLD-only rows", "Share of DLD-only annual value"], [[str(y), _n(m), _n(n), f"{float(v) * 100:.1f}%"] for y, m, n, v in multi_unit])}

Before the split, these lines carried most of DLD's annual rent value although they are a
fifth of the rows. Splitting keeps every contract's total (its rows add up to its rent) and
gives each unit a realistic rent.
"""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# Uniform market layer: what changed

Generated {now} by `holocron/scripts/report_uniform_layer_changes.py` on the local warehouse
copy (handoff snapshot). Before = frozen copy of the release built with the code on `VV` before
this change (`{before_sales}`, `{before_rentals}`); after = the published uniform release
(sales `{releases.get("market_sale_events", "?")}`, rentals
`{releases.get("market_rental_events", "?")}`). These are local pre-production numbers; re-run
the script on staging/production after deployment. Aggregates only.

## Rules (decided 2026-10-06)

- **DXBI is the primary source** from {SALES_DXBI_FROM} (sales) and {RENTALS_DXBI_FROM} (rentals).
  On a matched pair DXBI's value wins whenever DXBI has one; DLD fills empty or generic values
  and registry-only fields (procedure, registration type, parties, Arabic names).
- **DLD fills the gaps**: records only DLD holds are still served. Before those dates DLD is the
  row source and DXBI only fills DLD's empty values.
- **No double counting**: an unmatched DXBI record replaces its probable DLD duplicate one for
  one; a DXBI record whose DLD counterpart already stands for another DXBI record stays hidden.
- **Hidden source label** (`match_status`, `primary_source`, `filled_from_dld`,
  `dld_disagrees`): kept for audit, never displayed in the product.
- **One vocabulary**: property type (DXBI's words), bedrooms (`Studio`, `N B/R`), location
  (DXBI's community names, one spelling), size in m², rentals dated by contract start.

## Totals

| Dataset | Served before | Served after | Change | DXBI-primary rows in its period |
|---|---:|---:|---:|---:|
| Sales | {_n(b_sales)} | {_n(a_sales)} | {_n(a_sales - b_sales)} ({_pct(a_sales, b_sales)}) | {_n(dx_sales)} of {_n(period_sales)} ({_share(dx_sales, period_sales)}) |
| Rentals | {_n(b_rent)} | {_n(a_rent)} | {_n(a_rent - b_rent)} ({_pct(a_rent, b_rent)}) | {_n(dx_rent)} of {_n(period_rent)} ({_share(dx_rent, period_rent)}) |

Served record counts are unchanged by design: every swap is one DXBI record for one DLD
record. What changes is which source supplies each record and its values.

### Sales by year (Sales group only, by served date)

{_yearly(client, before=bs, after=s, date="instance_date", value="actual_worth", where="trans_group_en = 'Sales'", since=2018)}

### Rentals by year (by contract start, annual rent)

{_yearly(client, before=br, after=r, date="contract_start_date", value="annual_amount", where="1", since=2021)}

Record counts do not move. Sales value moves only where DXBI's price or date replaces DLD's.
Rental value falls because a multi-unit contract's rent was counted once per unit (see "Data
errors found"): each row now carries its unit's share, and DXBI's per-unit rent where it
holds the unit.

## Which source supplies each record (hidden label)

### Sales, DXBI period (from {SALES_DXBI_FROM})

{_labels(client, table=s, where=sale_period)}

### Rentals, DXBI period (from {RENTALS_DXBI_FROM})

{_labels(client, table=r, where=rent_period)}

## Values DXBI replaced and gaps DLD filled (matched records, DXBI period)

### Sales

{_value_changes(client, table=s, where=sale_period)}

### Rentals

{_value_changes(client, table=r, where=rent_period)}

### Sales dates

DXBI dates a share of matched sales one business day before DLD registers them (a Monday in
DLD is the previous Friday in DXBI). The served date is DXBI's; DLD's registration date is kept
in the hidden `dld_instance_date` column, and release validation checks completeness per day
on it.

{_table_md(["DXBI date minus DLD date (days)", "Matched sales", "Share"], [[str(d), _n(n), _share(n, shift_total)] for d, n in date_shift])}

## Vocabulary shown on Transactions and Analytics

### Sales property type

{pairs(types_before, types_after)}

### Rental property type

{pairs(rtypes_before, rtypes_after)}

### Locations and bedrooms

| Measure | Before | After |
|---|---:|---:|
| Distinct sales location names | {_n(names[0])} | {_n(names[1])} |
| Distinct rental location names | {_n(names[2])} | {_n(names[3])} |
| Sales with a bedroom label | {float(names[6]) * 100:.1f}% | {float(names[7]) * 100:.1f}% |
| Rentals with a bedroom label | {float(names[4]) * 100:.1f}% | {float(names[5]) * 100:.1f}% |

Before, the location list mixed DLD's official districts and open-data labels (`DUBAI MARINA`,
`Marsa Dubai`); after, it uses DXBI's community names with one spelling per place (`Damac
Hills 2`/`DAMAC HILLS 2`, `Dubailand`/`Dubai Land` merged), DLD names only where DXBI has none.

{findings}"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-sales", required=True)
    parser.add_argument("--before-rentals", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = build(ClickHouseClient(Settings()), args.before_sales, args.before_rentals)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
