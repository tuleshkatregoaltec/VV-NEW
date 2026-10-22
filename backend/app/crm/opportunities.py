from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.crm import opportunity_queries
from app.crm.models import (
    CrmAssetBreakdownItem,
    CrmBreakdownItem,
    CrmCoverage,
    CrmDashboardResponse,
    CrmFilters,
    CrmLead,
    CrmLeadDetailResponse,
    CrmSummary,
    CrmTimelineEvent,
)
from app.crm.opportunity_queries import (
    _tables_exist,
)


async def list_matching_rental_opportunities(area: str, property_type: str) -> list[dict[str, Any]]:
    return await opportunity_queries.fetch_matching_rentals(
        {
            "area": area,
            "property_type": property_type,
        }
    )


async def list_sales_inventory(parameters):
    return await opportunity_queries.fetch_sales_inventory(parameters)


async def get_instruction_evidence(parameters):
    return await opportunity_queries.fetch_instruction_evidence(parameters)


logger = logging.getLogger(__name__)


def _lead_from_row(row: dict[str, Any]) -> CrmLead:
    # Saved-lead joins return l.* fields with the table alias in their names.
    row = {key.removeprefix("l."): value for key, value in row.items()}
    days = int(row["days_to_expiry"])
    status = str(row["lead_status"])
    expired = status == "expired"
    asset_class = str(row.get("asset_class") or "other")
    property_type = str(row.get("property_type") or "Property")
    matched_sales_property_type = str(row.get("latest_property_type") or "").strip() or None
    property_subtype = str(row.get("property_subtype") or property_type)
    unit_number = str(row.get("unit_number") or "").strip() or None
    unit_resolved = bool(unit_number and row.get("unit_match_status") == "unique")
    ownership_timing = str(
        row.get("ownership_timing")
        or ("stable_rental_history" if unit_resolved else "unit_unresolved")
    )
    lease_evidence_state = str(
        row.get("lease_evidence_state")
        or (
            "ownership_review"
            if ownership_timing == "sale_during_lease"
            else "unlet_signal"
            if expired and unit_resolved
            else "unit_unresolved"
            if expired
            else "upcoming"
        )
    )
    sale_candidate = bool(
        unit_resolved
        and expired
        and lease_evidence_state == "unlet_signal"
        and ownership_timing == "stable_rental_history"
    )
    matched_sale_price = int(row.get("matched_sale_price") or 0) or None
    annual_rent = int(row.get("annual_rent_aed") or 0)
    gross_yield = (
        round(annual_rent / matched_sale_price * 100, 2)
        if matched_sale_price and annual_rent
        else None
    )
    if ownership_timing == "sale_during_lease":
        reason = (
            "Ownership changed during the lease and no post-purchase lease is yet linked to "
            "this exact unit. Occupancy requires verification."
        )
    elif expired:
        reason = (
            f"Unlet signal: lease expired {abs(days)} days ago and no later registered lease "
            "was observed for this exact unit."
            if unit_resolved
            else f"Lease candidate expired {abs(days)} days ago; exact-unit and renewal status "
            "remain unresolved."
        )
    else:
        reason = f"Lease expires in {days} days; begin a renewal-status conversation."
    if ownership_timing == "sale_during_lease":
        action = "Verify the current owner and occupancy before proposing renewal or re-letting."
    elif asset_class == "commercial" and unit_resolved and expired and sale_candidate:
        action = (
            f"Use unit {unit_number} to resolve the current owner, then offer a leasing "
            "mandate and test appetite for an investment sale."
        )
    elif asset_class == "commercial" and unit_resolved:
        action = (
            f"Use unit {unit_number} to resolve the current owner, then open a renewal "
            "or commercial leasing conversation."
        )
    elif asset_class == "commercial" and expired and sale_candidate:
        action = (
            "Resolve the exact unit, then offer a commercial leasing mandate and test "
            "appetite for an investment sale."
        )
    elif asset_class == "commercial":
        action = (
            "Resolve the exact unit, then contact the owner about renewal or commercial re-letting."
        )
    elif unit_resolved and expired and sale_candidate:
        action = (
            f"Use unit {unit_number} to resolve the current owner, then offer re-letting "
            "and test appetite for a sale."
        )
    elif unit_resolved:
        action = (
            f"Use unit {unit_number} to resolve the current owner, then open a renewal "
            "or re-letting conversation."
        )
    elif expired and sale_candidate:
        action = "Resolve the exact unit, then offer re-letting and test appetite for a sale."
    else:
        action = "Resolve the exact unit, then contact the owner about renewal or re-letting."
    if row.get("_legacy_identity"):
        reason = (
            "Saved rental snapshot from before the data refresh; current lease status needs review."
        )
        action = "Check the current unit and lease evidence before contacting the owner."
    return CrmLead(
        lead_id=str(row["event_key"]),
        unit_candidate_key=str(row["unit_candidate_key"]),
        building_name=str(row.get("building_name") or ""),
        project_name=str(row.get("project_name") or ""),
        area_name=str(row.get("area_name") or ""),
        registry_building_name=str(row.get("registry_building_name") or ""),
        registry_project_name=str(row.get("registry_project_name") or ""),
        registry_area_name=str(row.get("registry_area_name") or ""),
        canonical_location_id=str(row.get("canonical_location_id") or "") or None,
        geography_match_status=str(row.get("geography_match_status") or "unmapped"),
        coordinate_precision=str(row.get("coordinate_precision") or "missing"),
        latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
        longitude=float(row["longitude"]) if row.get("longitude") is not None else None,
        asset_class=asset_class,
        property_type=property_type,
        property_subtype=property_subtype,
        matched_sales_property_type=matched_sales_property_type,
        bedrooms=str(row.get("bedrooms") or "Not stated"),
        size_sqft=float(row["size_sqft"]) if row.get("size_sqft") else None,
        contract_amount_aed=int(row.get("contract_amount_aed") or row.get("annual_rent_aed") or 0),
        contract_term_days=int(
            row.get("contract_term_days") or ((row["lease_end"] - row["lease_start"]).days + 1)
        ),
        contract_term_months=(
            float(row["contract_term_months"]) if row.get("contract_term_months") else None
        ),
        annual_rent_aed=annual_rent,
        latest_purchase_price_aed=(
            int(row["purchase_price_aed"]) if row.get("purchase_price_aed") else None
        ),
        unit_number=unit_number,
        unit_match_status=str(row.get("unit_match_status") or "unresolved"),
        matching_units=int(row.get("matching_units") or 0),
        last_purchase_date=row.get("last_purchase_date"),
        matched_sale_price_aed=matched_sale_price,
        gross_yield_pct=gross_yield,
        identity_sale_date=row.get("identity_sale_date"),
        identity_sale_price_aed=(
            int(row["identity_sale_price"]) if row.get("identity_sale_price") else None
        ),
        latest_price_per_sqft_aed=(
            float(row["latest_price_per_sqft_aed"])
            if row.get("latest_price_per_sqft_aed") is not None
            else None
        ),
        latest_capital_gain_pct=(
            float(row["latest_capital_gain_pct"])
            if row.get("latest_capital_gain_pct") is not None
            else None
        ),
        latest_ltv_pct=(
            float(row["latest_ltv_pct"]) if row.get("latest_ltv_pct") is not None else None
        ),
        latest_seller_type=str(row.get("latest_seller_type") or "") or None,
        latest_seller_transaction_count=(
            int(row["latest_seller_transaction_count"])
            if row.get("latest_seller_transaction_count") is not None
            else None
        ),
        latest_market_status=str(row.get("latest_market_status") or "") or None,
        lease_start=row["lease_start"],
        lease_end=row["lease_end"],
        days_to_expiry=days,
        status=status,
        priority=str(row["priority"]),
        score=min(int(row["raw_score"]), 100),
        contract_state=str(row.get("contract_state") or "Unknown"),
        match_confidence=str(row["match_confidence"]),
        property_identity_key=str(row.get("property_identity_key") or row["unit_candidate_key"]),
        ownership_timing=ownership_timing,
        lease_evidence_state=lease_evidence_state,
        data_complete_through=row.get("data_complete_through"),
        lead_reason=reason,
        recommended_action=action,
        sale_conversation=sale_candidate,
    )


def _dld_rooms(dxbi_bedrooms: str) -> str:
    if dxbi_bedrooms == "Studio":
        return "Studio"
    if dxbi_bedrooms.endswith(" Bed"):
        return f"{dxbi_bedrooms.removesuffix(' Bed')} B/R"
    return dxbi_bedrooms


def _location_key(value: str) -> str:
    normalized = value.lower()
    for source, target in {
        "jumeriah": "jumeirah",
        "residences": "residence",
        "apartments": "apartment",
    }.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "", normalized)


async def get_dashboard(
    *,
    status: str | None,
    priority: str | None,
    asset_class: str | None,
    property_type: str | None,
    area: str | None,
    project: str | None,
    building: str | None,
    search: str | None,
    breakdown: str,
    limit: int,
    offset: int,
    expired_days: int = 365,
    as_of: date | None = None,
) -> CrmDashboardResponse:
    anchor = as_of or date.today()
    if not await _tables_exist():
        return _empty_dashboard(anchor, limit, offset, breakdown)

    try:
        (
            lead_rows,
            summary_rows,
            breakdown_rows,
            asset_rows,
            metadata_rows,
        ) = await opportunity_queries.fetch_dashboard(
            anchor,
            area,
            asset_class,
            breakdown,
            building,
            expired_days,
            limit,
            offset,
            priority,
            project,
            property_type,
            search,
            status,
        )
    except Exception as exc:
        logger.exception("CRM dashboard query failed")
        raise HTTPException(
            status_code=503, detail="CRM lead data is temporarily unavailable"
        ) from exc

    summary_row = summary_rows[0] if summary_rows else {}
    metadata = metadata_rows[0] if metadata_rows else {}
    leads = [_lead_from_row(row) for row in lead_rows]
    return CrmDashboardResponse(
        leads=leads,
        total=int(summary_row.get("total") or 0),
        limit=limit,
        offset=offset,
        summary=CrmSummary(
            total_leads=int(summary_row.get("total") or 0),
            urgent=int(summary_row.get("urgent") or 0),
            expired_unlet=int(summary_row.get("expired") or 0),
            expiring_30_days=int(summary_row.get("expiring_30") or 0),
            expiring_31_to_60_days=int(summary_row.get("expiring_60") or 0),
            expiring_61_to_90_days=int(summary_row.get("expiring_90") or 0),
            sale_conversation_candidates=int(summary_row.get("sale_candidates") or 0),
            annual_rent_at_risk_aed=int(summary_row.get("annual_rent_at_risk") or 0),
        ),
        asset_breakdown=[
            CrmAssetBreakdownItem(
                asset_class=str(row["asset_class"]),
                total=int(row["total"]),
                urgent=int(row["urgent"]),
                exact_unit_matches=int(row["exact_unit_matches"]),
                annual_rent_at_risk_aed=int(row["annual_rent_at_risk"] or 0),
                property_types=list(row.get("property_types") or []),
            )
            for row in asset_rows
        ],
        breakdown=[
            CrmBreakdownItem(
                label=str(row["label"]),
                total=int(row["total"]),
                urgent=int(row["urgent"]),
                expired=int(row["expired"]),
                expiring_30=int(row["expiring_30"]),
                expiring_60=int(row["expiring_60"]),
                expiring_90=int(row["expiring_90"]),
                potential_value_aed=int(row["potential_value"] or 0),
            )
            for row in breakdown_rows
        ],
        breakdown_level=breakdown,
        filters=CrmFilters(
            areas=list(metadata.get("areas") or []),
            projects=list(metadata.get("projects") or []),
            buildings=list(metadata.get("buildings") or []),
            property_types=sorted(
                {
                    str(property_type)
                    for row in asset_rows
                    if not asset_class or row["asset_class"] == asset_class
                    for property_type in row.get("property_types") or []
                }
            ),
        ),
        coverage=CrmCoverage(
            rental_source="Lease transaction registry",
            rental_start_date=metadata.get("min_date"),
            rental_end_date=metadata.get("max_date"),
            rental_rows=int(metadata.get("rows") or 0),
            exact_unit_ids_available=bool(metadata.get("exact_unit_matches")),
            exact_unit_matches=int(metadata.get("exact_unit_matches") or 0),
            data_complete_through=metadata.get("data_complete_through"),
            matching_method=(
                "Unique sale match on building, area, bedroom layout, exact size, "
                "purchase evidence, and a stable exact-unit lease timeline"
            ),
            caveat=(
                "An exact-unit lease is flagged as unlet immediately after expiry when no later "
                "registered lease is observed. Sales on or after lease expiry are excluded; "
                "sales during an active tenancy remain ownership-transition reviews."
            ),
        ),
        as_of=anchor,
    )


async def get_lead_detail(lead_id: str, as_of: date | None = None) -> CrmLeadDetailResponse:
    anchor = as_of or date.today()
    if not await _tables_exist():
        raise HTTPException(status_code=404, detail="CRM lead data has not been loaded")

    current_rows = await opportunity_queries.fetch_lead(
        {
            "as_of": anchor.isoformat(),
            "expired_days": 3650,
            "lead_id": lead_id,
        }
    )
    if not current_rows:
        raise HTTPException(status_code=404, detail="CRM lead not found")
    lead = _lead_from_row(current_rows[0])
    row = current_rows[0]

    rental_query = opportunity_queries.fetch_lead_rental_history(
        {"candidate_key": lead.unit_candidate_key}
    )

    exact_sales_rows: list[dict[str, Any]] = []
    dld_sales_rows: list[dict[str, Any]] = []
    building_name = str(row.get("building_name") or "")
    if lead.unit_number and building_name:
        rental_rows, exact_sales_rows = await asyncio.gather(
            rental_query,
            opportunity_queries.fetch_exact_sale_history(
                {
                    "unit_number": lead.unit_number,
                    "building_key": _location_key(building_name),
                }
            ),
        )
    elif building_name:
        rental_rows, dld_sales_rows = await asyncio.gather(
            rental_query,
            opportunity_queries.fetch_dld_sale_history(
                {
                    "building": building_name,
                    "bedrooms": _dld_rooms(str(row.get("bedrooms") or "")),
                    "size_sqft": float(row.get("size_sqft") or 0),
                }
            ),
        )
    else:
        rental_rows = await rental_query

    timeline = [
        CrmTimelineEvent(
            event_id=str(item["event_key"]),
            event_type="lease",
            event_date=item["lease_start"],
            end_date=item["lease_end"],
            title=f"{item.get('contract_state') or 'Lease'} contract",
            description=f"Annual rent AED {int(item.get('annual_rent_aed') or 0):,}",
            amount_aed=int(item.get("annual_rent_aed") or 0),
            source="Lease transaction registry",
            confidence=lead.match_confidence,
        )
        for item in rental_rows
    ]
    for item in exact_sales_rows:
        timeline.append(
            CrmTimelineEvent(
                event_id=str(item["sale_key"]),
                event_type="sale",
                event_date=item["sale_date"],
                title=f"Unit {lead.unit_number} purchased",
                description="Exact sale record matched to this unit number.",
                amount_aed=int(item.get("sale_amount_aed") or 0),
                source="Sales transaction registry",
                confidence="high",
                ltv_pct=(float(item["ltv_pct"]) if item.get("ltv_pct") is not None else None),
                capital_gain_pct=(
                    float(item["capital_gain_pct"])
                    if item.get("capital_gain_pct") is not None
                    else None
                ),
            )
        )
    for item in dld_sales_rows:
        timeline.append(
            CrmTimelineEvent(
                event_id=str(item["transaction_id"]),
                event_type="sale",
                event_date=item["sale_date"],
                title=str(item.get("procedure_name_en") or "Candidate sale"),
                description="DLD sale matched on building, layout, and size.",
                amount_aed=int(item.get("trans_value") or 0),
                source="DLD full sales registry",
                confidence="medium",
            )
        )
    timeline.sort(key=lambda event: event.event_date, reverse=True)
    agent_signals = _agent_signals(lead)
    if lead.asset_class == "commercial":
        next_best_actions = [
            (
                f"Use unit {lead.unit_number} to resolve the current title-deed owner and contact."
                if lead.unit_number
                else "Resolve the exact unit and current title-deed owner before contact."
            ),
            "Lead with an occupancy, rent, and renewal review or a commercial leasing mandate.",
            "If the owner is open to an exit, prepare an investment-sale valuation using current commercial comparables.",
        ]
    else:
        next_best_actions = [
            (
                f"Use unit {lead.unit_number} to resolve the current title-deed owner and contact."
                if lead.unit_number
                else "Resolve the exact unit and current title-deed owner before contact."
            ),
            "Lead with a rent review and renewal or re-letting plan.",
            "If the owner is open to an exit, prepare a sale valuation using current comparables.",
        ]
    subtype_evidence = (
        f" The matched sale identifies the unit as {lead.property_subtype.lower()}."
        if lead.asset_class == "commercial"
        and lead.property_subtype != "Commercial"
        and lead.unit_number
        else ""
    )
    return CrmLeadDetailResponse(
        lead=lead,
        timeline=timeline,
        next_best_actions=next_best_actions,
        agent_signals=agent_signals,
        match_explanation=(
            (
                f"Unit {lead.unit_number} is a unique sales match on building, area, "
                "bedroom layout, exact size, and reported purchase price. Owner identity "
                f"still requires title-deed/contact enrichment.{subtype_evidence}"
            )
            if lead.unit_number
            else (
                "No unique unit number could be resolved. Lease events are grouped by "
                "building/location, bedroom layout, exact area, and latest purchase price; "
                "candidate DLD sales are not confirmed title-deed links."
            )
        ),
        generated_at=datetime.now(timezone.utc),
    )


def _agent_signals(lead: CrmLead) -> list[str]:
    signals: list[str] = []
    if lead.latest_ltv_pct is not None:
        if 0 < lead.latest_ltv_pct <= 100:
            signals.append(
                f"The source reported {lead.latest_ltv_pct:g}% LTV on the latest purchase. "
                "Treat this as financing-at-purchase evidence, not a current mortgage balance."
            )
        else:
            signals.append(
                f"The source displays {lead.latest_ltv_pct:g}% LTV on the latest purchase, "
                "which is outside a normal range; do not use it for mortgage outreach."
            )
    if lead.latest_capital_gain_pct is not None:
        direction = "gain" if lead.latest_capital_gain_pct >= 0 else "loss"
        signals.append(
            f"The source reports a {abs(lead.latest_capital_gain_pct):g}% {direction} versus "
            "the preceding recorded sale."
        )
    if lead.latest_seller_type:
        seller = lead.latest_seller_type
        if lead.latest_seller_transaction_count:
            seller += f" ({lead.latest_seller_transaction_count} source-reported transactions)"
        signals.append(f"Latest sale was recorded as sold by {seller}.")
    if lead.gross_yield_pct is not None:
        signals.append(
            f"Current contract rent implies a {lead.gross_yield_pct:.2f}% gross yield "
            "on the latest recorded purchase price."
        )
    return signals


async def resolve_candidate_key(lead_id: str) -> str:
    rows = await opportunity_queries.fetch_candidate_key({"lead_id": lead_id})
    if not rows:
        raise HTTPException(status_code=404, detail="CRM lead not found")
    return str(rows[0]["unit_candidate_key"])


def _empty_dashboard(anchor: date, limit: int, offset: int, breakdown: str) -> CrmDashboardResponse:
    return CrmDashboardResponse(
        leads=[],
        total=0,
        limit=limit,
        offset=offset,
        summary=CrmSummary(
            total_leads=0,
            urgent=0,
            expired_unlet=0,
            expiring_30_days=0,
            expiring_31_to_60_days=0,
            expiring_61_to_90_days=0,
            sale_conversation_candidates=0,
            annual_rent_at_risk_aed=0,
        ),
        breakdown=[],
        breakdown_level=breakdown,
        filters=CrmFilters(),
        coverage=CrmCoverage(
            rental_source="Lease transaction registry",
            matching_method="Awaiting normalized rental events",
            caveat="Load the normalized rental snapshot before using lead inference.",
        ),
        as_of=anchor,
    )


async def get_saved_lead_snapshots(candidate_keys):
    live_rows = await opportunity_queries.fetch_saved_leads(
        {
            "as_of": date.today().isoformat(),
            "expired_days": 3650,
            "candidate_keys": candidate_keys,
        }
    )
    live_by_saved_key = {str(row["saved_candidate_key"]): _lead_from_row(row) for row in live_rows}
    return live_by_saved_key
