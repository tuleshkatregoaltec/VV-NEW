"""The uniform market layer: DXBI is the primary source inside its period, DLD fills its gaps.

Decision 2026-10-06: from SALES_DXBI_FROM / RENTALS_DXBI_FROM a DXBI value wins whenever DXBI
has one; DLD fills empty or generic values and still supplies the records no DXBI record
accounts for. Before those dates DLD is the row source. Each rule is checked end to end on a
disposable database: inputs in bronze/evidence tables, a published release, the served rows.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pytest

from holocron.market import release, transforms
from tests.test_market_release import MARSA_DUBAI_ID, database, warehouse  # noqa: F401

live = pytest.mark.skipif(
    os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") != "1",
    reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse",
)

_DLD_SALE = [
    "row_key", "run_id", "transaction_number", "instance_date", "group_en", "trans_value",
    "procedure_area", "area_en", "is_offplan_en", "source_system", "area_id", "prop_type_en",
    "prop_sb_type_en", "rooms_en",
]  # fmt: skip
_DXBI_SALE = [
    "sale_key", "transaction_date", "sale_amount_aed", "size_sqft", "area_name", "bedrooms",
    "property_type", "market_status", "building_name", "unit_number",
]  # fmt: skip


def _dld_sale(number: str, day: date, price: int, *, sqm: float = 80.0, rooms: str = "") -> tuple:
    return (f"r-{number}", "run", number, datetime.combine(day, datetime.min.time()), "Sales",
            price, sqm, "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID, "Unit", "Flat",
            rooms)  # fmt: skip


def _dxbi_sale(key: str, day: date, price: int, *, sqft: float, beds: str = "2 Beds") -> tuple:
    return (key, day, price, sqft, "Dubai Marina", beds, "Apartment", "Offplan", "Marina Gate",
            f"u-{key}")  # fmt: skip


def _served(client, table: str, order: str) -> list[dict[str, Any]]:
    return list(
        client.query(
            f"SELECT * FROM (SELECT * FROM {table} WHERE is_served = 1) ORDER BY {order}"
        ).named_results()
    )


@live
def test_dxbi_is_primary_in_its_period_and_dld_fills_the_gaps(warehouse) -> None:  # noqa: F811
    assert transforms.SALES_DXBI_FROM == "2022-01-01"
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=_DLD_SALE,
        rows=[
            # Strict pair (same price, one day apart): one row, DXBI's values win. DLD's rooms
            # column holds a type word ("NA"), not a bedroom count.
            _dld_sale("T1", date(2025, 3, 10), 1_500_000, rooms="NA"),
            # Same price 19 days apart and a different size: no strict pair. Inside the DXBI
            # period DXBI is served and this DLD row is hidden (loose pair).
            _dld_sale("T2", date(2025, 4, 1), 2_000_000, sqm=100.0),
            # Only DLD has it: DLD fills the gap.
            _dld_sale("T3", date(2025, 5, 1), 3_000_000),
            # Before the DXBI period: DLD is the row source; DXBI fills only its empty values.
            _dld_sale("T4", date(2021, 6, 1), 900_000),
            _dld_sale("T5", date(2021, 7, 1), 950_000, rooms=""),
            # Ordinary agreeing pairs: the release's ratio checks are calibrated on a whole
            # warehouse, so the scenario rows above sit in a realistic population.
            *(_dld_sale(f"F{i}", date(2025, 1, 5), 5_000_000 + i) for i in range(30)),
        ],
    )
    warehouse.insert_rows(
        table="dxbi_sales_unit_events",
        column_names=_DXBI_SALE,
        rows=[
            # 865 sqft is 80.4 m2: within 1% of DLD's 80 m2, so the same unit; DXBI's size wins.
            _dxbi_sale("S1", date(2025, 3, 11), 1_500_000, sqft=865.0),
            _dxbi_sale("S2", date(2025, 4, 20), 2_000_000, sqft=2_000.0),
            _dxbi_sale("S4", date(2021, 6, 20), 900_000, sqft=2_000.0),
            _dxbi_sale("S5", date(2021, 7, 1), 950_000, sqft=861.1, beds="1 Bed"),
            # A second DXBI record of T1's sale (same price, a week later, another size): T1
            # already stands for S1, so serving S7 would count one sale twice.
            _dxbi_sale("S7", date(2025, 3, 17), 1_500_000, sqft=1_200.0),
            # Only DXBI has it.
            _dxbi_sale("S6", date(2025, 6, 1), 4_444_444, sqft=700.0, beds="Studio"),
            *(_dxbi_sale(f"FS{i}", date(2025, 1, 5), 5_000_000 + i, sqft=861.1) for i in range(30)),
        ],
    )
    release.materialize_market_sale_events(clickhouse=warehouse)

    rows = {
        r["actual_worth"]: r
        for r in _served(warehouse, "market_sale_events", "actual_worth")
        if r["actual_worth"] < 5_000_000
    }
    # Every price is served exactly once: nothing double counted, nothing lost.
    assert sorted(rows) == [900_000, 950_000, 1_500_000, 2_000_000, 3_000_000, 4_444_444]

    pair = rows[1_500_000]
    assert pair["match_status"] == "matched"
    assert pair["instance_date"] == datetime(2025, 3, 11)  # DXBI's date, not DLD's 10th
    assert round(pair["size_sqm"], 2) == round(865.0 / transforms.SQFT_PER_SQM, 2)
    assert pair["procedure_area"] == pair["size_sqm"]  # legacy column carries the same value
    assert (pair["rooms_en"], pair["property_type"], pair["is_offplan_en"]) == (
        "2 B/R",
        "Apartment",
        "Off-Plan",
    )
    assert pair["location_name"] == "Dubai Marina"  # DXBI's community name
    assert pair["dld_instance_date"] == datetime(2025, 3, 10)  # DLD's registration day kept
    # DLD said another day and Ready; sizes agree within 1%, so size is not a disagreement.
    assert set(pair["dld_disagrees"]) == {"instance_date", "is_offplan_en"}

    assert rows[2_000_000]["match_status"] == "dxbi_preferred"
    hidden = warehouse.query(
        "SELECT transaction_id, match_status FROM market_sale_events WHERE is_served = 0"
    ).result_rows
    assert ("T2", "dld_probable_duplicate") in hidden

    assert rows[3_000_000]["match_status"] == "dld_only"
    assert rows[3_000_000]["property_type"] == "Apartment"  # DLD's Unit/Flat in DXBI's words
    assert rows[4_444_444]["match_status"] == "dxbi_only"
    assert rows[4_444_444]["rooms_en"] == "Studio"

    # Before the DXBI period: DLD's row and values; DXBI's unpaired copy stays hidden.
    assert rows[900_000]["match_status"] == "dld_only"
    assert ("dxbi:S4", "dxbi_probable_duplicate") in hidden
    assert ("dxbi:S7", "dxbi_probable_duplicate") in hidden
    early = rows[950_000]
    assert early["match_status"] == "matched"
    assert early["size_sqm"] == 80.0  # DLD's size wins before the DXBI period
    assert early["rooms_en"] == "1 B/R"  # DLD had none: DXBI fills it
    assert early["dld_disagrees"] == [] and early["filled_from_dld"] == []


_DLD_RENT = [
    "row_key", "run_id", "registration_date", "start_date", "end_date", "contract_amount",
    "annual_amount", "actual_area", "area_en", "area_id", "source_system", "prop_type_en",
    "prop_sub_type_en", "usage_en", "version_en",
]  # fmt: skip
_DXBI_RENT = [
    "contract_key", "unit_candidate_key", "unit_cohort_key", "lease_start", "lease_end",
    "contract_amount_aed", "annual_rent_aed", "size_sqft", "area_name", "property_type",
    "bedrooms", "contract_state", "source_account", "source_run_id", "filter_profile_id",
    "source_location_id",
]  # fmt: skip


def _dld_rent(key: str, start: date, amount: int, *, area: str, sub: str, sqm: float) -> tuple:
    begin = datetime.combine(start, datetime.min.time())
    end = begin.replace(year=begin.year + 1)
    return (key, "run", begin, begin, end, amount, amount, sqm, area, MARSA_DUBAI_ID,
            "dld_open_data", "Unit", sub, "Residential", "Renew")  # fmt: skip


def _dxbi_rent(key: str, start: date, amount: int, *, area: str, kind: str, beds: str) -> tuple:
    return (key, key, key, start, start.replace(year=start.year + 1), amount, amount, 850.0,
            area, kind, beds, "New", "acct", "run", "p", "loc")  # fmt: skip


@live
def test_rentals_follow_the_same_precedence_and_never_double_count(warehouse) -> None:  # noqa: F811
    # Rentals are located through the area link learned from matched sales.
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=_DLD_SALE,
        rows=[_dld_sale(f"A{i}", date(2025, 2, 1), 1_000_000 + i) for i in range(25)],
    )
    warehouse.insert_rows(
        table="dxbi_sales_unit_events",
        column_names=_DXBI_SALE,
        rows=[_dxbi_sale(f"SA{i}", date(2025, 2, 1), 1_000_000 + i, sqft=861.1) for i in range(25)],
    )
    release.materialize_market_sale_events(clickhouse=warehouse)

    warehouse.insert_rows(
        table="dld_od_rents_bronze",
        column_names=_DLD_RENT,
        rows=[
            # Same place, lease and amount: a tier-0 pair. DXBI has no type for it; DLD's
            # labour-camp sub-type fills it.
            _dld_rent("R1", date(2025, 3, 1), 60_000, area="Marsa Dubai", sub="Labor Camps",
                      sqm=79.0),
            # Same start and amount but DLD places it elsewhere: no pair; inside the DXBI
            # period the DXBI record is served and this DLD row hidden.
            _dld_rent("R2", date(2025, 4, 1), 75_000, area="Al Karama", sub="Flat", sqm=60.0),
            # Only DLD has it.
            _dld_rent("R3", date(2025, 5, 1), 99_000, area="Marsa Dubai", sub="Flat", sqm=90.0),
        ],
    )  # fmt: skip
    warehouse.insert_rows(
        table="dxbi_rental_events",
        column_names=_DXBI_RENT,
        rows=[
            _dxbi_rent("C1", date(2025, 3, 1), 60_000, area="Dubai Marina", kind="", beds="2 Bed"),
            _dxbi_rent("C2", date(2025, 4, 1), 75_000, area="Dubai Marina", kind="Apartment",
                       beds="1 Bed"),
        ],
    )  # fmt: skip
    release.materialize_market_rental_events(clickhouse=warehouse)

    rows = {r["contract_amount"]: r for r in _served(warehouse, "market_rental_events", "row_key")}
    assert sorted(rows) == [60_000, 75_000, 99_000]

    pair = rows[60_000]
    assert pair["match_status"] == "matched"
    assert (pair["rooms_en"], pair["version_en"]) == ("2 B/R", "New")  # DXBI wins
    assert round(pair["size_sqm"], 1) == round(850.0 / transforms.SQFT_PER_SQM, 1)
    assert pair["property_type"] == "Labour Accommodation"
    assert pair["filled_from_dld"] == ["property_type"]

    assert rows[75_000]["match_status"] == "dxbi_preferred"
    assert rows[75_000]["rooms_en"] == "1 B/R"  # DXBI-only rows keep DXBI's bedrooms
    hidden = warehouse.query(
        "SELECT row_key, match_status FROM market_rental_events WHERE is_served = 0"
    ).result_rows
    assert hidden == [("R2", "dld_probable_duplicate")]
    assert rows[99_000]["match_status"] == "dld_only"
    assert rows[99_000]["property_type"] == "Apartment"


@live
def test_type_vocabulary_maps_every_source_word_to_the_published_list(warehouse) -> None:  # noqa: F811
    expected = {
        "Flat": "Apartment", "Studio": "Apartment", "Apartment": "Apartment",
        "Stacked Townhouses": "Townhouse", "Complex Villas": "Villa", "Land": "Plot",
        "Labor Camps": "Labour Accommodation", "Staff Accommodation": "Labour Accommodation",
        "Hotel apartments": "Hotel Apartment", "Hotel Rooms": "Hotel Apartment",
        "Show Rooms": "Shop", "Kiosk": "Shop", "Workshop": "Warehouse", "Store": "Warehouse",
        "Commercial": "Commercial", "Clinic": "Other", "Virtual Unit": "Other", "": "",
    }  # fmt: skip
    words = ", ".join(f"'{word}'" for word in expected)
    mapped = warehouse.query(
        f"SELECT w, {transforms._uniform_type('w')} FROM (SELECT arrayJoin([{words}]) AS w)"
    ).result_rows
    assert dict(mapped) == expected


@live
def test_one_location_spelling_per_place_in_dxbi_words(warehouse) -> None:  # noqa: F811
    # Sources spell one place several ways; filters must list it once, as DXBI mostly writes it.
    warehouse.insert_rows(
        table="dxbi_sales_unit_events",
        column_names=["sale_key", "transaction_date", "area_name"],
        rows=[
            *((f"a{i}", date(2025, 1, 1), "Damac Hills 2") for i in range(3)),
            ("b", date(2025, 1, 1), "DAMAC HILLS 2"),
            *((f"c{i}", date(2025, 1, 1), "Dubailand") for i in range(2)),
            ("d", date(2025, 1, 1), "Dubai Land"),
        ],
    )
    spelled = warehouse.query(
        f"WITH {transforms._LOCATION_SPELLING} SELECT w, {transforms._spelled('w')} "
        "FROM (SELECT arrayJoin(['DAMAC HILLS 2', 'Dubai Land', 'DownTown Dubai',"
        " 'AL KHAWANEEJ DISTRICT', 'DIFC']) AS w)"
    ).result_rows
    # A spelling DXBI never uses keeps its letters (no guessing); DLD's all-capitals labels
    # are title-cased so the list reads as one vocabulary, acronyms excepted.
    assert dict(spelled) == {
        "DAMAC HILLS 2": "Damac Hills 2",
        "Dubai Land": "Dubailand",
        "DownTown Dubai": "DownTown Dubai",
        "AL KHAWANEEJ DISTRICT": "Al Khawaneej District",
        "DIFC": "DIFC",
    }


@live
def test_multi_unit_dld_rent_is_split_across_its_units(warehouse) -> None:  # noqa: F811
    # DLD writes one row per unit of a multi-unit contract and repeats the whole contract rent
    # on each; served as is, a 3-unit AED 300k contract counted as AED 900k of rent.
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=_DLD_SALE,
        rows=[_dld_sale(f"A{i}", date(2023, 5, 1), 1_000_000 + i) for i in range(5)],
    )
    release.materialize_market_sale_events(clickhouse=warehouse)
    begin = datetime(2023, 5, 1)
    warehouse.insert_rows(
        table="dld_od_rents_bronze",
        column_names=[*_DLD_RENT, "total_properties", "property_id"],
        rows=[
            (f"M{i}", "run", begin, begin, begin.replace(year=2024), 300_000, 300_000, 50.0 + i,
             "Marsa Dubai", MARSA_DUBAI_ID, "dld_open_data", "Unit", "Labor Camps",
             "Residential", "New", 3, i)
            for i in range(3)
        ] + [
            # A single-unit contract keeps its rent.
            ("S1", "run", begin, begin, begin.replace(year=2024), 80_000, 80_000, 60.0,
             "Marsa Dubai", MARSA_DUBAI_ID, "dld_open_data", "Unit", "Flat", "Residential",
             "New", 1, 9),
        ],
    )  # fmt: skip
    release.materialize_market_rental_events(clickhouse=warehouse)
    served = dict(
        warehouse.query(
            "SELECT row_key, annual_amount FROM market_rental_events WHERE is_served = 1"
        ).result_rows
    )
    assert served == {"M0": 100_000, "M1": 100_000, "M2": 100_000, "S1": 80_000}
    # The contract's rows still add up to its rent.
    assert sum(served[f"M{i}"] for i in range(3)) == 300_000
