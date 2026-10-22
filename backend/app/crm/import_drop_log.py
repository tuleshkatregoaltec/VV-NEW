from __future__ import annotations

import gzip
import json
import logging
from collections import Counter
from functools import lru_cache
from typing import Any

from botocore.exceptions import ClientError

from app.config import settings
from app.core.object_storage import ObjectStorageClient
from app.crm.identity import ParsedContact
from app.crm.import_models import CrmDroppedRow, CrmDroppedRowsResponse

logger = logging.getLogger(__name__)

_SEARCH_FIELDS = ("name", "building", "unit")


@lru_cache(maxsize=1)
def _storage() -> ObjectStorageClient:
    return ObjectStorageClient(
        endpoint_url=(
            str(settings.OBJECT_STORAGE_ENDPOINT_URL)
            if settings.OBJECT_STORAGE_ENDPOINT_URL
            else None
        ),
        access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY_ID,
        secret_access_key=settings.OBJECT_STORAGE_SECRET_ACCESS_KEY,
        bucket_name=settings.OBJECT_STORAGE_BUCKET,
        region=settings.OBJECT_STORAGE_REGION,
        use_path_style=settings.OBJECT_STORAGE_USE_PATH_STYLE,
    )


def drop_log_key(organization_id: str, import_id: int, revision: str | None = None) -> str:
    """Private, organization-scoped location of one import's dropped-rows log."""
    name = f"dropped-rows-{revision}" if revision else "dropped-rows"
    return f"crm-imports/{organization_id}/{import_id}/{name}.json.gz"


def no_unit_rows(candidates: list[ParsedContact]) -> list[dict[str, Any]]:
    """Rows kept by the parser but dropped after matching because no unit could be found.

    Uses the same condition as identity._drop_candidates_without_unit.
    """
    return [
        {
            "sheet": candidate.source_sheet,
            "row": candidate.source_row_number,
            "reason": "no_unit",
            "name": candidate.contact_name,
            "building": candidate.building_name or candidate.project_name,
            "unit": "",
            "date": candidate.transaction_date.isoformat() if candidate.transaction_date else "",
            "price": str(candidate.transaction_price_aed or ""),
            "role": candidate.party_role,
            "procedure": candidate.procedure_type,
        }
        for candidate in candidates
        if not candidate.unit_number
    ]


def count_by_reason(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row["reason"]) for row in rows))


_LOGGED_ROW_NUMBERS = 10


def log_drop_summary(
    import_id: int | None,
    *,
    total_rows: int,
    kept: int,
    merged: int,
    rows: list[dict[str, Any]],
) -> None:
    """Write what an import dropped to the server log, without contact data.

    Counts per reason, then per sheet and reason with the first row numbers and the roles or
    procedures involved, so a row can be found in the file or in the Dropped list. Names,
    phones, emails, buildings and units are never logged.
    """
    reasons = " · ".join(
        f"{reason} {n}" for reason, n in Counter(r["reason"] for r in rows).most_common()
    )
    logger.info(
        "CRM import #%s: %s rows -> %s kept, %s merged, %s dropped (%s)",
        import_id,
        total_rows,
        kept,
        merged,
        len(rows),
        reasons or "none",
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((str(row["sheet"]), str(row["reason"])), []).append(row)
    for (sheet, reason), group in sorted(groups.items(), key=lambda item: -len(item[1])):
        numbers = ", ".join(str(row["row"]) for row in group[:_LOGGED_ROW_NUMBERS])
        more = len(group) - _LOGGED_ROW_NUMBERS
        field = {"not_a_sale": "procedure", "not_buyer_seller": "role"}.get(reason)
        values = ""
        if field:
            top = Counter(str(row.get(field) or "(empty)") for row in group).most_common(3)
            values = f" · {field}s: " + ", ".join(f"{value} {n}" for value, n in top)
        logger.info(
            "CRM import #%s dropped · sheet %r · %s · %s rows%s · rows %s%s",
            import_id,
            sheet,
            reason,
            len(group),
            values,
            numbers,
            f" (+{more} more)" if more > 0 else "",
        )


async def save_drop_log(
    organization_id: str, import_id: int, rows: list[dict[str, Any]], *, revision: str | None = None
) -> bool:
    """Store the log; a storage failure never fails the import, it only leaves no log."""
    payload = gzip.compress(json.dumps(rows, ensure_ascii=False).encode())
    try:
        await _storage().upload(drop_log_key(organization_id, import_id, revision), payload)
    except Exception:
        # No row values here: the log holds contact data.
        logger.warning("CRM dropped-rows log could not be saved", extra={"import_id": import_id})
        return False
    return True


async def load_drop_log(
    organization_id: str, import_id: int, *, revision: str | None = None
) -> list[dict[str, Any]] | None:
    """The stored rows, or None when this import has no log (imported before it existed)."""
    try:
        payload = await _storage().download(drop_log_key(organization_id, import_id, revision))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return None
        raise
    return json.loads(gzip.decompress(payload))


async def delete_drop_log(
    organization_id: str, import_id: int, *, revision: str | None = None
) -> None:
    try:
        await _storage().delete_many([drop_log_key(organization_id, import_id, revision)])
    except Exception:
        logger.warning("CRM dropped-rows log could not be deleted", extra={"import_id": import_id})


def page_drop_log(
    rows: list[dict[str, Any]] | None,
    *,
    reason: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> CrmDroppedRowsResponse:
    if rows is None:
        return CrmDroppedRowsResponse(
            available=False, total=0, offset=0, limit=limit, by_reason={}, rows=[]
        )
    by_reason = count_by_reason(rows)
    selected = [row for row in rows if not reason or row["reason"] == reason]
    needle = (search or "").strip().casefold()
    if needle:
        selected = [
            row
            for row in selected
            if any(needle in str(row.get(field) or "").casefold() for field in _SEARCH_FIELDS)
        ]
    return CrmDroppedRowsResponse(
        available=True,
        total=len(selected),
        offset=offset,
        limit=limit,
        by_reason=by_reason,
        rows=[CrmDroppedRow.model_validate(row) for row in selected[offset : offset + limit]],
    )
