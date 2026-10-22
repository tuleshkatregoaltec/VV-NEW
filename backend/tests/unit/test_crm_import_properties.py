from datetime import date

from app.crm.import_models import CrmPropertyContactClaim
from app.crm.imports import _group_properties


def _claim(row: int, **overrides) -> CrmPropertyContactClaim:
    values = {
        "organization_id": "org",
        "import_id": 1,
        "list_id": 1,
        "source_sheet": "Sheet1",
        "source_row_number": row,
        "row_fingerprint": f"fp-{row}",
        "owner_key": f"owner-{row}",
        "property_key": "aspecttower|602",
        "contact_name": f"Owner {row}",
        "building_name": "Aspect Tower",
        "project_name": "Executive Towers",
        "area_name": "Business Bay",
        "unit_number": "602",
        "party_role": "Buyer",
        "ownership_status": "former_owner",
        "confidence_score": 99,
        "match_reason": "",
        "evidence": {},
    }
    values.update(overrides)
    return CrmPropertyContactClaim(**values)


def test_buyer_and_seller_rows_of_one_unit_list_as_one_property():
    # Clicking the Properties tile must show each unit once, with every buyer/seller row under it,
    # so managers can see who bought and sold the same unit.
    claims = [
        _claim(2, party_role="Seller", transaction_date=date(2020, 1, 5)),
        _claim(3, transaction_date=date(2020, 1, 5)),
        _claim(
            4,
            transaction_date=date(2024, 6, 1),
            transaction_price_aed=2_000_000,
            ownership_status="verified_current_owner",
            confidence_score=98,
            contact_name="Current Owner",
        ),
        _claim(5, property_key="aspecttower|101", unit_number="101", building_name="Aspect Tower"),
    ]

    properties = _group_properties(claims)

    assert [p.unit for p in properties] == ["101", "602"]
    unit = properties[1]
    assert (unit.rows, unit.buyers, unit.sellers) == (3, 2, 1)
    assert unit.current_owner == "Current Owner"
    assert unit.latest_date == date(2024, 6, 1)
    assert unit.latest_price_aed == 2_000_000
    assert [r.row for r in unit.records] == [4, 3, 2]
    assert properties[0].current_owner is None
