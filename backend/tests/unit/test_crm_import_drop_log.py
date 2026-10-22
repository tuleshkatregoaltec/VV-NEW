import gzip
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.crm import import_drop_log
from app.crm import imports as crm_imports
from app.crm.identity import ParsedContact
from app.crm.import_drop_log import (
    count_by_reason,
    drop_log_key,
    load_drop_log,
    no_unit_rows,
    page_drop_log,
    save_drop_log,
)
from app.crm.import_parsing import _inspect_csv, _parse_csv

TRANSACTIONS = (
    b"Date,Price,Building,Unit,Party Type,Name,ProcedureNameEn\n"
    b"2024-01-10,1200000,Panorama,T2-201,Buyer,Kept Owner,Sell\n"
    b"2024-01-11,1300000,Panorama,T2-202,Buyer,,Sell\n"
    b"2024-01-12,1400000,Panorama,T2-203,Mortgagee,Bank Example,Mortgage Registration\n"
    b"2024-01-13,1500000,Panorama,T2-204,Buyer,Gift Owner,Grant\n"
)


def _parse(data: bytes, collect: bool):
    profiles = _inspect_csv(data, "Greens.csv")
    dropped: list[dict] | None = [] if collect else None
    candidates, total = _parse_csv(data, "Greens.csv", profiles, dropped)
    return candidates, total, dropped


def test_parser_records_why_each_row_was_dropped():
    """Non-sale events and other parties survive; only nameless rows are rejected."""
    candidates, total, dropped = _parse(TRANSACTIONS, collect=True)

    assert total == 4
    assert [(row["row"], row["reason"]) for row in dropped] == [
        (3, "no_name"),
    ]
    mortgage = candidates[1]
    assert mortgage.contact_name == "Bank Example"
    assert mortgage.building_name == "Panorama"
    assert mortgage.unit_number == "T2-203"
    assert mortgage.party_role == "Mortgagee"
    assert mortgage.procedure_type == "Mortgage Registration"


def test_collecting_dropped_rows_does_not_change_the_kept_rows():
    """Logging is read-only: imports keep exactly the same rows as before."""
    with_log, _, _ = _parse(TRANSACTIONS, collect=True)
    without_log, _, _ = _parse(TRANSACTIONS, collect=False)

    assert [c.row_fingerprint for c in with_log] == [c.row_fingerprint for c in without_log]
    assert [c.contact_name for c in with_log] == ["Kept Owner", "Bank Example", "Gift Owner"]


def _contact(**overrides) -> ParsedContact:
    values = {
        "source_sheet": "Old",
        "source_row_number": 9,
        "contact_name": "No Unit Owner",
        "phone_primary": None,
        "phone_alternate": None,
        "email_primary": None,
        "area_name": "",
        "project_name": "Greens",
        "building_name": "",
        "unit_number": "",
        "property_type": "",
        "transaction_date": date(2024, 2, 1),
        "transaction_price_aed": 900_000,
        "party_role": "Buyer",
        "procedure_type": "Sell",
        "row_fingerprint": "fp",
    }
    values.update(overrides)
    return ParsedContact(**values)


def test_rows_without_a_unit_after_matching_are_logged_as_no_unit():
    rows = no_unit_rows([_contact(), _contact(source_row_number=10, unit_number="101")])

    assert [(row["row"], row["reason"], row["building"]) for row in rows] == [
        (9, "no_unit", "Greens")
    ]
    assert rows[0]["date"] == "2024-02-01"
    assert rows[0]["price"] == "900000"


LOG = [
    {"sheet": "Old", "row": 3, "reason": "no_name", "name": "", "building": "Panorama"},
    {"sheet": "Old", "row": 4, "reason": "not_buyer_seller", "name": "Bank Example"},
    {"sheet": "Old", "row": 5, "reason": "not_a_sale", "name": "Gift Owner", "unit": "T2-204"},
    {"sheet": "Old", "row": 6, "reason": "not_buyer_seller", "name": "Other Bank"},
]


def test_page_filters_by_reason_and_search_and_counts_every_reason():
    page = page_drop_log(LOG, reason="not_buyer_seller", search="other", limit=100, offset=0)

    assert page.available is True
    assert page.total == 1
    assert page.rows[0].name == "Other Bank"
    assert page.by_reason == {"no_name": 1, "not_buyer_seller": 2, "not_a_sale": 1}


def test_page_is_paged():
    page = page_drop_log(LOG, reason=None, search=None, limit=2, offset=2)

    assert page.total == 4
    assert [row.row for row in page.rows] == [5, 6]


def test_import_from_before_the_log_says_it_is_not_available():
    page = page_drop_log(None, reason=None, search=None, limit=100, offset=0)

    assert page.available is False
    assert page.rows == []


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, key, data):
        self.objects[key] = data

    async def download(self, key):
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return self.objects[key]

    async def delete_many(self, keys):
        for key in keys:
            self.objects.pop(key, None)


async def test_log_round_trips_through_private_organization_scoped_storage(monkeypatch):
    storage = _FakeStorage()
    monkeypatch.setattr(import_drop_log, "_storage", lambda: storage)

    assert await save_drop_log("org-a", 7, LOG) is True

    key = drop_log_key("org-a", 7)
    assert key == "crm-imports/org-a/7/dropped-rows.json.gz"
    assert json.loads(gzip.decompress(storage.objects[key])) == LOG
    assert await load_drop_log("org-a", 7) == LOG
    assert await load_drop_log("org-b", 7) is None
    assert count_by_reason(LOG)["not_buyer_seller"] == 2


async def test_storage_failure_does_not_fail_the_import(monkeypatch):
    """The owners file must still import when the log cannot be written."""
    broken = MagicMock()
    broken.upload = AsyncMock(side_effect=RuntimeError("storage down"))
    monkeypatch.setattr(import_drop_log, "_storage", lambda: broken)

    assert await save_drop_log("org-a", 7, LOG) is False


async def test_another_organization_cannot_read_the_dropped_rows(monkeypatch):
    monkeypatch.setattr(
        crm_imports.import_repository, "get_import_for_org", AsyncMock(return_value=None)
    )
    load = AsyncMock(return_value=LOG)
    monkeypatch.setattr(crm_imports, "load_drop_log", load)

    with pytest.raises(HTTPException) as error:
        await crm_imports.list_dropped_rows(
            MagicMock(),
            import_id=7,
            organization_id="org-b",
            reason=None,
            search=None,
            limit=100,
            offset=0,
        )

    assert error.value.status_code == 404
    load.assert_not_called()


def test_server_log_shows_what_was_dropped_without_contact_data(caplog):
    """The console shows counts, sheets, row numbers and procedures, never names or units."""
    rows = [
        {
            "sheet": "Old",
            "row": 55,
            "reason": "not_a_sale",
            "name": "ALISTAIR JAMES BUSH",
            "building": "AL GHOZLAN 3",
            "unit": "719",
            "role": "Seller",
            "procedure": "Mortgage Registration",
        },
        {
            "sheet": "Apr to Dec",
            "row": 2,
            "reason": "not_buyer_seller",
            "name": "Private Owner",
            "building": "Panorama",
            "unit": "G10",
            "role": "",
            "procedure": "",
        },
    ]
    caplog.set_level("INFO", logger="app.crm.import_drop_log")

    import_drop_log.log_drop_summary(61, total_rows=928, kept=318, merged=12, rows=rows)

    text = caplog.text
    assert "CRM import #61: 928 rows -> 318 kept, 12 merged, 2 dropped" in text
    assert (
        "sheet 'Old' · not_a_sale · 1 rows · procedures: Mortgage Registration 1 · rows 55" in text
    )
    assert "roles: (empty) 1" in text
    for private in ("ALISTAIR", "Private Owner", "AL GHOZLAN", "Panorama", "719", "G10"):
        assert private not in text
