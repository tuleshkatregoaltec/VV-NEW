from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.crm import owner_queries as crm_owner_queries
from app.crm import owners as crm_owners
from app.crm.identity import (
    ParsedContact,
    _building_match_strength,
    _dedupe_candidates,
    _drop_candidates_without_unit,
    _location_similarity,
    _normalize_phone,
    _normalize_unit,
    _recover_embedded_property_identity,
)
from app.crm.import_models import CrmOwnerProfile, CrmOwnerProperty
from app.crm.import_parsing import _heuristic_mapping, _summary, _unique_headers
from app.crm.imports import delete_contact_import
from app.crm.owner_repository import _owner_filter_sql, _owner_scope_clause
from app.crm.ownership import (
    _apply_evidence,
    _recover_property_identity_from_near_transaction,
    _recover_property_identity_from_transaction,
    _sales_evidence,
)


def _contact(**overrides) -> ParsedContact:
    values = {
        "source_sheet": "Sheet1",
        "source_row_number": 2,
        "contact_name": "Example Owner",
        "phone_primary": "+971501234567",
        "phone_alternate": None,
        "email_primary": "owner@example.com",
        "area_name": "Business Bay",
        "project_name": "Executive Towers",
        "building_name": "Aspect Tower",
        "unit_number": "602",
        "property_type": "Apartment",
        "transaction_date": date(2025, 1, 31),
        "transaction_price_aed": 2_250_000,
        "party_role": "Buyer",
        "procedure_type": "Sale",
        "row_fingerprint": "fingerprint",
    }
    values.update(overrides)
    return ParsedContact(**values)


def test_known_transaction_export_maps_owner_property_and_sale_fields():
    headers = _unique_headers(
        [
            "Regis",
            "ProcedureValue",
            "Master Project",
            "Project",
            "BuildingNameEn",
            "UnitNumber",
            "ProcedurePartyTypeNameEn",
            "NameEn",
            "Mobile",
            "ProcedureNameEn",
        ]
    )

    mapping, family, warnings = _heuristic_mapping(
        headers,
        filename="Business Bay 2024 SVR.xlsx",
        sheet_name="Sheet1",
    )

    assert family == "transaction_party"
    assert mapping["transaction_date"] == "Regis"
    assert mapping["transaction_price_aed"] == "ProcedureValue"
    assert mapping["building_name"] == "BuildingNameEn"
    assert mapping["unit_number"] == "UnitNumber"
    assert mapping["contact_name"] == "NameEn"
    assert not warnings


def test_building_identity_resolves_reordered_words_and_roman_numerals():
    assert _building_match_strength(
        "MULBERRY at PARK HEIGHTS Building A1",
        "Mulberry Building A1, Mulberry At Park Heights",
    ) == (2, "canonical_building_identity")
    assert _building_match_strength("Ellington House II", "Ellington House 2") == (
        2,
        "canonical_building_identity",
    )
    assert _building_match_strength(
        "Mulberry II at Park Heights Building A1",
        "Mulberry Building A1",
    ) == (0, "")


def test_embedded_property_identifiers_are_recovered_from_building_field():
    villa = _contact(
        project_name="MAPLE 2",
        building_name="DE Maple 2-V-486",
        unit_number="NULL",
    )
    _recover_embedded_property_identity(villa)

    assert villa.building_name == "MAPLE 2"
    assert villa.unit_number == "DEMAPLE2-V-486"
    assert villa.evidence["address_recovery"]["method"] == "embedded_villa_identifier"

    numeric = _contact(
        project_name="Golf Grove",
        building_name="66",
        unit_number="null",
    )
    _recover_embedded_property_identity(numeric)

    assert numeric.building_name == "Golf Grove"
    assert numeric.unit_number == "66"
    assert numeric.evidence["address_recovery"]["method"] == "embedded_numeric_identifier"
    assert _normalize_unit("NULL") == ""

    edge = _contact(
        project_name="The EDGE",
        building_name="THE EDGE",
        unit_number="B2611",
    )
    _recover_embedded_property_identity(edge)

    assert edge.building_name == "The Edge Tower B"
    assert edge.unit_number == "B2611"
    assert edge.evidence["address_recovery"]["method"] == "project_unit_prefix_tower"


@pytest.mark.asyncio
async def test_unique_transaction_signature_recovers_canonical_property(monkeypatch):
    contact = _contact(
        area_name="",
        project_name="Sidra 2",
        building_name="",
        unit_number="NULL",
        transaction_date=date(2024, 4, 8),
        transaction_price_aed=4_250_000,
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "transaction_date": date(2024, 4, 8),
                "sale_amount_aed": 4_250_000,
                "unit_key": "de-sidra2-v-117",
                "unit_number": "DE-SIDRA2-V-117",
                "building_name": "Sidra 2",
                "building_key": "sidra2",
                "project_name": "",
                "area_name": "Dubai Hills Estate",
                "area_key": "dubaihillsestate",
                "sale_key": "sidra-117",
                "detail_url": "https://dxbinteract.com/sold/sidra-117",
            }
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unique"] == 1
    assert contact.area_name == "Dubai Hills Estate"
    assert contact.building_name == "Sidra 2"
    assert contact.unit_number == "DE-SIDRA2-V-117"
    assert (
        contact.evidence["property_identity_resolution"]["method"] == "unique_transaction_signature"
    )


@pytest.mark.asyncio
async def test_unique_transaction_signature_rejects_contradictory_location(monkeypatch):
    contact = _contact(
        area_name="Marsa Dubai",
        project_name="Jumeirah Gate",
        building_name="Jumeirah Gate Tower 2",
        unit_number="1110",
        transaction_date=date(2024, 4, 8),
        transaction_price_aed=4_250_000,
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "transaction_date": contact.transaction_date,
                "sale_amount_aed": contact.transaction_price_aed,
                "unit_key": "1110",
                "unit_number": "1110",
                "building_name": "Avenue Residence 4",
                "building_key": "avenueresidence4",
                "project_name": "Al Furjan",
                "area_name": "Jebel Ali First",
                "area_key": "jebelalifirst",
                "sale_key": "unrelated-sale",
                "detail_url": "",
            }
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unique"] == 0
    assert result["ambiguous"] == 1
    assert contact.building_name == "Jumeirah Gate Tower 2"
    assert "property_identity_resolution" not in contact.evidence


def test_vetted_location_aliases_and_padded_numbers_match():
    assert _location_similarity("Jumeirah Gate Tower 1", "The Address JBR 1") == 100
    assert _location_similarity("Jumeirah Gate Tower 1", "Jason The Address JBR 1") == 100
    assert _location_similarity("PARK HEIGHTS II T2", "Park Heights 2 Tower 2") == 100
    assert _location_similarity("MAG EYE 910", "MAG 910") == 100
    assert _location_similarity("ELLINGTON HOUSE", "Ellington House 1") == 100
    assert _building_match_strength("Bay Square - 08", "Bay Square 8") == (
        2,
        "canonical_building_identity",
    )


@pytest.mark.asyncio
async def test_transaction_collision_requires_unique_location_context(monkeypatch):
    contact = _contact(
        area_name="Marsa Dubai",
        project_name="Jumeirah Gate",
        building_name="Jumeirah Gate Tower 1",
        unit_number="1203",
        transaction_date=date(2024, 5, 2),
        transaction_price_aed=3_100_000,
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "transaction_date": contact.transaction_date,
                "sale_amount_aed": contact.transaction_price_aed,
                "unit_key": "1203",
                "unit_number": "1203",
                "building_name": f"Jumeirah Gate Tower {tower}",
                "building_key": f"jumeirahgatetower{tower}",
                "project_name": "Jumeirah Gate",
                "area_name": "Marsa Dubai",
                "area_key": "marsadubai",
                "sale_key": f"tower-{tower}",
                "detail_url": "",
            }
            for tower in (1, 2)
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    result = await _recover_property_identity_from_transaction([contact])

    assert result["contextual"] == 1
    assert contact.building_name == "Jumeirah Gate Tower 1"
    assert contact.evidence["property_identity_resolution"]["score"] == 100


@pytest.mark.asyncio
async def test_near_transaction_date_recovers_unit_with_project_context(monkeypatch):
    contact = _contact(
        area_name="",
        project_name="Sidra 2",
        building_name="",
        unit_number="NULL",
        transaction_date=date(2024, 3, 13),
        transaction_price_aed=6_300_000,
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "transaction_date": date(2024, 3, 14),
                "sale_amount_aed": 6_300_000,
                "unit_key": "desidra2-v-57",
                "unit_number": "DE Sidra 2-V-57",
                "building_name": "Sidra 2",
                "building_key": "sidra2",
                "project_name": "Sidra Villas",
                "area_name": "Dubai Hills Estate",
                "area_key": "dubaihillsestate",
                "sale_key": "sidra-57",
                "detail_url": "",
            }
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    result = await _recover_property_identity_from_near_transaction([contact])

    assert result["resolved"] == 1
    assert contact.building_name == "Sidra 2"
    assert contact.unit_number == "DESIDRA2-V-57"
    resolution = contact.evidence["property_identity_resolution"]
    assert resolution["method"] == "near_transaction_signature_with_location"
    assert resolution["date_delta_days"] == 1


@pytest.mark.asyncio
async def test_sales_evidence_uses_canonical_building_identity(monkeypatch):
    contact = _contact(
        area_name="",
        project_name="Mulberry at Park Heights",
        building_name="Mulberry at Park Heights Building A1",
        unit_number="401",
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "unit_key": "401",
                "unit_number": "401",
                "building_name": "Mulberry Building A1, Mulberry At Park Heights",
                "building_key": "mulberrybuildinga1mulberryatparkheights",
                "project_name": "",
                "area_name": "Dubai Hills Estate",
                "area_key": "dubaihillsestate",
                "transaction_date": date(2026, 7, 10),
                "sale_amount_aed": 5_575_000,
                "sale_key": "mulberry-401",
                "detail_url": "https://dxbinteract.com/sold/t-vnyeo",
            }
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    sales = await _sales_evidence([contact])

    assert sales[contact.property_key][0]["_match_method"] == "unit_with_location_hierarchy"


@pytest.mark.asyncio
async def test_sales_evidence_combines_project_and_building_hierarchy(monkeypatch):
    contact = _contact(
        area_name="",
        project_name="PARK RIDGE",
        building_name="PARK RIDGE TOWER C",
        unit_number="1203",
    )

    async def fake_query(*_args, **_kwargs):
        return [
            {
                "unit_key": "1203",
                "unit_number": "1203",
                "building_name": f"Tower {tower}",
                "building_key": f"tower{tower.lower()}",
                "project_name": "Park Ridge",
                "area_name": "Dubai Hills Estate",
                "area_key": "dubaihillsestate",
                "transaction_date": date(2024, 1, index),
                "sale_amount_aed": 2_000_000 + index,
                "sale_key": f"tower-{tower}",
                "detail_url": "",
            }
            for index, tower in enumerate(("B", "C"), start=1)
        ]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)

    sales = await _sales_evidence([contact])

    assert contact.building_name == "Tower C"
    assert contact.project_name == "Park Ridge"
    assert [row["sale_key"] for row in sales[contact.property_key]] == ["tower-C"]


def test_placeholder_columns_are_pruned_and_elan_uses_compound_villa_identifier():
    headers = _unique_headers(
        [
            "VILLA NUMBER",
            "UNIT NUMBER PROXY",
            "VILLA NUMBER",
            "NAME",
            "PHONE",
            "Column2",
            "Column999",
        ]
    )

    mapping, family, _ = _heuristic_mapping(
        headers,
        filename="Tilal Al Ghaf - ELAN(923).xlsx",
        sheet_name="Sheet1",
    )

    assert [header.key for header in headers] == [
        "VILLA NUMBER",
        "UNIT NUMBER PROXY",
        "VILLA NUMBER__2",
        "NAME",
        "PHONE",
    ]
    assert family == "contact_list"
    assert mapping["unit_number"] == "VILLA NUMBER__2"
    assert _normalize_unit("TAG-ELAN-B359") == "B359"


def test_aura_plot_preregistration_column_is_used_as_the_villa_identifier():
    headers = _unique_headers(
        [
            "Date",
            "Price",
            "Master Project",
            "Project",
            "Plot Pre Registeration No",
            "Party Type",
            "NameEn",
            "Mobile",
        ]
    )

    mapping, family, warnings = _heuristic_mapping(
        headers,
        filename="Aura 3.xlsx",
        sheet_name="Sheet1",
    )

    assert family == "transaction_party"
    assert mapping["unit_number"] == "Plot Pre Registeration No"
    assert _normalize_unit("TAG-A3-075.2") == "075.2"
    assert "No direct unit-number column was detected." not in warnings


@pytest.mark.asyncio
async def test_delete_import_removes_published_records_and_the_import(monkeypatch):
    monkeypatch.setattr("app.crm.imports.delete_drop_log", AsyncMock())
    record = MagicMock()
    record.mapping = {}
    lookup = MagicMock()
    lookup.first.return_value = record
    db = AsyncMock()
    db.exec.side_effect = [lookup, MagicMock(), MagicMock()]

    await delete_contact_import(
        db,
        import_id=4,
        organization_id="organization-1",
    )

    assert db.exec.await_count == 3
    db.delete.assert_awaited_once_with(record)
    db.commit.assert_awaited_once()


def test_uae_phone_numbers_are_normalized_without_inventing_short_numbers():
    assert _normalize_phone("050 123 4567") == "+971501234567"
    assert _normalize_phone("971501234567") == "+971501234567"
    assert _normalize_phone("123") is None


def test_exact_latest_sale_date_and_price_verifies_current_owner():
    contact = _contact()
    sales = {
        contact.property_key: [
            {
                "transaction_date": date(2025, 1, 31),
                "sale_amount_aed": 2_250_000,
            }
        ]
    }
    rentals = {
        contact.property_key: {
            "event_key": "lead-1",
            "unit_candidate_key": "candidate-1",
            "lease_end": date(2026, 3, 31),
            "annual_rent_aed": 240_000,
        }
    }

    _apply_evidence(contact, sales=sales, rentals=rentals)

    assert contact.ownership_status == "verified_current_owner"
    assert contact.confidence_score == 98
    assert contact.lead_id == "lead-1"
    assert contact.unit_candidate_key == "candidate-1"


def test_later_sale_marks_uploaded_buyer_as_former_owner():
    contact = _contact(
        transaction_date=date(2018, 9, 4),
        transaction_price_aed=1_300_000,
    )
    sales = {
        contact.property_key: [
            {
                "transaction_date": date(2018, 9, 4),
                "sale_amount_aed": 1_300_000,
            },
            {
                "transaction_date": date(2025, 1, 31),
                "sale_amount_aed": 2_250_000,
            },
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "former_owner"
    assert contact.later_sale_date == date(2025, 1, 31)
    assert "later sale" in contact.match_reason.lower()


def test_contact_only_row_remains_unverified_even_with_exact_unit():
    contact = _contact(
        transaction_date=None,
        transaction_price_aed=None,
        party_role="",
        procedure_type="",
    )
    sales = {
        contact.property_key: [
            {
                "transaction_date": date(2025, 1, 31),
                "sale_amount_aed": 2_250_000,
            }
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "unit_linked_unverified"
    assert "does not establish a sale acquisition" in contact.match_reason


def _owner_list_contact(**overrides) -> ParsedContact:
    contact = _contact(party_role="", procedure_type="", **overrides)
    contact.evidence["event_family"] = "contact_snapshot"
    return contact


def test_owner_list_row_matching_latest_sale_is_verified():
    # Owner-list sheets omit Buyer/Seller; a DLD sale on the same date and price
    # proves the acquisition, so these owners must not stay unverified.
    contact = _owner_list_contact()
    sales = {
        contact.property_key: [
            {"transaction_date": date(2025, 1, 31), "sale_amount_aed": 2_250_000}
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "verified_current_owner"
    assert contact.evidence["acquisition_inferred"] is True


def test_owner_list_row_with_later_sale_is_former_owner():
    # An owner-list entry whose acquisition is followed by a resale is not a lead.
    contact = _owner_list_contact(
        transaction_date=date(2018, 9, 4), transaction_price_aed=1_300_000
    )
    sales = {
        contact.property_key: [
            {"transaction_date": date(2018, 9, 4), "sale_amount_aed": 1_300_000},
            {"transaction_date": date(2025, 1, 31), "sale_amount_aed": 2_250_000},
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "former_owner"
    assert contact.later_sale_date == date(2025, 1, 31)


def test_owner_list_row_without_price_matching_sale_date_is_probable():
    # Date alone fits the sale but cannot prove the amount.
    contact = _owner_list_contact(transaction_price_aed=None)
    sales = {
        contact.property_key: [
            {"transaction_date": date(2025, 1, 31), "sale_amount_aed": 2_250_000}
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "probable_current_owner"


@pytest.mark.parametrize(
    ("row_date", "row_price"),
    [(date(2023, 6, 1), 2_250_000), (date(2025, 1, 31), 1_000_000)],
)
def test_owner_list_row_without_matching_sale_stays_unverified(row_date, row_price):
    # A registration date may be a title-deed date, not the sale date; without a
    # matching DLD sale the row is not evidence of a conflict.
    contact = _owner_list_contact(transaction_date=row_date, transaction_price_aed=row_price)
    sales = {
        contact.property_key: [
            {"transaction_date": date(2025, 1, 31), "sale_amount_aed": 2_250_000}
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "unit_linked_unverified"
    assert "acquisition_inferred" not in contact.evidence


def test_mortgage_row_is_not_treated_as_owner_list_acquisition():
    # Financing events do not transfer ownership.
    contact = _contact(procedure_type="Mortgage Registration")
    contact.evidence["event_family"] = "financing"
    sales = {
        contact.property_key: [
            {"transaction_date": date(2025, 1, 31), "sale_amount_aed": 2_250_000}
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "unit_linked_unverified"


def _plot_contact(**overrides) -> ParsedContact:
    values = {
        "unit_number": "",
        "building_name": "",
        "project_name": "",
        "transaction_date": date(2022, 12, 29),
        "transaction_price_aed": 1_717_600,
        "procedure_type": "Complete Delayed Sell",
    }
    contact = _contact(**{**values, **overrides})
    contact.evidence["event_family"] = "sale"
    contact.evidence["cadastral_identity"] = {
        "property_key": "dld-land:505:2822:0",
        "villa_code": "dh2-xv728b",
    }
    return contact


def _villa_sale(on: date, amount: int) -> dict:
    return {"transaction_date": on, "sale_amount_aed": amount, "sale_key": f"s-{on}"}


def test_plot_seller_is_former_owner_without_registry_sales():
    # The file itself records the disposal; villas without a unit must not hide it.
    contact = _plot_contact(party_role="Seller")

    _apply_evidence(contact, sales={}, rentals={})

    assert contact.ownership_status == "former_owner"


def test_plot_buyer_with_later_villa_sale_is_former_owner():
    # A resale of the same villa after the buyer's acquisition ends their ownership.
    contact = _plot_contact()
    sales = {
        contact.property_key: [
            _villa_sale(date(2016, 11, 29), 1_200_000),
            _villa_sale(date(2024, 2, 13), 1_200_000),
        ]
    }

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "former_owner"
    assert contact.later_sale_date == date(2024, 2, 13)


def test_plot_buyer_without_later_villa_sale_is_probable_owner():
    # The handover is not in DLD sales, but no resale is recorded since.
    contact = _plot_contact()
    sales = {contact.property_key: [_villa_sale(date(2016, 11, 29), 1_200_000)]}

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "probable_current_owner"


def test_plot_buyer_matching_latest_villa_sale_is_verified():
    contact = _plot_contact(transaction_date=date(2024, 2, 13), transaction_price_aed=1_200_000)
    sales = {contact.property_key: [_villa_sale(date(2024, 2, 14), 1_200_000)]}

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "verified_current_owner"


def test_plot_mortgage_without_later_sale_does_not_become_current_owner():
    # A mortgage does not prove who acquired the villa.
    contact = _plot_contact(procedure_type="Mortgage Registration")
    contact.evidence["event_family"] = "financing"
    sales = {contact.property_key: [_villa_sale(date(2016, 11, 29), 1_200_000)]}

    _apply_evidence(contact, sales=sales, rentals={})

    assert contact.ownership_status == "unit_linked_unverified"


def test_plot_without_villa_sales_stays_unverified():
    contact = _plot_contact()

    _apply_evidence(contact, sales={}, rentals={})

    assert contact.ownership_status == "unit_linked_unverified"


def test_unresolvable_source_and_registry_gap_are_distinct_states():
    missing_identity = _contact(building_name="Sidra 2", unit_number="")
    _apply_evidence(missing_identity, sales={}, rentals={})
    assert missing_identity.ownership_status == "insufficient_property_data"

    registry_gap = _contact(building_name="Elan", unit_number="A150")
    registry_gap.evidence["sales_lookup"] = {"status": "unit_not_observed"}
    _apply_evidence(registry_gap, sales={}, rentals={})
    assert registry_gap.ownership_status == "registry_not_observed"


def test_duplicate_owner_property_rows_collapse_to_strongest_evidence():
    verified_one = _contact(
        source_row_number=2157,
        transaction_date=date(2024, 2, 20),
        ownership_status="verified_current_owner",
        confidence_score=98,
    )
    verified_two = _contact(
        source_row_number=2184,
        transaction_date=date(2024, 2, 20),
        ownership_status="verified_current_owner",
        confidence_score=98,
    )
    probable_one = _contact(
        source_row_number=1739,
        transaction_date=date(2024, 2, 26),
        ownership_status="probable_current_owner",
        confidence_score=72,
    )
    probable_two = _contact(
        source_row_number=1765,
        transaction_date=date(2024, 2, 26),
        ownership_status="probable_current_owner",
        confidence_score=72,
    )

    unique, collapsed = _dedupe_candidates([verified_one, verified_two, probable_one, probable_two])

    assert collapsed == 3
    assert len(unique) == 1
    assert unique[0].ownership_status == "verified_current_owner"
    assert unique[0].confidence_score == 98
    assert unique[0].evidence["duplicate_count"] == 4
    assert unique[0].evidence["source_rows"] == [
        "Sheet1:1739",
        "Sheet1:1765",
        "Sheet1:2157",
        "Sheet1:2184",
    ]


def test_same_contact_across_two_properties_remains_two_property_claims():
    first = _contact(unit_number="602", row_fingerprint="first")
    second = _contact(unit_number="603", row_fingerprint="second")

    unique, collapsed = _dedupe_candidates([first, second])

    assert collapsed == 0
    assert len(unique) == 2


def test_rows_without_a_unit_after_matching_are_dropped_and_counted():
    # A row with no unit cannot identify a property; keeping it only adds an empty
    # owner entry that a later import of the same owner never replaces.
    with_unit = _contact(unit_number="602", row_fingerprint="with-unit")
    no_unit = _contact(unit_number="", row_fingerprint="no-unit")
    no_building = _contact(
        building_name="", project_name="", unit_number="1001", row_fingerprint="no-building"
    )

    kept, dropped = _drop_candidates_without_unit([with_unit, no_unit, no_building])

    assert dropped == 1
    assert kept == [with_unit, no_building]
    assert _summary(kept, total_rows=3, dropped_no_unit=dropped).dropped_no_unit == 1


def test_summary_counts_each_property_once_across_buyer_and_seller_rows():
    # Managers asked how many units/villas a file covers; one unit sold twice, with a buyer and
    # a seller row per sale, is one property, and rows with no unit are not properties.
    rows = [
        _contact(contact_name="Buyer A", party_role="Buyer", row_fingerprint="a"),
        _contact(contact_name="Seller B", party_role="Seller", row_fingerprint="b"),
        _contact(contact_name="Buyer C", building_name="ASPECT TOWER", row_fingerprint="c"),
        _contact(unit_number="603", row_fingerprint="d"),
        _contact(unit_number="", row_fingerprint="e"),
    ]

    summary = _summary(rows, total_rows=5)

    assert summary.candidate_rows == 5
    assert summary.distinct_properties == 2


def test_unit_recovered_by_registry_match_is_kept():
    # Matching runs before the drop, so a unit filled in from an exact sale signature
    # keeps the row even when the uploaded unit cell was empty.
    recovered = _contact(unit_number="")
    recovered.unit_number = "1204"

    kept, dropped = _drop_candidates_without_unit([recovered])

    assert dropped == 0
    assert kept == [recovered]


def test_prior_owners_are_separated_from_current_owner_registry():
    prior_clause = _owner_scope_clause("prior_owners")
    registry_clause = _owner_scope_clause("registry")

    assert "ownership_status = 'former_owner'" in prior_clause
    assert "ownership_status <> 'former_owner'" in registry_clause
    assert "verified_current_owner" in registry_clause


def test_owner_filters_cover_lease_rent_and_value_ranges():
    clause, parameters = _owner_filter_sql(
        scope="rental_ready",
        status=None,
        search=None,
        lease_window="expiring_60",
        min_rent_aed=100_000,
        max_rent_aed=250_000,
        min_value_aed=1_500_000,
        max_value_aed=4_000_000,
    )

    assert "lease_end" in clause
    assert "annual_rent_aed" in clause
    assert "latest_sale_price_aed" in clause
    assert parameters == {
        "lease_lower_days": 30,
        "lease_upper_days": 60,
        "owner_min_rent": 100_000,
        "owner_max_rent": 250_000,
        "owner_min_value": 1_500_000,
        "owner_max_value": 4_000_000,
    }


@pytest.mark.asyncio
async def test_owner_profile_enrichment_adds_authoritative_registry_details(monkeypatch):
    owner_property = CrmOwnerProperty(
        property_key="mulberry-a1-401",
        area_name="Dubai Hills Estate",
        project_name="Mulberry at Park Heights",
        building_name="MULBERRY at PARK HEIGHTS Building A1",
        unit_number="401",
        property_type="Flat",
        ownership_status="former_owner",
        confidence_score=99,
        match_reason="A later sale was found",
        imported_transaction_date=date(2020, 2, 9),
        imported_transaction_price_aed=2_789_888,
        imported_property_type="Flat",
        latest_sale_date=date(2026, 7, 10),
        latest_sale_price_aed=5_575_000,
    )
    profile = CrmOwnerProfile(
        owner_key="owner-1",
        contact_name="Example Owner",
        highest_ownership_status="former_owner",
        highest_confidence_score=99,
        property_count=1,
        rental_linked_count=0,
        contact_ready_count=0,
        annual_rent_aed=0,
        properties=[owner_property],
    )
    registry_query = AsyncMock(
        return_value=[
            {
                "transaction_date": date(2026, 7, 10),
                "sale_amount_aed": 5_575_000,
                "unit_key": "401",
                "unit_number": "401",
                "building_name": "Mulberry Building A1, Mulberry At Park Heights",
                "building_key": "mulberry-building-a1",
                "project_name": "Mulberry at Park Heights",
                "area_name": "Dubai Hills Estate",
                "area_key": "dubai-hills-estate",
                "property_type": "Apartment",
                "market_status": "Ready",
                "bedrooms": "3 Beds",
                "size_sqft": 1_938,
                "built_up_area_sqft": 0,
                "price_per_sqft_aed": 2_877,
                "sale_key": "sale-1",
            }
        ]
    )
    monkeypatch.setattr(crm_owner_queries, "query", registry_query)

    enriched = await crm_owners._enrich_owner_profile_registry(profile)

    result = enriched.properties[0]
    assert result.imported_transaction_price_aed == 2_789_888
    assert result.registry_sale_price_aed == 5_575_000
    assert result.registry_property_type == "Apartment"
    assert result.registry_bedrooms == "3 Beds"
    assert result.registry_size_sqft == 1_938
    assert result.registry_built_up_area_sqft is None
    assert result.registry_price_per_sqft_aed == 2_877
    assert result.registry_market_status == "Ready"
    registry_query.assert_awaited_once()
