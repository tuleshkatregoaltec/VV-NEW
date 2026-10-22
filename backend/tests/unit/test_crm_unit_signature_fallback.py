from datetime import date

from app.crm import owner_queries as crm_owner_queries
from app.crm.identity import ParsedContact, _location_key
from app.crm.ownership import _recover_property_identity_from_transaction

SALE_DATE = date(2024, 4, 8)
SALE_PRICE = 1_250_000


def _contact(**overrides) -> ParsedContact:
    values = {
        "source_sheet": "Sheet1",
        "source_row_number": 2,
        "contact_name": "Example Owner",
        "phone_primary": "+971501234567",
        "phone_alternate": None,
        "email_primary": None,
        "area_name": "The Greens",
        "project_name": "Greens",
        "building_name": "Al Ghozlan 1",
        "unit_number": "105",
        "property_type": "Apartment",
        "transaction_date": SALE_DATE,
        "transaction_price_aed": SALE_PRICE,
        "party_role": "Buyer",
        "procedure_type": "Sale",
        "row_fingerprint": "fingerprint",
    }
    values.update(overrides)
    return ParsedContact(**values)


def _sale(unit_number, building_name, project_name="Greens", area_name="The Greens"):
    return {
        "transaction_date": SALE_DATE,
        "sale_amount_aed": SALE_PRICE,
        "unit_key": unit_number.lower(),
        "unit_number": unit_number,
        "building_name": building_name,
        "building_key": _location_key(building_name),
        "project_name": project_name,
        "area_name": area_name,
        "area_key": _location_key(area_name),
        "sale_key": f"{building_name}-{unit_number}",
        "detail_url": "",
    }


def _registry(monkeypatch, rows):
    async def fake_query(*_args, **_kwargs):
        return rows

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)


async def test_unit_decides_between_sales_the_location_cannot_separate(monkeypatch):
    """Two Greens sales on the same day at the same price: location ties, the unit does not."""
    _registry(
        monkeypatch,
        [_sale("105", "Al Ghozlan 2"), _sale("311", "Al Ghozlan 2")],
    )
    contact = _contact(building_name="Ghozlan Two")

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 1
    resolution = contact.evidence["property_identity_resolution"]
    assert resolution["method"] == "unique_transaction_unit_signature"
    assert resolution["resolved"]["sale_key"] == "Al Ghozlan 2-105"


async def test_rows_the_location_rules_already_match_are_unchanged(monkeypatch):
    """Other imports keep their result: a clear location winner is decided exactly as before."""
    _registry(
        monkeypatch,
        [
            _sale("105", "Al Ghozlan 1"),
            _sale("105", "Executive Tower B", "Executive Towers", "Business Bay"),
        ],
    )
    contact = _contact()

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 0
    resolution = contact.evidence["property_identity_resolution"]
    assert resolution["method"] == "transaction_signature_with_location"
    assert resolution["resolved"]["sale_key"] == "Al Ghozlan 1-105"


async def test_same_unit_in_another_community_is_still_rejected(monkeypatch):
    """Off-plan launches repeat date, price and unit numbers; a sale in a different community
    must never be accepted on the unit alone."""
    _registry(
        monkeypatch,
        [_sale("105", "Avenue Residence 4", "Al Furjan", "Jebel Ali First")],
    )
    contact = _contact()

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 0
    assert result["ambiguous"] == 1
    assert "property_identity_resolution" not in contact.evidence


async def test_same_unit_in_a_different_numbered_tower_is_rejected(monkeypatch):
    """Same area and project, unit 105 in Tower 2 is not unit 105 in Tower 1."""
    _registry(
        monkeypatch,
        [
            _sale("105", "Al Ghozlan 2"),
            _sale("311", "Al Ghozlan 1"),
        ],
    )
    contact = _contact(building_name="Al Ghozlan 1 Tower")

    await _recover_property_identity_from_transaction([contact])

    resolution = contact.evidence.get("property_identity_resolution")
    assert not resolution or resolution["resolved"]["sale_key"] != "Al Ghozlan 2-105"


async def test_unit_shared_by_two_sales_on_the_signature_stays_undecided(monkeypatch):
    """If both tied sales carry the row's unit, the unit cannot decide either."""
    _registry(
        monkeypatch,
        [_sale("105", "Al Ghozlan 2"), _sale("105", "Al Ghozlan 3")],
    )
    contact = _contact(building_name="Ghozlan")

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 0
    assert "property_identity_resolution" not in contact.evidence


async def test_prefixed_building_name_and_excel_errors_fit_the_registry_building(monkeypatch):
    """Greens "Old" sheet: building "GR PANORAMA", project and area "#REF!" (broken formulas).
    DLD has "Panorama" in The Views; the words are contained and #REF! is not a location."""
    _registry(
        monkeypatch,
        [
            _sale("T2-201", "Panorama", "", "The Views"),
            _sale("T2-404", "Panorama", "", "The Views"),
        ],
    )
    contact = _contact(
        building_name="GR PANORAMA", project_name="#REF!", area_name="#REF!", unit_number="T2-201"
    )

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 1
    assert contact.evidence["property_identity_resolution"]["resolved"]["sale_key"] == (
        "Panorama-T2-201"
    )


async def test_word_containment_does_not_cross_into_another_panorama(monkeypatch):
    """Golf Panorama Tower B (Damac Hills) does not contain or fit "GR PANORAMA"."""
    _registry(
        monkeypatch,
        [_sale("T2-201", "Golf Panorama - Tower B", "", "Damac Hills")],
    )
    contact = _contact(
        building_name="GR PANORAMA",
        project_name="Greens",
        area_name="The Greens",
        unit_number="T2-201",
    )

    result = await _recover_property_identity_from_transaction([contact])

    assert result["unit"] == 0
    assert "property_identity_resolution" not in contact.evidence
