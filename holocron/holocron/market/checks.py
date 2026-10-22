"""Reconciliation checks for the canonical market layer.

Each check is one SQL query returning a number and a threshold. Thresholds come from the
measurements in docs/data-pipelines/DXBI_RECONCILIATION.md (§6.3) and the first builds of
this layer; a failing check means the data moved outside what has been observed as healthy.
"""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass

from holocron.market.transforms import (
    DLD_RENTS_BASE,
    DLD_SALES_BASE,
    DXBI_SALES_BASE,
    PROPERTY_TYPES,
)
from holocron.contracts import ClickHouseQuery

_OPS = {"==": operator.eq, "<=": operator.le, ">=": operator.ge}


@dataclass(frozen=True, slots=True)
class MarketCheck:
    name: str
    asset: str
    description: str
    sql: str
    op: str
    threshold: float
    # Blocking checks are evaluated on the staged release and stop publication. Source-health
    # signals (DXBI lag, coverage, match rate) stay as post-publication asset checks: a
    # lagging DXBI feed must not freeze fresh DLD data.
    blocking: bool = True
    # Measures DXBI evidence: with no DXBI input loaded at all the ratio has nothing to
    # measure and is recorded as not applicable. Unmeasurable with DXBI input, it fails.
    needs_dxbi: bool = False


def evaluate(clickhouse: ClickHouseQuery, check: MarketCheck) -> tuple[bool, float]:
    """Run the check query; no value (nothing to measure, e.g. no DXBI rows) fails."""
    raw = clickhouse.query(check.sql).result_rows[0][0]
    if raw is None:
        return False, float("nan")
    value = float(raw)
    return _OPS[check.op](value, check.threshold), value


_DXBI_INPUT = {
    "market_sale_events": "dxbi_sales_unit_events",
    "market_rental_events": "dxbi_rental_events",
}


def dxbi_input_rows(clickhouse: ClickHouseQuery, asset: str) -> int:
    return int(clickhouse.query(f"SELECT count() FROM {_DXBI_INPUT[asset]}").result_rows[0][0])


def outcome(clickhouse: ClickHouseQuery, check: MarketCheck) -> tuple[str, float]:
    """passed / failed / not_applicable for a published-table check."""
    passed, value = evaluate(clickhouse, check)
    if math.isnan(value) and check.needs_dxbi and not dxbi_input_rows(clickhouse, check.asset):
        return "not_applicable", value
    return ("passed" if passed else "failed"), value


_SALES = "market_sale_events"
_RENTALS = "market_rental_events"
# Months with complete data: from 2022 up to, not including, the latest DLD sale month.
_SALE_MONTHLY_COVERAGE = f"""
SELECT min(dxbi / dld) FROM (
    SELECT toStartOfMonth(coalesce(dxbi_transaction_date, toDate(instance_date))) AS m,
           countIf(dxbi_sale_key != '') AS dxbi,
           countIf(source_system != 'dxbi' AND trans_group_en = 'Sales') AS dld
    FROM {_SALES}
    WHERE coalesce(dxbi_transaction_date, toDate(instance_date)) >= '2022-01-01'
    GROUP BY m
    HAVING m < (SELECT toStartOfMonth(max(instance_date)) FROM {_SALES}
                WHERE source_system != 'dxbi' AND trans_group_en = 'Sales')
       AND dld > 0
)"""


def _off_vocabulary_share(table: str) -> str:
    # No DXBI-only rows means nothing off-vocabulary is served: 0, not an unmeasurable 0/0.
    return f"""
SELECT if(count() = 0, 0, countIf(area_name_en NOT IN (
           SELECT DISTINCT area_name_en FROM {table} WHERE source_system != 'dxbi'))
       / count())
FROM {table}
WHERE match_status = 'dxbi_only'"""


def _uniform_vocabulary(table: str) -> str:
    """Served rows whose uniform type or bedroom label is outside the published vocabulary."""
    types = ", ".join(f"'{name}'" for name in PROPERTY_TYPES)
    return (
        f"SELECT countIf(property_type NOT IN ({types}, ''))"
        " + countIf(NOT match(rooms_en, '^([0-9]+ B/R|Studio)?$'))"
        f" FROM {table} WHERE is_served = 1"
    )


def _uniform_checks(prefix: str, table: str, matches: str, loose_key: str, empty: str) -> tuple:
    return (
        MarketCheck(
            name=f"{prefix}_uniform_vocabulary",
            asset=table,
            description="Served rows use one property-type list and one bedroom label format.",
            sql=_uniform_vocabulary(table),
            op="==",
            threshold=0,
        ),
        MarketCheck(
            name=f"{prefix}_loose_pairs_hide_one_dld_row_each",
            asset=table,
            # A DLD row hidden as dld_probable_duplicate must be standing in for exactly one
            # served DXBI record; otherwise a hidden row is a silently dropped record.
            description="Every DLD row hidden in the DXBI period has exactly one served DXBI partner.",
            sql=f"SELECT abs((SELECT countIf(match_status = 'dld_probable_duplicate') FROM {table})"
            f" - (SELECT uniqExact({loose_key}) FROM {matches} WHERE {loose_key} != {empty}))"
            f" + (SELECT countIf({loose_key} != {empty}) - uniqExact({loose_key})"
            f"    FROM {matches} WHERE {loose_key} != {empty})",
            op="==",
            threshold=0,
        ),
    )


def _geo_vocabulary(table: str) -> str:
    return (
        f"SELECT countIf(geo_resolution NOT IN ('community', 'district', 'unresolved'))"
        " + countIf(geo_method NOT IN ('pulse_master', 'project_evidence', 'district_name',"
        f" 'label_evidence', 'unresolved')) FROM {table}"
    )


CHECKS: tuple[MarketCheck, ...] = (
    # --- sales -------------------------------------------------------------------------
    MarketCheck(
        name="sale_served_ids_unique",
        asset=_SALES,
        description="Every served sale appears once (no double counting).",
        sql=f"SELECT count() - uniqExact(transaction_id) FROM {_SALES} WHERE is_served = 1",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_dld_rows_conserved",
        asset=_SALES,
        description="Every de-duplicated DLD transaction is in the canonical table (none dropped).",
        sql=f"SELECT (SELECT count() FROM {_SALES} WHERE source_system != 'dxbi')"
        f" - (SELECT count() FROM ({DLD_SALES_BASE}))",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_dxbi_rows_labelled",
        asset=_SALES,
        description="Every DXBI sale has exactly one match-ledger row (none silently dropped).",
        sql=f"SELECT abs((SELECT count() FROM ({DXBI_SALES_BASE}))"
        " - (SELECT uniqExact(dxbi_sale_key) FROM market_sale_matches))"
        " + (SELECT count() - uniqExact(dxbi_sale_key) FROM market_sale_matches)",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_dxbi_match_rate_since_2022",
        asset=_SALES,
        description="Share of DXBI sales since 2022 paired with a DLD transaction.",
        sql=f"SELECT countIf(match_status = 'matched') / count() FROM {_SALES}"
        " WHERE dxbi_sale_key != ''"
        " AND coalesce(dxbi_transaction_date, toDate(instance_date)) >= '2022-01-01'",
        op=">=",
        threshold=0.80,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_dxbi_monthly_coverage_min",
        asset=_SALES,
        description="Worst complete month since 2022: DXBI sales / DLD sales.",
        sql=_SALE_MONTHLY_COVERAGE,
        op=">=",
        threshold=0.80,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_probable_duplicate_share",
        asset=_SALES,
        description="DXBI sales held back as probable duplicates of DLD rows.",
        sql=f"SELECT countIf(match_status = 'dxbi_probable_duplicate') / countIf(dxbi_sale_key != '')"
        f" FROM {_SALES}",
        op="<=",
        threshold=0.15,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_dxbi_only_share",
        asset=_SALES,
        description="DXBI sales served without any DLD counterpart (DXBI adding records).",
        sql=f"SELECT countIf(match_status = 'dxbi_only') / countIf(dxbi_sale_key != '')"
        f" FROM {_SALES}",
        op="<=",
        threshold=0.08,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_label_vocabulary",
        asset=_SALES,
        description="Transaction group and off-plan labels use one vocabulary.",
        sql=f"SELECT countIf(trans_group_en NOT IN ('Sales', 'Mortgages', 'Gifts'))"
        f" + countIf(is_offplan_en NOT IN ('Off-Plan', 'Ready', '')) FROM {_SALES}",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_dxbi_only_area_off_vocabulary_share",
        asset=_SALES,
        description="Served DXBI-only sales whose area is not a DLD area name (pseudo-areas).",
        sql=_off_vocabulary_share(_SALES),
        op="<=",
        threshold=0.25,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_offplan_status_missing_share",
        asset=_SALES,
        description="Served sales with no off-plan/ready status after normalisation.",
        sql=f"SELECT countIf(is_offplan_en = '') / count() FROM {_SALES}"
        " WHERE is_served = 1 AND trans_group_en = 'Sales'",
        op="<=",
        threshold=0.01,
    ),
    MarketCheck(
        name="sale_pair_size_agreement",
        asset=_SALES,
        description="Paired sales whose DXBI and DLD sizes agree within 1% — same unit (0.96 measured).",
        sql="SELECT countIf(size_gap <= 0.01) / count() FROM market_sale_matches"
        " WHERE dld_transaction_number != ''",
        op=">=",
        threshold=0.95,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_dxbi_lag_days",
        asset=_SALES,
        description="DXBI sales keep up with DLD (days between their latest records).",
        sql=f"SELECT abs(dateDiff('day',"
        f" (SELECT max(dxbi_transaction_date) FROM {_SALES} WHERE match_status = 'matched'),"
        f" (SELECT toDate(max(instance_date)) FROM {_SALES}"
        "   WHERE source_system != 'dxbi' AND trans_group_en = 'Sales')))",
        op="<=",
        threshold=7,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="sale_geo_crosswalk_keys_unique",
        asset=_SALES,
        description="Each name key has one crosswalk entry per kind (no conflicting placements).",
        sql="SELECT count() - uniqExact((kind, source_key)) FROM market_geo_crosswalk",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_geo_vocabulary",
        asset=_SALES,
        description="Geography resolution and method labels use one vocabulary.",
        sql=_geo_vocabulary(_SALES),
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="sale_geo_unresolved_share_open_data",
        asset=_SALES,
        # 0.012 measured on the 2026-10-03 local copy (open data has no area_id and a second
        # area vocabulary); a jump means the crosswalk evidence regressed.
        description="Served open-data sales placed in no district or community.",
        sql=f"SELECT countIf(geo_resolution = 'unresolved') / count() FROM {_SALES}"
        " WHERE is_served = 1 AND source_system = 'dld_open_data' AND trans_group_en = 'Sales'",
        op="<=",
        threshold=0.03,
    ),
    *_uniform_checks("sale", _SALES, "market_sale_matches", "loose_dld_key", "''"),
    # --- rentals -----------------------------------------------------------------------
    MarketCheck(
        name="rental_served_rows_unique",
        asset=_RENTALS,
        description="Every served rental row appears once (no double counting).",
        sql=f"SELECT count() - uniqExact(row_key) FROM {_RENTALS} WHERE is_served = 1",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="rental_dld_rows_conserved",
        asset=_RENTALS,
        description="Every de-duplicated DLD rent row is in the canonical table (none dropped).",
        sql=f"SELECT (SELECT count() FROM {_RENTALS} WHERE source_system != 'dxbi')"
        f" - (SELECT count() FROM ({DLD_RENTS_BASE}))",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="rental_dxbi_rows_labelled",
        asset=_RENTALS,
        description="Every DXBI rental has exactly one match-ledger row (none silently dropped).",
        sql="SELECT abs((SELECT count() FROM dxbi_rental_events FINAL)"
        " - (SELECT uniqExact(dxbi_contract_key) FROM market_rental_matches))"
        " + (SELECT count() - uniqExact(dxbi_contract_key) FROM market_rental_matches)",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="rental_dxbi_match_rate",
        asset=_RENTALS,
        # 0.69 measured with location-keyed pairing. DXBI lists a multi-unit contract once per
        # unit while DLD has one row per contract line, and some DXBI areas have no DLD
        # equivalent, so that surplus stays unpaired. (A location-blind matcher reached 0.87 by
        # pairing contracts across areas; rental_pair_size_agreement guards against that.)
        description="Share of DXBI rentals paired with a DLD contract row in the same area.",
        sql=f"SELECT countIf(match_status = 'matched') / countIf(dxbi_contract_key != '')"
        f" FROM {_RENTALS}",
        op=">=",
        threshold=0.60,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="rental_probable_duplicate_share",
        asset=_RENTALS,
        # 0.30 measured: the unpaired surplus above, held back because DLD has candidates.
        description="DXBI rentals held back as probable duplicates of DLD rows.",
        sql=f"SELECT countIf(match_status = 'dxbi_probable_duplicate')"
        f" / countIf(dxbi_contract_key != '') FROM {_RENTALS}",
        op="<=",
        threshold=0.40,
        blocking=False,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="rental_pair_size_agreement",
        asset=_RENTALS,
        description="Paired rentals whose DXBI and DLD floor areas agree within 5% (0.87 measured).",
        sql="SELECT countIf(size_gap <= 0.05) / count() FROM market_rental_matches"
        " WHERE dld_rent_key != 0",
        op=">=",
        threshold=0.80,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="rental_dxbi_only_share",
        asset=_RENTALS,
        description="DXBI rentals served without any DLD counterpart (DXBI adding records).",
        sql=f"SELECT countIf(match_status = 'dxbi_only') / countIf(dxbi_contract_key != '')"
        f" FROM {_RENTALS}",
        op="<=",
        threshold=0.05,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="rental_dxbi_only_area_off_vocabulary_share",
        asset=_RENTALS,
        description="Served DXBI-only rentals whose area is not a DLD area name (pseudo-areas).",
        sql=_off_vocabulary_share(_RENTALS),
        op="<=",
        threshold=0.25,
        needs_dxbi=True,
    ),
    MarketCheck(
        name="rental_geo_vocabulary",
        asset=_RENTALS,
        description="Geography resolution and method labels use one vocabulary.",
        sql=_geo_vocabulary(_RENTALS),
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="rental_geo_unresolved_share_dld",
        asset=_RENTALS,
        # Every DLD rent row carries an official district (0.000 measured); unresolved rows
        # mean the served crosswalk is missing or stale.
        description="Served DLD rent rows placed in no district.",
        sql=f"SELECT countIf(geo_resolution = 'unresolved') / count() FROM {_RENTALS}"
        " WHERE is_served = 1 AND source_system != 'dxbi'",
        op="<=",
        threshold=0.01,
    ),
    MarketCheck(
        name="rental_version_vocabulary",
        asset=_RENTALS,
        description="Contract version labels use one vocabulary (New / Renewed).",
        sql=f"SELECT countIf(version_en NOT IN ('New', 'Renewed', '')) FROM {_RENTALS}",
        op="==",
        threshold=0,
    ),
    MarketCheck(
        name="rental_dxbi_lag_days",
        asset=_RENTALS,
        # DLD registers leases that start in the future, so DXBI's latest lease start is
        # compared with DLD's latest registration — both mean "recorded up to".
        description="DXBI rentals keep up with DLD (latest DXBI lease start vs latest DLD registration).",
        sql=f"SELECT abs(dateDiff('day',"
        f" (SELECT toDate(max(contract_start_date)) FROM {_RENTALS}"
        "   WHERE dxbi_contract_key != '' AND contract_start_date <= now()),"
        f" (SELECT toDate(max(registration_date)) FROM {_RENTALS}"
        "   WHERE source_system != 'dxbi' AND registration_date <= now())))",
        op="<=",
        threshold=7,
        blocking=False,
        needs_dxbi=True,
    ),
    *_uniform_checks("rental", _RENTALS, "market_rental_matches", "loose_dld_rent_key", "0"),
)
