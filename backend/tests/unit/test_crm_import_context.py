from datetime import date, timedelta
from unittest.mock import AsyncMock

from app.crm import owner_queries
from app.crm.cadastral import _select_plot
from app.crm.import_context import (
    _compatible_details,
    prepare_file_context,
    recover_from_file_context,
)
from app.crm.import_inventory import SourcePropertyInventory
from app.crm.import_parsing import _inspect_csv, _parse_csv
from app.crm.ownership import _apply_evidence, _sales_evidence


def parse(text, filename="Damac Hills 2.csv"):
    data = text.encode()
    profiles = _inspect_csv(data, filename)
    rows, _ = _parse_csv(data, filename, profiles)
    return rows, profiles


def sale(**overrides):
    return {
        "sale_key": "acquisition",
        "transaction_date": date(2020, 1, 1),
        "sale_amount_aed": 1000000,
        "unit_number": "V101",
        "unit_key": "v101",
        "building_name": "Vardon",
        "building_key": "vardon",
        "project_name": "",
        "area_name": "Damac Hills 2",
        "area_key": "damachills2",
        "property_type": "Villa",
        "market_status": "Offplan",
        **overrides,
    }


def test_source_counts_precede_owner_filter_and_keep_apartment_scope():
    rows, profiles = parse(
        "NameEn,BuildingNameEn,UnitNumber,Building No\n"
        "Buyer,Tower A,101,1\nSeller,Tower A,101,1\n"
        "Other,Tower B,101,1\nVilla,Cluster,,V102\n"
        "null,Cluster,,V103\nAnother,Cluster,null,0\n"
    )
    counts = profiles[0].property_inventory.summary()
    assert len(rows) == 5
    assert counts.total_rows == 6
    assert counts.distinct_unit_numbers == 1
    assert counts.location_scoped_units == 2
    assert counts.distinct_building_numbers_without_unit == 2
    assert counts.distinct_identifiers == 3
    assert counts.rows_without_identifiers == 1


def test_inventory_deduplicates_across_sheets_and_column_roles():
    first, second = SourcePropertyInventory(), SourcePropertyInventory()
    first.add({"unit_number": "V101"})
    second.add({"unit_number": "null", "building_number": "V101"})
    first.merge(second)
    assert first.summary().distinct_identifiers == 1
    assert first.summary().identifiers_in_both_columns == 1


def test_new_sheet_cluster_column_does_not_replace_actual_property_number():
    inventory = SourcePropertyInventory()
    inventory.add({"building_number": "Odora", "property_number": "XK1190B"})
    inventory.add({"building_number": "Odora", "property_number": "XK1191B"})
    inventory.add({"building_number": "XK1190B"})  # Same villa in the old layout.
    result = inventory.summary()
    assert result.distinct_building_numbers_without_unit == 2  # Literal column values.
    assert result.distinct_property_numbers_without_unit == 2
    assert result.source_property_references == 2


def test_alphanumeric_apartment_units_remain_scoped_by_building():
    inventory = SourcePropertyInventory()
    inventory.add({"building_name": "Tower A", "unit_number": "A101"})
    inventory.add({"building_name": "Tower B", "unit_number": "A101"})
    assert inventory.summary().source_property_references == 2


async def test_source_location_precedes_filename_and_numeric_suffix_is_not_an_alias(monkeypatch):
    monkeypatch.setattr(
        owner_queries,
        "fetch_import_locations",
        AsyncMock(
            return_value=[
                {"name": "Damac Hills"},
                {"name": "Damac Hills 2"},
                {"name": "Dubai Hills"},
            ]
        ),
    )
    rows, _ = parse("NameEn,Master Project\nA,Dubai Hills\nB,DAMAC HILLS 1591\n")
    await prepare_file_context(rows)
    assert rows[0].evidence["file_context"]["locations"] == ["Dubai Hills"]
    assert rows[0].evidence["file_context"]["source"] == "master_project"
    assert rows[1].evidence["file_context"]["locations"] == ["Damac Hills 2"]


async def test_legal_area_and_filename_project_share_only_registry_proven_scope(monkeypatch):
    monkeypatch.setattr(
        owner_queries,
        "fetch_import_locations",
        AsyncMock(
            return_value=[
                {
                    "name": "Damac Hills 2",
                    "area_name": "Madinat Hind 4",
                    "project_name": "Damac Hills 2",
                },
                {
                    "name": "Madinat Hind 4",
                    "area_name": "Madinat Hind 4",
                    "project_name": "Damac Hills 2",
                },
            ]
        ),
    )
    rows, _ = parse("NameEn,AreaNameEn\nExample,Madinat Hind 4\n")
    await prepare_file_context(rows)
    assert rows[0].evidence["file_context"]["locations"] == ["Damac Hills 2", "Madinat Hind 4"]
    assert rows[0].evidence["file_context"]["scope_relationship"] == "registry_area_project"


def test_villa_plural_and_full_project_hierarchy_are_compared():
    rows, _ = parse("NameEn,Project,Property Type\nExample,DAMAC HILLS (2) - VARDON,Villas\n")
    assert _compatible_details(rows[0], sale())
    assert not _compatible_details(rows[0], sale(property_type="Apartment"))
    assert not _compatible_details(rows[0], sale(building_name="Pacifica"))


def test_project_names_starting_with_v_are_not_villa_numbers():
    rows, _ = parse("NameEn,Project\nExample,Vardon\nAnother,Violet\n")
    assert all(not row.unit_number for row in rows)


async def test_wrong_exact_date_property_does_not_hide_compatible_nearby_sale(monkeypatch):
    rows, _ = parse(
        "NameEn,Project,Regis,ProcedureValue,ProcedureNameEn,ProcedurePartyTypeNameEn\n"
        "Example,Vardon,2020-01-01,1000000,Sale,Buyer\n"
    )
    row = rows[0]
    row.evidence["file_context"] = {"locations": ["Damac Hills 2"]}
    nearby = sale(transaction_date=date(2020, 1, 3))
    query = AsyncMock(
        return_value=[
            sale(building_name="Pacifica", unit_number="V202", unit_key="v202"),
            nearby,
        ]
    )
    monkeypatch.setattr(owner_queries, "fetch_import_context_sales", query)
    await recover_from_file_context(rows)
    assert row.unit_number == "V101"
    assert row.evidence["property_identity_resolution"]["date_delta_days"] == 2
    assert query.call_args.args[0]["start_date"] == date(2020, 1, 1) - timedelta(days=14)
    # Later history is deliberately outside the source-file search window.
    _apply_evidence(
        row,
        sales={
            row.property_key: [
                nearby,
                sale(sale_key="resale", transaction_date=date(2025, 1, 1)),
            ]
        },
        rentals={},
    )
    assert row.ownership_status == "former_owner"
    assert row.later_sale_date == date(2025, 1, 1)


async def test_same_price_different_properties_stays_ambiguous(monkeypatch):
    rows, _ = parse(
        "NameEn,Regis,ProcedureValue,ProcedureNameEn,ProcedurePartyTypeNameEn\n"
        "Example,2020-01-01,1000000,Sale,Buyer\n"
    )
    rows[0].evidence["file_context"] = {"locations": ["Damac Hills 2"]}
    monkeypatch.setattr(
        owner_queries,
        "fetch_import_context_sales",
        AsyncMock(
            return_value=[
                sale(),
                sale(unit_number="V102", unit_key="v102"),
            ]
        ),
    )
    await recover_from_file_context(rows)
    assert not rows[0].unit_number
    assert rows[0].evidence["context_lookup"]["status"] == "ambiguous"


async def test_full_history_stays_on_identified_property_and_has_no_file_date_limit(monkeypatch):
    rows, _ = parse(
        "NameEn,BuildingNameEn,UnitNumber,Regis,ProcedureValue,ProcedureNameEn,ProcedurePartyTypeNameEn\n"
        "Example,Vardon,V101,2020-01-01,1000000,Sale,Buyer\n"
    )
    row = rows[0]
    row.evidence["file_context"] = {"locations": ["Damac Hills 2"]}
    row.evidence["property_identity_resolution"] = {"resolved": {"sale_key": "acquisition"}}
    history = AsyncMock(
        return_value=[
            sale(),
            sale(sale_key="resale", transaction_date=date(2026, 1, 1)),
            sale(
                sale_key="unrelated",
                area_name="Other",
                area_key="other",
                transaction_date=date(2026, 2, 1),
            ),
        ]
    )
    monkeypatch.setattr(owner_queries, "fetch_unit_sales", history)
    sales = await _sales_evidence(rows)
    assert history.call_args.args[0] == {"unit_keys": ["v101"]}
    assert [s["sale_key"] for s in sales[row.property_key]] == ["acquisition", "resale"]
    _apply_evidence(row, sales=sales, rentals={})
    assert row.later_sale_date == date(2026, 1, 1)


def test_filename_and_exact_land_resolve_plot_without_claiming_villa_ownership():
    rows, _ = parse("NameEn,Master Project,Project,LandNumber\nExample,DAMAC HILLS 1591,0,12\n")
    row = rows[0]
    row.evidence["file_context"] = {"locations": ["Damac Hills 2"], "source": "filename"}
    plot = {
        "land_number": "12",
        "land_sub_number": "0",
        "area_id": 1,
        "area_en": "Madinat Hind 4",
        "master_project_en": "DAMAC HILLS 2",
        "project_en": "DAMAC HILLS (2) - VARDON",
    }
    assert _select_plot(row, [plot]) == plot
    assert _select_plot(row, [plot, {**plot, "area_id": 2}]) is None
    row.evidence["cadastral_identity"] = {"property_key": "dld-land:1:12:0"}
    _apply_evidence(row, sales={}, rentals={})
    assert row.ownership_status == "unit_linked_unverified"
