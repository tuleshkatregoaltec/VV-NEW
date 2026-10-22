"""The owner panel shows the Buyer/Seller role the owner file gave each row, and the unit, building
and area of the DLD sale it was checked against, so an agent can see when DLD names the property
differently from the file."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from app.crm import owner_queries as crm_owner_queries
from app.crm import owners as crm_owners
from app.crm.import_models import CrmOwnerProfile, CrmOwnerProperty


def _property(**overrides) -> CrmOwnerProperty:
    values = {
        "property_key": "jvt|jvt03b1th066",
        "area_name": "Jumeirah Village Triangle",
        "project_name": "Jumeirah Village Triangle",
        # A matched row carries DLD's building name; the file had the plot number there.
        "building_name": "Jumeirah Village Triangle (JVT)",
        "unit_number": "JVT03B1TH066",
        "property_type": "Land",
        "ownership_status": "former_owner",
        "confidence_score": 100,
        "match_reason": "The uploaded record identifies this contact as the seller.",
        "latest_sale_date": date(2025, 5, 27),
        "latest_sale_price_aed": 3_175_000,
    }
    values.update(overrides)
    return CrmOwnerProperty(**values)


async def test_dld_unit_building_and_area_come_from_the_dld_sale(monkeypatch):
    profile = CrmOwnerProfile(
        owner_key="owner-1",
        contact_name="ZHEREN ZHOU",
        highest_ownership_status="former_owner",
        highest_confidence_score=100,
        property_count=1,
        rental_linked_count=0,
        contact_ready_count=0,
        annual_rent_aed=0,
        properties=[_property()],
    )
    monkeypatch.setattr(
        crm_owner_queries,
        "query",
        AsyncMock(
            return_value=[
                {
                    "transaction_date": date(2025, 5, 27),
                    "sale_amount_aed": 3_175_000,
                    "unit_key": "jvt03b1th066",
                    "unit_number": "JVT03B1TH066",
                    "building_name": "Jumeirah Village Triangle (JVT)",
                    "building_key": "jumeirahvillagetrianglejvt",
                    "project_name": "",
                    "area_name": "Jumeirah Village Triangle",
                    "area_key": "jumeirahvillagetriangle",
                    "property_type": "Villa",
                    "market_status": "Ready",
                    "bedrooms": "1 Bed",
                    "size_sqft": 1_938,
                    "built_up_area_sqft": 1_636,
                    "price_per_sqft_aed": 1_638,
                    "sale_key": "sale-4",
                }
            ]
        ),
    )

    result = (await crm_owners._enrich_owner_profile_registry(profile)).properties[0]

    assert result.registry_unit_number == "JVT03B1TH066"
    assert result.registry_building_name == "Jumeirah Village Triangle (JVT)"
    assert result.registry_area_name == "Jumeirah Village Triangle"


async def test_no_dld_sale_leaves_the_dld_unit_empty(monkeypatch):
    profile = CrmOwnerProfile(
        owner_key="owner-1",
        contact_name="ZHEREN ZHOU",
        highest_ownership_status="former_owner",
        highest_confidence_score=100,
        property_count=1,
        rental_linked_count=0,
        contact_ready_count=0,
        annual_rent_aed=0,
        properties=[_property()],
    )
    monkeypatch.setattr(crm_owner_queries, "query", AsyncMock(return_value=[]))

    result = (await crm_owners._enrich_owner_profile_registry(profile)).properties[0]

    assert result.registry_unit_number is None
    assert result.registry_building_name is None


def test_role_from_the_file_is_kept_on_the_owner_property():
    row = {
        "property_key_sql": "jvt|jvt03b1th066",
        "area_name": "Jumeirah Village Triangle",
        "project_name": "",
        "building_name": "JVT03B1TH066",
        "unit_number": "JVT03B1TH066",
        "property_type": "Land",
        "party_role": "Seller",
        "ownership_status": "former_owner",
        "confidence_score": 100,
        "match_reason": "",
        "lead_id": None,
        "unit_candidate_key": None,
        "transaction_date": date(2024, 3, 25),
        "transaction_price_aed": 2_060_000,
        "latest_sale_date": None,
        "latest_sale_price_aed": None,
        "later_sale_date": None,
        "lease_end": None,
        "annual_rent_aed": None,
        "duplicate_count": 1,
        "source_lists": ["JVT JAN 2025"],
        "source_rows": ["Old:7"],
    }
    assert crm_owners._sql_owner_property(row).imported_party_role == "Seller"

    claim = MagicMock(
        party_role="Buyer",
        transaction_date=date(2024, 3, 25),
        transaction_price_aed=2_060_000,
        latest_sale_date=None,
        latest_sale_price_aed=None,
        later_sale_date=None,
        lease_end=None,
        annual_rent_aed=None,
        lead_id=None,
        unit_candidate_key=None,
        ownership_status="verified_current_owner",
        confidence_score=98,
        match_reason="",
        area_name="A",
        project_name="P",
        building_name="B",
        unit_number="1",
        property_type="Unit",
        evidence={},
        source_sheet="Old",
        source_row_number=7,
        property_key="b|1",
    )
    contact_list = MagicMock()
    contact_list.name = "JVT JAN 2025"
    assert crm_owners._property_from_rows([(claim, contact_list)]).imported_party_role == "Buyer"
