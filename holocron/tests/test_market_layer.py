from __future__ import annotations

from typing import Any

from holocron.market import transforms
from holocron.market.checks import CHECKS, MarketCheck, evaluate
from holocron.platform.census import SCHEMA_SQL, parse_expected_schema


def _schema() -> dict[str, list[str]]:
    return parse_expected_schema(SCHEMA_SQL.read_text(encoding="utf-8"))


def test_canonical_tables_keep_every_legacy_serving_column() -> None:
    # ch_transactions / ch_rent_contracts become SELECT * over these tables; a missing column
    # would break backend queries that still select it.
    schema = _schema()
    assert set(transforms.SALE_COLUMNS) <= set(schema["market_sale_events"])
    assert set(transforms.RENTAL_COLUMNS) <= set(schema["market_rental_events"])


def test_insert_column_lists_match_table_definitions_exactly() -> None:
    # INSERT ... (columns) SELECT maps by position; an unlisted table column would be filled
    # with a default and a stray one would fail the build. release_id is the one deliberate
    # default: the release sets it on each staging table, so no statement can mislabel rows.
    schema = _schema()
    tail = [*transforms.GEO_COLUMNS, *transforms.UNIFORM_COLUMNS]
    sales_tail = [*tail, *transforms.SALE_AUDIT_COLUMNS]
    assert [*transforms.SALE_COLUMNS, "release_id", *sales_tail] == schema["market_sale_events"]
    assert [*transforms.RENTAL_COLUMNS, "release_id", *tail] == schema["market_rental_events"]


def test_label_drift_is_normalised_in_the_serving_layer() -> None:
    # Open data says "Mortgage"/"Renewed", Pulse says "Mortgages"/"Renew"; the backend filters
    # one spelling, so both must map to it before serving.
    assert "if(d.group_en = 'Mortgage', 'Mortgages', d.group_en)" in transforms._SALE_EVENTS_DLD_SQL
    assert "d.version_en = 'Renew', 'Renewed', d.version_en)" in transforms._RENTAL_EVENTS_DLD_SQL


def test_rental_pairing_is_one_to_one_without_fan_out() -> None:
    # A pairwise join fanned out to ~198M candidate pairs; rank pairing joins on the group key
    # plus row number and excludes rows already paired by an earlier tier.
    sql = transforms._rental_rank_pairing_sql(
        tier=0,
        dx_keys=("lease_start", "lease_end", "contract_amount_aed"),
        dld_keys=("s", "e", "contract_amt"),
        dx_filter="contract_amount_aed > 0",
        dld_filter="contract_amt > 0",
    )
    assert "a.k0 = b.k0 AND a.k1 = b.k1 AND a.k2 = b.k2 AND a.rn = b.rn" in sql
    assert sql.count("NOT IN (SELECT") == 2


def test_property_type_vocabulary_is_one_closed_list() -> None:
    # Every uniform value the mapping can produce is in the published list filters offer, and
    # no source word maps to two types.
    assert [*transforms._TYPE_WORDS, "Other"] == list(transforms.PROPERTY_TYPES)
    words = [word for group in transforms._TYPE_WORDS.values() for word in group]
    assert len(words) == len(set(words))


class _Result:
    def __init__(self, value: Any) -> None:
        self.result_rows = [(value,)]


class _Client:
    def __init__(self, value: Any) -> None:
        self.value = value

    def query(self, sql: str) -> _Result:
        return _Result(self.value)


def _check(op: str, threshold: float) -> MarketCheck:
    return MarketCheck(
        name="x", asset="market_sale_events", description="", sql="", op=op, threshold=threshold
    )


def test_check_evaluation_honours_direction_and_fails_when_nothing_was_measured() -> None:
    assert evaluate(_Client(0), _check("==", 0)) == (True, 0.0)
    assert evaluate(_Client(3), _check("==", 0)) == (False, 3.0)
    assert evaluate(_Client(0.87), _check(">=", 0.8))[0] is True
    assert evaluate(_Client(0.2), _check("<=", 0.15))[0] is False
    # With no DXBI rows the lag query returns NULL; reading that as "0 days behind" passed
    # the freshness check on an empty feed. No measurement must never pass.
    assert evaluate(_Client(None), _check("<=", 7))[0] is False


def test_checks_cover_conservation_uniqueness_and_labelling_for_both_tables() -> None:
    names = {check.name for check in CHECKS}
    assert len(names) == len(CHECKS)
    for prefix in ("sale", "rental"):
        assert {f"{prefix}_dld_rows_conserved", f"{prefix}_dxbi_rows_labelled"} <= names
    assert {check.asset for check in CHECKS} == {"market_sale_events", "market_rental_events"}
