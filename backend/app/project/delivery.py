"""Scheduled delivery of DLD-registered projects: what is due, overdue or undated.

Source: the DLD project register (ch_projects, one row per project number). Units are DLD
registered unit counts; a project with no registered units is counted, not estimated.
Reelly catalogue fields (payment plans, unit prices) are a separate source and not used here.
"""

from __future__ import annotations

from datetime import date

from app.core.observability import operation
from app.market.periods import BUSINESS_TIMEZONE, business_today
from app.market.reference import community_location
from app.market.scope import location_parameters
from app.project import queries
from app.project.models import DeliveryGroupRow, DeliveryStatus, DeliverySummaryResponse

ALL_DELIVERY_STATUSES: tuple[DeliveryStatus, ...] = (
    "missing_date",
    "overdue",
    "scheduled_in_horizon",
    "scheduled_later",
    "completed",
    "cancelled",
)

DEFINITIONS = {
    "missing_date": "No planned end date registered and not completed or cancelled.",
    "overdue": "Planned end date before the reference date, not completed or cancelled.",
    "scheduled_in_horizon": "Planned end date from the reference date to the horizon end.",
    "scheduled_later": "Planned end date after the horizon end.",
    "completed": "DLD completion date recorded.",
    "cancelled": "DLD cancellation date recorded.",
    "units": "DLD registered units (cnt_unit); projects without registered units count as 0.",
    "project_status": "DLD register status, e.g. ACTIVE, NOT_STARTED, PENDING, FINISHED.",
}


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month = index // 12, index % 12 + 1
    for day in (value.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(value)


def _rows(raw: list[dict], total_units: int) -> list[DeliveryGroupRow]:
    return [
        DeliveryGroupRow(
            group=str(item["group_key"]),
            project_count=int(item["project_count"]),
            units=int(item["units"]),
            projects_without_units=int(item["projects_without_units"]),
            share_of_units=round(int(item["units"]) / total_units, 4) if total_units else None,
        )
        for item in raw
    ]


@operation
async def get_delivery_summary(
    *,
    location: str | None = None,
    project_statuses: tuple[str, ...] = ("ACTIVE",),
    delivery_statuses: tuple[DeliveryStatus, ...] = ALL_DELIVERY_STATUSES,
    group_by: str = "developer",
    horizon_months: int = 24,
    reference_date: date | None = None,
    limit: int = 20,
) -> DeliverySummaryResponse:
    if group_by not in queries.DELIVERY_GROUPS:
        raise ValueError(f"group_by must be one of {sorted(queries.DELIVERY_GROUPS)}")
    if not 1 <= horizon_months <= 240:
        raise ValueError("horizon_months must be between 1 and 240")
    reference = reference_date or business_today()
    horizon_end = _add_months(reference, horizon_months)
    resolved = community_location(location) if location else None
    parameters = {
        "statuses": [status.upper() for status in project_statuses],
        "reference_date": reference,
        "horizon_end": horizon_end,
        "limit": max(1, min(limit, 200)),
        **(location_parameters(resolved) if resolved else {}),
    }
    by_status_raw = await queries.fetch_delivery_summary(
        parameters | {"delivery_statuses": list(ALL_DELIVERY_STATUSES)},
        group_by="delivery_status",
        located=resolved is not None,
    )
    raw = await queries.fetch_delivery_summary(
        parameters | {"delivery_statuses": list(delivery_statuses)},
        group_by=group_by,
        located=resolved is not None,
    )
    selected_units = sum(
        int(item["units"]) for item in by_status_raw if item["group_key"] in delivery_statuses
    )
    snapshot = await queries.fetch_project_register_snapshot()
    limitations = []
    if resolved and resolved.communities and not resolved.districts:
        for item in await queries.fetch_district_only_projects(parameters):
            limitations.append(
                f"{int(item['projects'])} projects in {item['district']} have no master project "
                f"and are not counted: DLD does not place them in {resolved.name}."
            )
    if any(int(item["projects_without_units"]) for item in raw):
        limitations.append("Some projects have no registered unit count and add 0 units.")
    if not by_status_raw:
        limitations.append("No DLD-registered projects match these filters.")
    return DeliverySummaryResponse(
        location=resolved.name if resolved else None,
        project_statuses=parameters["statuses"],
        delivery_statuses=list(delivery_statuses),
        group_by=group_by,
        reference_date=reference,
        horizon_end=horizon_end,
        definitions=DEFINITIONS | {"timezone": str(BUSINESS_TIMEZONE)},
        by_delivery_status=_rows(by_status_raw, sum(int(i["units"]) for i in by_status_raw)),
        rows=_rows(raw, selected_units),
        matched_terms=location_parameters(resolved) if resolved else {},
        source="DLD project register (ch_projects, latest snapshot per project number)",
        source_snapshot_at=str(snapshot[0]["snapshot_at"]) if snapshot else None,
        limitations=limitations,
    )
