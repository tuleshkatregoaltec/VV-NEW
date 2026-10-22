"""An empty owner file (no data rows) is refused with one clear message and leaves nothing behind:
no import in the history, no list, and no "already imported" the next time. A file with rows
still imports exactly as before."""

import io
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile

from app.crm import imports
from app.crm.import_models import CrmContactImport, CrmContactList

EMPTY_MESSAGE = "File is empty, so nothing was imported."


def _xlsx(rows: list[list[object]]) -> bytes:
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _db():
    """A session that gives every saved record an id, like the database would."""
    added: list[object] = []

    def give_ids(*_):
        for item in added:
            if getattr(item, "id", None) is None:
                item.id = 1

    db = MagicMock()
    db.add.side_effect = added.append
    db.add_all.side_effect = added.extend
    for name in ("commit", "rollback", "delete", "exec"):
        setattr(db, name, AsyncMock())
    db.refresh = AsyncMock(side_effect=give_ids)
    db.flush = AsyncMock(side_effect=give_ids)
    db.added = added
    return db


@pytest.fixture
def pipeline(monkeypatch):
    async def same(profile):
        return profile

    monkeypatch.setattr(
        imports.import_repository, "find_import_by_digest", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(imports, "_llm_mapping", same)
    monkeypatch.setattr(imports, "prepare_file_context", AsyncMock())
    monkeypatch.setattr(imports, "recover_from_file_context", AsyncMock())
    monkeypatch.setattr(imports, "resolve_cadastral_plots", AsyncMock())
    monkeypatch.setattr(imports, "villa_sales_for_plots", AsyncMock(return_value={}))
    monkeypatch.setattr(imports, "_recover_property_identity_from_transaction", AsyncMock())
    monkeypatch.setattr(imports, "_recover_property_identity_from_near_transaction", AsyncMock())
    monkeypatch.setattr(imports, "_sales_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(imports, "_apply_registry_unit_fallbacks", AsyncMock())
    monkeypatch.setattr(imports, "save_drop_log", AsyncMock(return_value=True))
    monkeypatch.setattr(imports, "_rental_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(imports, "_enrich_workspaces", AsyncMock())
    monkeypatch.setattr(imports, "_invalidate_owner_summary_cache", MagicMock())
    response = MagicMock(name="import response")
    monkeypatch.setattr(imports, "_import_response", AsyncMock(return_value=response))
    return response


async def _upload(db, filename: str, data: bytes):
    return await imports.process_contact_import(
        db,
        upload=UploadFile(file=io.BytesIO(data), filename=filename),
        name="",
        user_id="user-1",
        organization_id="organization-1",
    )


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("owners.xlsx", _xlsx([])),
        ("owners.xlsx", _xlsx([["NameEn", "Mobile", "BuildingNameEn", "UnitNumber"]])),
        ("owners.xlsx", _xlsx([["NameEn", "Mobile"], [None, None], ["", ""]])),
        ("owners.csv", b"NameEn,Mobile,UnitNumber\n"),
        ("owners.csv", b"\n\n\n"),
    ],
    ids=[
        "xlsx-no-rows",
        "xlsx-headers-only",
        "xlsx-blank-rows",
        "csv-headers-only",
        "csv-blank-lines",
    ],
)
async def test_empty_file_is_refused_and_leaves_nothing_behind(pipeline, filename, data):
    db = _db()

    with pytest.raises(HTTPException) as refused:
        await _upload(db, filename, data)

    assert refused.value.status_code == 400
    assert refused.value.detail == EMPTY_MESSAGE
    record = next(item for item in db.added if isinstance(item, CrmContactImport))
    db.delete.assert_awaited_once_with(record)
    assert record.status != "failed"
    assert not [item for item in db.added if isinstance(item, CrmContactList)]


async def test_zero_byte_file_gets_the_same_message(pipeline):
    db = _db()

    with pytest.raises(HTTPException) as refused:
        await _upload(db, "owners.csv", b"")

    assert refused.value.status_code == 400
    assert refused.value.detail == EMPTY_MESSAGE
    assert not db.added


async def test_file_with_rows_still_imports_as_before(pipeline):
    db = _db()
    data = _xlsx(
        [
            ["NameEn", "Mobile", "BuildingNameEn", "UnitNumber"],
            ["Ali", "0501234567", "Tower A", "101"],
        ]
    )

    result = await _upload(db, "owners.xlsx", data)

    assert result is pipeline
    record = next(item for item in db.added if isinstance(item, CrmContactImport))
    assert record.status == "completed"
    assert record.summary["total_rows"] == 1
    assert [item for item in db.added if isinstance(item, CrmContactList)]
    db.delete.assert_not_awaited()
