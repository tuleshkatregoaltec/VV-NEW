from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import import_repository
from app.crm.ai import _llm_mapping
from app.crm.cadastral import resolve_cadastral_plots, villa_sales_for_plots
from app.crm.identity import (
    _candidate_owner_key,
)
from app.crm.import_context import prepare_file_context, property_details, recover_from_file_context
from app.crm.import_drop_log import (
    count_by_reason,
    delete_drop_log,
    load_drop_log,
    log_drop_summary,
    page_drop_log,
    save_drop_log,
)
from app.crm.import_events import IMPORT_RESOLVER_VERSION
from app.crm.import_inventory import SourcePropertyInventory
from app.crm.import_models import (
    CrmContactImport,
    CrmContactImportResponse,
    CrmContactImportSummary,
    CrmContactList,
    CrmDroppedRowsResponse,
    CrmImportPropertiesResponse,
    CrmImportProperty,
    CrmImportPropertyRecord,
    CrmPropertyContactClaim,
    CrmSheetMapping,
)
from app.crm.import_parsing import (
    _inspect_csv,
    _inspect_xlsx,
    _mapping_response,
    _missing_owner_name,
    _parse_csv,
    _parse_xlsx,
    _summary,
)
from app.crm.owners import _invalidate_owner_summary_cache
from app.crm.ownership import (
    _apply_evidence,
    _recover_property_identity_from_near_transaction,
    _recover_property_identity_from_transaction,
    _rental_evidence,
    _sales_evidence,
)
from app.crm.registry_fallback import _apply_registry_unit_fallbacks
from app.crm.workspaces import _enrich_workspaces

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 50 * 1024 * 1024
_EMPTY_FILE = "File is empty, so nothing was imported."


async def process_contact_import(
    db: AsyncSession,
    *,
    upload: UploadFile,
    name: str,
    user_id: str,
    organization_id: str,
    reprocess_import_id: int | None = None,
) -> CrmContactImportResponse:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".csv"}:
        raise HTTPException(status_code=400, detail="Upload an XLSX or CSV file")
    data = await upload.read(_MAX_FILE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail=_EMPTY_FILE)
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="The uploaded file exceeds 50 MB")
    sha256 = hashlib.sha256(data).hexdigest()
    now = datetime.now(timezone.utc)
    replay = reprocess_import_id is not None
    existing_claims = {}
    preserved_owner_keys = {}
    previous_mapping = {}
    previous_summary = {}
    if replay:
        record = await import_repository.lock_import_for_replay(
            db, reprocess_import_id, organization_id
        )
        if not record or record.sha256 != sha256:
            raise HTTPException(status_code=409, detail="Replay requires the original import file.")
        if record.status == "processing":
            raise HTTPException(status_code=409, detail="This import is already processing.")
        if record.mapping.get("resolver_version") == IMPORT_RESOLVER_VERSION:
            return await _import_response(db, record)
        previous_mapping = dict(record.mapping)
        previous_summary = dict(record.summary)
        claims = await import_repository.list_rental_refresh_claims(db, record.id, organization_id)
        existing_claims = {(c.source_sheet, c.source_row_number): c for c in claims}
        # Previously collapsed rows may also own notes through their selected claim.
        for claim in claims:
            preserved_owner_keys[(claim.source_sheet, claim.source_row_number)] = claim.owner_key
            for source in (claim.evidence or {}).get("source_rows", []):
                sheet, _, number = source.rpartition(":")
                if number.isdigit():
                    preserved_owner_keys[(sheet, int(number))] = claim.owner_key
    else:
        existing = await import_repository.find_import_by_digest(db, organization_id, sha256)
        if existing:
            return await _import_response(db, existing)
        record = CrmContactImport(
            organization_id=organization_id,
            created_by_user_id=user_id,
            name=name.strip()[:200] or Path(filename).stem[:200],
            original_filename=filename,
            sha256=sha256,
            size_bytes=len(data),
            status="processing",
            mapping={},
            summary={},
            created_at=now,
        )
        import_repository.save(db, record)
        await db.commit()
        await db.refresh(record)
    if record.id is None:
        raise HTTPException(status_code=500, detail="Import record could not be created")

    try:
        inspector = _inspect_xlsx if suffix == ".xlsx" else _inspect_csv
        profiles = await asyncio.to_thread(inspector, data, filename)
        # Replay must be deterministic and must not send source data to a model.
        if not replay:
            profiles = [await _llm_mapping(profile) for profile in profiles]
        parser = _parse_xlsx if suffix == ".xlsx" else _parse_csv
        dropped: list[dict[str, Any]] = []
        candidates, total_rows = await asyncio.to_thread(parser, data, filename, profiles, dropped)
        if not total_rows:
            if replay:
                raise ValueError(
                    "Replay parsed no source records; existing results were preserved."
                )
            # Nothing to import: remove the record so no empty import or list is left behind.
            await import_repository.delete_record(db, record)
            await db.commit()
            raise HTTPException(status_code=400, detail=_EMPTY_FILE)
        missing_sources = existing_claims.keys() - {
            (candidate.source_sheet, candidate.source_row_number) for candidate in candidates
        }
        nameless_sources = {
            (row["sheet"], row["row"]) for row in dropped if row["reason"] == "no_name"
        }
        if any(
            source not in nameless_sources
            or not _missing_owner_name(existing_claims[source].contact_name)
            for source in missing_sources
        ):
            raise ValueError("Replay would lose named source records; results were preserved.")
        await prepare_file_context(candidates)
        await _recover_property_identity_from_transaction(candidates)
        await _recover_property_identity_from_near_transaction(candidates)
        await recover_from_file_context(candidates)
        sales = await _sales_evidence(candidates)
        await _apply_registry_unit_fallbacks(candidates, sales)
        rentals = await _rental_evidence(candidates)
        await resolve_cadastral_plots(candidates)
        sales.update(await villa_sales_for_plots(candidates))
        for candidate in candidates:
            _apply_evidence(candidate, sales=sales, rentals=rentals)
        # Each source event is persisted independently. Repeated owner/property
        # rows are history, not duplicates; unresolved identities remain reviewable.
        dropped_no_unit = duplicate_rows_collapsed = 0
        for source in missing_sources:
            await import_repository.delete_record(db, existing_claims[source])

        contact_list = await import_repository.find_import_list(db, record) if replay else None
        if contact_list is None:
            contact_list = CrmContactList(
                organization_id=organization_id,
                created_by_user_id=user_id,
                source_import_id=record.id,
                name=record.name,
                description=f"Imported from {filename}",
                created_at=now,
                updated_at=now,
            )
        import_repository.save(db, contact_list)
        await db.flush()
        if contact_list.id is None:
            raise RuntimeError("Contact list could not be created")

        for start in range(0, len(candidates), 1_000):
            batch = [
                CrmPropertyContactClaim(
                    organization_id=organization_id,
                    import_id=record.id,
                    list_id=contact_list.id,
                    source_sheet=candidate.source_sheet,
                    source_row_number=candidate.source_row_number,
                    row_fingerprint=candidate.row_fingerprint,
                    owner_key=_candidate_owner_key(candidate),
                    property_key=candidate.property_key or f"source:{candidate.row_fingerprint}",
                    contact_name=candidate.contact_name,
                    phone_primary=candidate.phone_primary,
                    phone_alternate=candidate.phone_alternate,
                    email_primary=candidate.email_primary,
                    area_name=candidate.area_name,
                    project_name=candidate.project_name,
                    building_name=candidate.building_name,
                    unit_number=candidate.unit_number,
                    property_type=candidate.property_type,
                    transaction_date=candidate.transaction_date,
                    transaction_price_aed=candidate.transaction_price_aed,
                    party_role=candidate.party_role,
                    procedure_type=candidate.procedure_type,
                    ownership_status=candidate.ownership_status,
                    confidence_score=candidate.confidence_score,
                    match_reason=candidate.match_reason,
                    lead_id=candidate.lead_id,
                    unit_candidate_key=candidate.unit_candidate_key,
                    latest_sale_date=candidate.latest_sale_date,
                    latest_sale_price_aed=candidate.latest_sale_price_aed,
                    later_sale_date=candidate.later_sale_date,
                    lease_end=candidate.lease_end,
                    annual_rent_aed=candidate.annual_rent_aed,
                    evidence=candidate.evidence,
                    created_at=now,
                    updated_at=now,
                )
                for candidate in candidates[start : start + 1_000]
            ]
            for claim in batch:
                source = (claim.source_sheet, claim.source_row_number)
                old = existing_claims.get(source)
                if source in preserved_owner_keys:
                    claim.owner_key = preserved_owner_keys[source]
                if old is not None:
                    for field_name in CrmPropertyContactClaim.model_fields:
                        if field_name not in {"id", "created_at"}:
                            setattr(old, field_name, getattr(claim, field_name))
                else:
                    import_repository.save(db, claim)
            await db.flush()

        if not replay:
            await _enrich_workspaces(
                db,
                candidates=candidates,
                user_id=user_id,
                organization_id=organization_id,
                list_name=record.name,
            )
        summary = _summary(candidates, total_rows, duplicate_rows_collapsed, dropped_no_unit)
        inventory = SourcePropertyInventory()
        for profile in profiles:
            inventory.merge(profile.property_inventory)
        summary.source_property_counts = inventory.summary()
        summary.dropped_by_reason = count_by_reason(dropped)
        log_drop_summary(
            record.id,
            total_rows=total_rows,
            kept=len(candidates),
            merged=duplicate_rows_collapsed,
            rows=dropped,
        )
        # Publish a replay log under a new key. A failed database commit must not
        # replace the log referenced by the previous saved result.
        summary.dropped_log = (
            True
            if replay and not dropped
            else await save_drop_log(
                organization_id, record.id, dropped, revision=IMPORT_RESOLVER_VERSION
            )
            if replay
            else await save_drop_log(organization_id, record.id, dropped)
        )
        if missing_sources and not summary.dropped_log:
            raise ValueError("Nameless rows cannot be removed without a saved dropped-row log.")
        record.status = "completed"
        record.error_message = None
        record.mapping = {
            "sheets": [_mapping_response(profile) for profile in profiles],
            "resolver_version": IMPORT_RESOLVER_VERSION,
        }
        if replay:
            if dropped:
                record.mapping["drop_log_revision"] = IMPORT_RESOLVER_VERSION
            record.mapping["previous_result"] = {
                "mapping": previous_mapping,
                "summary": previous_summary,
                "reprocessed_at": now.isoformat(),
            }
        record.summary = summary.model_dump()
        record.completed_at = datetime.now(timezone.utc)
        import_repository.save(db, record)
        await db.commit()
        _invalidate_owner_summary_cache(organization_id)
        return await _import_response(db, record)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CRM contact import failed", extra={"import_id": record.id})
        await db.rollback()
        if replay:
            raise HTTPException(
                status_code=422, detail="Replay failed; previous import results were preserved."
            ) from exc
        await delete_drop_log(organization_id, record.id)
        record.status = "failed"
        record.error_message = str(exc)[:2_000]
        record.completed_at = datetime.now(timezone.utc)
        import_repository.save(db, record)
        await db.commit()
        raise HTTPException(status_code=422, detail=f"Import failed: {exc}") from exc


async def _import_response(
    db: AsyncSession,
    record: CrmContactImport,
) -> CrmContactImportResponse:
    list_result = await import_repository.find_import_list(db, record)
    contact_list = list_result
    raw_sheets = record.mapping.get("sheets", []) if record.mapping else []
    return CrmContactImportResponse(
        id=record.id or 0,
        list_id=contact_list.id if contact_list else None,
        name=record.name,
        original_filename=record.original_filename,
        status=record.status,  # type: ignore[arg-type]
        summary=CrmContactImportSummary.model_validate(record.summary or {}),
        mappings=[CrmSheetMapping.model_validate(item) for item in raw_sheets],
        error_message=record.error_message,
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


async def list_contact_imports(
    db: AsyncSession,
    *,
    organization_id: str,
) -> list[CrmContactImportResponse]:
    result = await import_repository.list_imports(db, organization_id)
    return [await _import_response(db, record) for record in result]


async def rename_contact_import(
    db: AsyncSession,
    *,
    import_id: int,
    name: str,
    organization_id: str,
) -> CrmContactImportResponse:
    result = await import_repository.get_import_for_org(db, import_id, organization_id)
    record = result
    if not record:
        raise HTTPException(status_code=404, detail="Owner-data import not found")
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="Import name cannot be empty")
    record.name = cleaned[:200]
    import_repository.save(db, record)
    list_result = await import_repository.find_import_list_for_org(db, import_id, organization_id)
    contact_list = list_result
    if contact_list:
        contact_list.name = record.name
        contact_list.updated_at = datetime.now(timezone.utc)
        import_repository.save(db, contact_list)
    await db.commit()
    await db.refresh(record)
    return await _import_response(db, record)


async def delete_contact_import(
    db: AsyncSession,
    *,
    import_id: int,
    organization_id: str,
) -> None:
    result = await import_repository.get_import_for_org(db, import_id, organization_id)
    record = result
    if not record:
        raise HTTPException(status_code=404, detail="Owner-data import not found")

    # Remove only records published by this import. Agent-maintained lead
    # workspaces, notes, and manually saved properties are intentionally kept.
    await import_repository.delete_import_claims(db, import_id, organization_id)
    await import_repository.delete_import_lists(db, import_id, organization_id)
    await import_repository.delete_record(db, record)
    await db.commit()
    _invalidate_owner_summary_cache(organization_id)
    revisions = {None}
    mapping = record.mapping or {}
    while mapping:
        revisions.add(mapping.get("drop_log_revision"))
        mapping = mapping.get("previous_result", {}).get("mapping", {})
    for revision in revisions:
        await delete_drop_log(organization_id, import_id, revision=revision)


async def list_dropped_rows(
    db: AsyncSession,
    *,
    import_id: int,
    organization_id: str,
    reason: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> CrmDroppedRowsResponse:
    """One page of the rows an import did not keep, read from its dropped-rows log."""
    record = await import_repository.get_import_for_org(db, import_id, organization_id)
    if not record:
        raise HTTPException(status_code=404, detail="Owner-data import not found")
    rows = (
        []
        if record.mapping.get("resolver_version") == IMPORT_RESOLVER_VERSION
        and not record.summary.get("dropped_by_reason")
        else await load_drop_log(
            organization_id, import_id, revision=record.mapping.get("drop_log_revision")
        )
    )
    page = page_drop_log(rows, reason=reason, search=search, limit=limit, offset=offset)
    # The search text is not logged: it may be an owner's name.
    logger.info(
        "CRM dropped rows viewed · import #%s · reason=%s · search=%s · %s",
        import_id,
        reason or "all",
        "yes" if search and search.strip() else "no",
        f"{len(page.rows)} of {page.total} shown" if page.available else "no log for this import",
    )
    return page


_CURRENT_OWNER_STATUSES = ("verified_current_owner", "probable_current_owner")
# A property lists at most this many of its rows; a unit resold many times stays readable.
_PROPERTY_RECORDS_MAX = 50


def _group_properties(claims: list[CrmPropertyContactClaim]) -> list[CrmImportProperty]:
    """One entry per property key, sorted by building/project then unit, newest rows first."""
    grouped: dict[str, list[CrmPropertyContactClaim]] = {}
    for claim in claims:
        grouped.setdefault(claim.property_key, []).append(claim)
    properties: list[CrmImportProperty] = []
    for key, group in grouped.items():
        group.sort(
            key=lambda c: (c.transaction_date is not None, c.transaction_date, c.source_row_number),
            reverse=True,
        )
        first = group[0]
        current = max(
            (c for c in group if c.ownership_status in _CURRENT_OWNER_STATUSES),
            key=lambda c: c.confidence_score,
            default=None,
        )
        dated = next((c for c in group if c.transaction_date), None)
        properties.append(
            CrmImportProperty(
                property_key=key,
                area=first.area_name,
                project=first.project_name,
                building=first.building_name,
                unit=first.unit_number,
                property_type=first.property_type,
                rows=len(group),
                buyers=sum(c.party_role == "Buyer" for c in group),
                sellers=sum(c.party_role == "Seller" for c in group),
                current_owner=current.contact_name if current else None,
                latest_date=dated.transaction_date if dated else None,
                latest_price_aed=dated.transaction_price_aed if dated else None,
                records=[
                    CrmImportPropertyRecord(
                        name=c.contact_name,
                        role=c.party_role,
                        sale_date=c.transaction_date,
                        price_aed=c.transaction_price_aed,
                        status=c.ownership_status,
                        sheet=c.source_sheet,
                        row=c.source_row_number,
                        procedure=c.procedure_type,
                        event_family=(c.evidence or {}).get("event_family", ""),
                        identifiers=(c.evidence or {}).get("source_identifiers", {}),
                        match_reason=c.match_reason,
                        property_details=property_details(c.evidence or {}),
                    )
                    for c in group[:_PROPERTY_RECORDS_MAX]
                ],
            )
        )
    properties.sort(key=lambda p: ((p.building or p.project).casefold(), p.unit.casefold()))
    return properties


async def list_import_properties(
    db: AsyncSession,
    *,
    import_id: int,
    organization_id: str,
    search: str | None,
    limit: int,
    offset: int,
) -> CrmImportPropertiesResponse:
    """One page of an import's distinct properties, each with its kept rows."""
    record = await import_repository.get_import_for_org(db, import_id, organization_id)
    if not record:
        raise HTTPException(status_code=404, detail="Owner-data import not found")
    claims = await import_repository.list_import_property_claims(
        db, organization_id, import_id, search
    )
    properties = _group_properties(list(claims))
    return CrmImportPropertiesResponse(
        total=len(properties),
        offset=offset,
        limit=limit,
        rows=properties[offset : offset + limit],
    )
