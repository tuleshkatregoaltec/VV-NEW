from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import match_repository
from app.crm.import_models import CrmPropertyContactClaim
from app.crm.inbound_models import (
    CrmInboundEnquiry,
    CrmInboundMatch,
    InboundMatchesResponse,
    InboundMatchResponse,
)
from app.crm.inbound_policy import (
    _amount,
    _locations,
    _qualification_complete,
    _utcnow,
)
from app.crm.matching_policy import (
    _bedroom_fit,
    _budget_fit,
    _json_snapshot,
    _listing_property_type,
    _location_fit,
    _property_fit,
    _violates_strict_fields,
)
from app.crm.opportunities import (
    get_instruction_evidence,
    list_matching_rental_opportunities,
    list_sales_inventory,
)
from app.listings.service import get_inventory_freshness, list_listings_from_clickhouse
from app.supply.service import get_catalog_freshness, list_matching_inventory

_ALGORITHM_VERSION = "inbound-v1"


def _match_response(item: CrmInboundMatch) -> InboundMatchResponse:
    return InboundMatchResponse(
        id=item.id or 0,
        target_type=item.target_type,  # type: ignore[arg-type]
        target_key=item.target_key,
        state=item.state,  # type: ignore[arg-type]
        fit=item.fit,  # type: ignore[arg-type]
        title=item.title,
        subtitle=item.subtitle,
        reasons=item.reasons,
        missing_fields=item.missing_fields,
        snapshot=item.snapshot,
        generated_at=item.generated_at,
    )


async def _upsert_match(
    db: AsyncSession,
    *,
    enquiry: CrmInboundEnquiry,
    target_type: str,
    target_key: str,
    score: int,
    title: str,
    subtitle: str,
    reasons: list[str],
    breakdown: dict[str, Any],
    snapshot: dict[str, Any],
) -> CrmInboundMatch | None:
    if score < 50:
        return None
    result = await match_repository.find_target_match(db, enquiry, target_key, target_type)
    item = result
    now = _utcnow()
    missing_fields = [
        label
        for label, present in (
            ("location", bool(_locations(enquiry.criteria))),
            (
                "budget",
                bool(
                    enquiry.criteria.get("budget_min_aed")
                    or enquiry.criteria.get("budget_max_aed")
                    or enquiry.criteria.get("expected_price_aed")
                ),
            ),
            (
                "property details",
                bool(
                    enquiry.criteria.get("property_type")
                    or enquiry.criteria.get("bedrooms_min") is not None
                    or enquiry.criteria.get("unit_number")
                ),
            ),
        )
        if not present
    ]
    fit = "exact" if score >= 85 and not missing_fields else "near" if score >= 65 else "review"
    if item is None:
        item = CrmInboundMatch(
            enquiry_id=enquiry.id or 0,
            organization_id=enquiry.organization_id,
            target_type=target_type,
            target_key=target_key,
            score=score,
            title=title,
            subtitle=subtitle,
            reasons=reasons,
            score_breakdown=breakdown,
            fit=fit,
            missing_fields=missing_fields,
            snapshot=snapshot,
            generated_at=now,
            updated_at=now,
        )
    elif item.state != "dismissed":
        if item.state == "stale":
            item.state = "suggested"
        item.score = score
        item.title = title
        item.subtitle = subtitle
        item.reasons = reasons
        item.score_breakdown = breakdown
        item.fit = fit
        item.missing_fields = missing_fields
        item.snapshot = snapshot
        item.generated_at = now
        item.updated_at = now
    match_repository.save(db, item)
    return item


async def refresh_inbound_matches(
    db: AsyncSession,
    *,
    enquiry_id: int,
    organization_id: str,
    expand_units: bool = False,
) -> InboundMatchesResponse:
    enquiry = await match_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    if not _qualification_complete(enquiry):
        raise HTTPException(
            status_code=409,
            detail="Confirm the enquiry intent and minimum property requirements before matching",
        )
    criteria = enquiry.criteria or {}
    await match_repository.mark_refreshable_matches_stale(db, enquiry_id)
    generated: list[CrmInboundMatch] = []

    if enquiry.intent in {"buy", "rent"}:
        requested_locations = _locations(criteria)
        claims_result = await match_repository.find_owner_candidates(
            db, organization_id, requested_locations
        )
        best_by_owner: dict[str, CrmPropertyContactClaim] = {}
        for claim in claims_result:
            current = best_by_owner.get(claim.owner_key)
            if current is None or claim.confidence_score > current.confidence_score:
                best_by_owner[claim.owner_key] = claim
        for claim in best_by_owner.values():
            location, location_reason = _location_fit(
                criteria, claim.area_name, claim.project_name, claim.building_name
            )
            owner_value = (
                claim.annual_rent_aed if enquiry.intent == "rent" else claim.latest_sale_price_aed
            )
            budget, budget_reason = _budget_fit(criteria, owner_value)
            property_points = _property_fit(criteria, claim.property_type)
            if _violates_strict_fields(
                enquiry.strict_fields,
                location_points=location,
                budget_points=budget,
                bedroom_points=5,
                property_points=property_points,
            ):
                continue
            actionability = 20 if claim.phone_primary or claim.email_primary else 10
            timing = 10 if claim.lease_end else 3
            score = min(100, location + budget + property_points + 15 + actionability + timing)
            item = await _upsert_match(
                db,
                enquiry=enquiry,
                target_type="owner",
                target_key=claim.owner_key,
                score=score,
                title=claim.contact_name,
                subtitle=f"{claim.building_name or claim.project_name} · Unit {claim.unit_number}",
                reasons=[
                    location_reason,
                    budget_reason,
                    "Current ownership evidence",
                    "Contact available" if actionability == 20 else "Contact enrichment needed",
                ],
                breakdown={
                    "requirements": location + budget + property_points + 15,
                    "actionability": actionability,
                    "timing": timing,
                },
                snapshot={
                    "owner_key": claim.owner_key,
                    "phone": claim.phone_primary,
                    "email": claim.email_primary,
                    "area_name": claim.area_name,
                    "project_name": claim.project_name,
                    "building_name": claim.building_name,
                    "unit_number": claim.unit_number,
                    "latest_sale_price_aed": claim.latest_sale_price_aed,
                    "lease_end": claim.lease_end.isoformat() if claim.lease_end else None,
                },
            )
            if item:
                generated.append(item)

        for lead in await list_matching_rental_opportunities(
            next(iter(_locations(criteria)), ""), str(criteria.get("property_type") or "")
        ):
            location, location_reason = _location_fit(
                criteria,
                str(lead.get("area_name") or ""),
                str(lead.get("project_name") or ""),
                str(lead.get("building_name") or ""),
            )
            price = (
                int(lead.get("annual_rent_aed") or 0) or None
                if enquiry.intent == "rent"
                else int(lead.get("matched_sale_price") or lead.get("purchase_price_aed") or 0)
                or None
            )
            budget, budget_reason = _budget_fit(criteria, price)
            bedrooms = str(lead.get("bedrooms") or "")
            bedroom, bedroom_reason = _bedroom_fit(criteria, bedrooms)
            property_type = str(lead.get("property_type") or "")
            property_points = _property_fit(criteria, property_type)
            if _violates_strict_fields(
                enquiry.strict_fields,
                location_points=location,
                budget_points=budget,
                bedroom_points=bedroom,
                property_points=property_points,
            ):
                continue
            score = min(100, location + budget + bedroom + property_points + 7 + 10 + 10)
            item = await _upsert_match(
                db,
                enquiry=enquiry,
                target_type="unit",
                target_key=str(lead["unit_candidate_key"]),
                score=score,
                title=f"{lead.get('building_name') or 'Building'} · Unit {lead.get('unit_number') or 'unresolved'}",
                subtitle=f"{bedrooms or 'Layout pending'} · AED {price:,}"
                if price
                else f"{bedrooms or 'Layout pending'} · value pending",
                reasons=[
                    location_reason,
                    budget_reason,
                    bedroom_reason,
                    f"Lease ends {lead.get('lease_end')}",
                ],
                breakdown={
                    "requirements": location + budget + bedroom + property_points,
                    "actionability": 7,
                    "timing": 10,
                },
                snapshot=_json_snapshot(lead),
            )
            if item:
                generated.append(item)

        if expand_units and enquiry.intent == "buy":
            sales_rows = await list_sales_inventory(
                {"area": requested_locations[0] if requested_locations else ""}
            )
            existing_keys = {item.target_key for item in generated if item.target_type == "unit"}
            for row in sales_rows:
                target_key = (
                    f"registry:{row.get('building_key', '')}|{row.get('normalized_unit', '')}"
                )
                if target_key in existing_keys:
                    continue
                location, location_reason = _location_fit(
                    criteria, str(row.get("area_name") or ""), str(row.get("building_name") or "")
                )
                price = int(row.get("sale_amount_aed") or 0) or None
                budget, budget_reason = _budget_fit(criteria, price)
                bedroom, bedroom_reason = _bedroom_fit(criteria, str(row.get("bedrooms") or ""))
                property_points = _property_fit(criteria, str(row.get("property_type") or ""))
                if _violates_strict_fields(
                    enquiry.strict_fields,
                    location_points=location,
                    budget_points=budget,
                    bedroom_points=bedroom,
                    property_points=property_points,
                ):
                    continue
                score = min(100, location + budget + bedroom + property_points + 5 + 5 + 10)
                latest_sale_date = row.get("latest_sale_date")
                item = await _upsert_match(
                    db,
                    enquiry=enquiry,
                    target_type="unit",
                    target_key=target_key,
                    score=score,
                    title=f"{row.get('building_name') or 'Building'} · Unit {row.get('unit_number')}",
                    subtitle=f"{row.get('bedrooms') or 'Layout pending'} · {f'AED {price:,}' if price else 'value pending'}",
                    reasons=[
                        location_reason,
                        budget_reason,
                        bedroom_reason,
                        "Expanded sales-registry universe",
                    ],
                    breakdown={
                        "requirements": location + budget + bedroom + property_points,
                        "actionability": 5,
                        "timing": 5,
                    },
                    snapshot={
                        **row,
                        "latest_sale_date": latest_sale_date.isoformat()
                        if isinstance(latest_sale_date, date)
                        else latest_sale_date,
                        "source": "sales_registry",
                    },
                )
                if item:
                    generated.append(item)
                    existing_keys.add(target_key)
    else:
        reverse_intent = "buy" if enquiry.intent == "sell" else "rent"
        building = str(criteria.get("building") or "").strip()
        unit_number = str(criteria.get("unit_number") or "").strip()
        if building and unit_number:
            evidence_rows = await get_instruction_evidence(
                {"building": building, "unit_number": unit_number}
            )
            if evidence_rows:
                evidence = evidence_rows[0]
                sale_price = int(evidence.get("sale_amount_aed") or 0)
                item = await _upsert_match(
                    db,
                    enquiry=enquiry,
                    target_type="unit",
                    target_key=f"instruction:{evidence.get('sale_key')}",
                    score=98,
                    title=f"{evidence.get('building_name')} · Unit {evidence.get('unit_number')}",
                    subtitle=f"Latest recorded sale · AED {sale_price:,}",
                    reasons=[
                        "Exact building and unit match",
                        f"Purchased {evidence.get('transaction_date')}",
                        "Financing-at-purchase evidence available"
                        if evidence.get("ltv_pct") is not None
                        else "Financing evidence not exposed",
                    ],
                    breakdown={"identity": 50, "evidence": 28, "actionability": 20},
                    snapshot={**_json_snapshot(evidence), "source": "sales_registry"},
                )
                if item:
                    generated.append(item)
        reverse_result = await match_repository.list_reverse_demand(
            db, enquiry, organization_id, reverse_intent
        )
        subject_locations = _locations(criteria)
        if not subject_locations and building:
            subject_locations = [building]
        subject_value = criteria.get("expected_price_aed") or criteria.get("budget_max_aed")
        for demand in reverse_result:
            locations = _locations(demand.criteria)
            location = (
                20
                if not subject_locations
                or not locations
                or any(
                    a.casefold() in b.casefold() or b.casefold() in a.casefold()
                    for a in subject_locations
                    for b in locations
                )
                else 0
            )
            budget, budget_reason = _budget_fit(demand.criteria, int(subject_value or 0) or None)
            bedroom, bedroom_reason = _bedroom_fit(
                demand.criteria, str(criteria.get("bedrooms_min", ""))
            )
            score = min(100, location + budget + bedroom + 10 + 20 + 10)
            contact = await match_repository.get_crm_inbound_contact(db, demand.contact_id)
            if not contact:
                continue
            item = await _upsert_match(
                db,
                enquiry=enquiry,
                target_type="demand",
                target_key=str(demand.id),
                score=score,
                title=contact.full_name,
                subtitle=f"Active {reverse_intent} enquiry · {demand.campaign or demand.source}",
                reasons=["Reverse demand match", budget_reason, bedroom_reason],
                breakdown={
                    "requirements": location + budget + bedroom + 10,
                    "actionability": 20,
                    "timing": 10,
                },
                snapshot={
                    "enquiry_id": demand.id,
                    "criteria": demand.criteria,
                    "phone": contact.phone,
                    "email": contact.email,
                },
            )
            if item:
                generated.append(item)

    listing_last_refresh, listing_fresh = await get_inventory_freshness()
    offplan_last_refresh = await get_catalog_freshness()

    if listing_last_refresh and enquiry.intent in {"buy", "rent"}:
        requested_locations = _locations(criteria)
        listings = await list_listings_from_clickhouse(
            mode="sale" if enquiry.intent == "buy" else "rent",
            limit=60,
            area=requested_locations[0] if requested_locations else None,
            property_type=_listing_property_type(criteria.get("property_type")),
            bedrooms=str(criteria.get("bedrooms_min"))
            if criteria.get("bedrooms_min") is not None
            else None,
            price_max=criteria.get("budget_max_aed"),
        )
        for listing in listings.listings:
            location, location_reason = _location_fit(
                criteria,
                listing.area_name or "",
                listing.subcommunity_name or "",
                listing.tower_name or "",
            )
            listing_price = listing.price_value
            if enquiry.intent == "rent" and listing_price:
                period = (listing.price_period or "").casefold()
                if "month" in period:
                    listing_price *= 12
                elif "week" in period:
                    listing_price *= 52
                elif "day" in period:
                    listing_price *= 365
            budget, budget_reason = _budget_fit(criteria, listing_price)
            bedroom, bedroom_reason = _bedroom_fit(criteria, listing.bedrooms or "")
            property_points = _property_fit(criteria, listing.property_type or "")
            if _violates_strict_fields(
                enquiry.strict_fields,
                location_points=location,
                budget_points=budget,
                bedroom_points=bedroom,
                property_points=property_points,
            ):
                continue
            score = min(100, location + budget + bedroom + property_points + 20 + 10)
            item = await _upsert_match(
                db,
                enquiry=enquiry,
                target_type="listing",
                target_key=listing.listing_id,
                score=score,
                title=listing.title,
                subtitle=f"{listing.tower_name or listing.area_name or 'Dubai'} · AED {listing.price_value:,}"
                if listing.price_value
                else listing.tower_name or "Dubai",
                reasons=[
                    location_reason,
                    budget_reason,
                    bedroom_reason,
                    "Fresh active listing"
                    if listing_fresh
                    else "Listing catalog candidate; availability requires confirmation",
                ],
                breakdown={
                    "requirements": location + budget + bedroom + property_points,
                    "actionability": 20,
                    "timing": 10,
                },
                snapshot=listing.model_dump(mode="json"),
            )
            if item:
                generated.append(item)

    if enquiry.intent == "buy" and offplan_last_refresh:
        matched_projects: set[str] = set()
        for project in await list_matching_inventory(next(iter(_locations(criteria)), "")):
            project_id = str(project.get("project_id") or "")
            if not project_id or project_id in matched_projects:
                continue
            location, location_reason = _location_fit(
                criteria,
                str(project.get("area_name") or ""),
                str(project.get("project_name") or ""),
            )
            price = _amount(project.get("unit_price_from")) or _amount(project.get("min_price_aed"))
            budget, budget_reason = _budget_fit(criteria, price)
            bedroom, bedroom_reason = _bedroom_fit(
                criteria, str(project.get("unit_bedrooms") or "")
            )
            property_points = _property_fit(criteria, str(project.get("unit_type") or ""))
            if criteria.get("bedrooms_min") is not None and bedroom == 0:
                continue
            if criteria.get("property_type") and property_points == 0:
                continue
            if _violates_strict_fields(
                enquiry.strict_fields,
                location_points=location,
                budget_points=budget,
                bedroom_points=bedroom,
                property_points=property_points,
            ):
                continue
            score = min(100, location + budget + bedroom + property_points + 20 + 10)
            item = await _upsert_match(
                db,
                enquiry=enquiry,
                target_type="project",
                target_key=(
                    f"{project.get('project_id')}:{project.get('unit_type_key') or 'project'}"
                ),
                score=score,
                title=str(project.get("project_name") or "Off-plan project"),
                subtitle=(
                    f"{project.get('developer_name') or 'Developer pending'} · from AED {price:,}"
                    if price
                    else f"{project.get('developer_name') or 'Developer pending'} · price pending"
                ),
                reasons=[
                    location_reason,
                    budget_reason,
                    bedroom_reason,
                    "Developer inventory catalog; availability requires confirmation",
                ],
                breakdown={
                    "requirements": location + budget + bedroom + property_points,
                    "actionability": 20,
                    "timing": 10,
                },
                snapshot={**_json_snapshot(project), "source": "developer_supply_catalog"},
            )
            if item:
                generated.append(item)
                matched_projects.add(project_id)
                if len(matched_projects) >= 20:
                    break

    await db.commit()
    stored = await match_repository.list_matches_by_score(db, enquiry_id)
    lanes: dict[str, list[InboundMatchResponse]] = {
        "owners": [],
        "units": [],
        "listings": [],
        "offplan": [],
        "demand": [],
    }
    lane_names = {
        "owner": "owners",
        "unit": "units",
        "listing": "listings",
        "project": "offplan",
        "demand": "demand",
    }
    for item in stored:
        if item.state not in {"dismissed", "stale"}:
            lanes[lane_names[item.target_type]].append(_match_response(item))
    return InboundMatchesResponse(
        enquiry_id=enquiry_id,
        listing_fresh=listing_fresh,
        listing_last_refresh=listing_last_refresh,
        offplan_catalog_available=bool(offplan_last_refresh),
        offplan_last_refresh=offplan_last_refresh,
        lanes=lanes,
    )


async def get_inbound_matches(
    db: AsyncSession, enquiry_id: int, organization_id: str
) -> InboundMatchesResponse:
    enquiry = await match_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    if not _qualification_complete(enquiry):
        return InboundMatchesResponse(
            enquiry_id=enquiry_id,
            listing_fresh=False,
            listing_last_refresh=None,
            offplan_catalog_available=False,
            offplan_last_refresh=None,
            lanes={"owners": [], "units": [], "listings": [], "offplan": [], "demand": []},
        )
    result = await match_repository.list_matches_by_score(db, enquiry_id)
    items = result
    if not any(item.state != "stale" for item in items):
        return await refresh_inbound_matches(
            db, enquiry_id=enquiry_id, organization_id=organization_id
        )
    listing_last_refresh, listing_fresh = await get_inventory_freshness()
    offplan_last_refresh = await get_catalog_freshness()
    lanes: dict[str, list[InboundMatchResponse]] = {
        "owners": [],
        "units": [],
        "listings": [],
        "offplan": [],
        "demand": [],
    }
    lane_names = {
        "owner": "owners",
        "unit": "units",
        "listing": "listings",
        "project": "offplan",
        "demand": "demand",
    }
    for item in items:
        if item.state not in {"dismissed", "stale"}:
            lanes[lane_names[item.target_type]].append(_match_response(item))
    return InboundMatchesResponse(
        enquiry_id=enquiry_id,
        listing_fresh=listing_fresh,
        listing_last_refresh=listing_last_refresh,
        offplan_catalog_available=bool(offplan_last_refresh),
        offplan_last_refresh=offplan_last_refresh,
        lanes=lanes,
    )


async def update_inbound_match_state(
    db: AsyncSession, *, match_id: int, organization_id: str, state: str
) -> InboundMatchResponse:
    item = await match_repository.get_crm_inbound_match(db, match_id)
    if not item or item.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound match not found")
    item.state = state
    item.updated_at = _utcnow()
    match_repository.save(db, item)
    await db.commit()
    await db.refresh(item)
    return _match_response(item)
