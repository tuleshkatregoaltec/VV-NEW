from datetime import date
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.market.reference import (
    SQM_TO_SQFT,
)
from app.supply import queries
from app.supply.mapping import (
    _developer_brand_name,
    _developer_identity,
    _developer_logo_match_key,
    _developer_rankable_brand,
    _developer_ranking_hidden,
    _float_or_none,
)
from app.supply.models import (
    DeveloperRankingResponse,
    DeveloperRankingsResponse,
    DeveloperRankingSummaryResponse,
)
from app.supply.queries import _latest_sales_date
from app.supply.scoring import (
    DEVELOPER_SCORE_WEIGHTS,
    _developer_criteria,
    _developer_rank_sort_value,
    _ratio_score,
    _risk_badge,
    _score_value,
)

DEVELOPER_RANKING_PERIODS: dict[str, tuple[str, int | None]] = {
    "ytd": ("Year to date", None),
    "1y": ("Last 12 months", 365),
    "3y": ("Last 3 years", 365 * 3),
    "5y": ("Last 5 years", 365 * 5),
}

DEVELOPER_RANKING_METRICS = {
    "proprietary_score",
    "units_sold",
    "sales_volume",
    "avg_price_sqft",
    "pipeline_units",
    "active_projects",
    "completed_projects",
    "avg_completion_pct",
}


async def _developer_logo_lookup(db: AsyncSession | None = None) -> dict[str, tuple[str, str]]:
    del db
    return {}


def _developer_period(period: str, end_date: date) -> tuple[str, date, date]:
    normalized = period.lower().strip()
    label, days = DEVELOPER_RANKING_PERIODS.get(normalized, DEVELOPER_RANKING_PERIODS["1y"])
    if normalized == "ytd":
        return label, date(end_date.year, 1, 1), end_date
    return label, date.fromordinal(end_date.toordinal() - int(days or 365)), end_date


async def get_developer_rankings(
    db: AsyncSession | None = None,
    period: str = "1y",
    metric: str = "proprietary_score",
    limit: int = 25,
) -> DeveloperRankingsResponse:
    ranking_metric = metric if metric in DEVELOPER_RANKING_METRICS else "proprietary_score"
    end_date = await _latest_sales_date() or date.today()
    period_label, start_date, end_date = _developer_period(period, end_date)
    logos = await _developer_logo_lookup(db)

    rows = await queries.fetch_developer_ranking_rows(
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "sqm_to_sqft": SQM_TO_SQFT,
        }
    )

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        brand_name = _developer_brand_name(str(row.get("developer_name") or ""))
        developer_name, logo_url = _developer_identity(brand_name, logos)
        if _developer_ranking_hidden(developer_name):
            continue
        if not _developer_rankable_brand(developer_name):
            continue
        group_key = _developer_logo_match_key(developer_name) or developer_name.lower()
        group = grouped.setdefault(
            group_key,
            {
                "developer_name": developer_name,
                "developer_id": row.get("developer_id"),
                "developer_logo_url": logo_url,
                "units_sold": 0,
                "sales_transaction_count": 0,
                "sales_volume_aed": 0.0,
                "avg_price_sqft_weighted": 0.0,
                "avg_price_sqft_weight": 0,
                "selling_projects": 0,
                "total_projects": 0,
                "active_projects": 0,
                "completed_projects": 0,
                "completed_units": 0,
                "pipeline_units": 0,
                "registered_units": 0,
                "avg_completion_weighted": 0.0,
                "avg_completion_weight": 0,
                "on_time_units": 0,
                "completed_units_with_schedule": 0,
                "initiated_projects_10y": 0,
                "completed_projects_10y": 0,
            },
        )
        group["units_sold"] += int(row.get("units_sold") or 0)
        group["sales_transaction_count"] += int(row.get("sales_transaction_count") or 0)
        group["sales_volume_aed"] += float(row.get("sales_volume_aed") or 0)
        group["selling_projects"] += int(row.get("selling_projects") or 0)
        group["total_projects"] += int(row.get("total_projects") or 0)
        group["active_projects"] += int(row.get("active_projects") or 0)
        group["completed_projects"] += int(row.get("completed_projects") or 0)
        group["completed_units"] += int(row.get("completed_units") or 0)
        group["pipeline_units"] += int(row.get("pipeline_units") or 0)
        group["registered_units"] += int(row.get("registered_units") or 0)
        group["on_time_units"] += int(row.get("on_time_units") or 0)
        group["completed_units_with_schedule"] += int(row.get("completed_units_with_schedule") or 0)
        group["initiated_projects_10y"] += int(row.get("initiated_projects_10y") or 0)
        group["completed_projects_10y"] += int(row.get("completed_projects_10y") or 0)

        sales_count = int(row.get("sales_transaction_count") or 0)
        avg_price_sqft = _float_or_none(row.get("avg_price_sqft_aed"))
        if avg_price_sqft is not None and sales_count:
            group["avg_price_sqft_weighted"] += avg_price_sqft * sales_count
            group["avg_price_sqft_weight"] += sales_count

        active_projects = int(row.get("active_projects") or 0)
        avg_completion = _float_or_none(row.get("avg_completion_pct"))
        if avg_completion is not None and active_projects:
            group["avg_completion_weighted"] += avg_completion * active_projects
            group["avg_completion_weight"] += active_projects

    rankings: list[DeveloperRankingResponse] = []
    for row in grouped.values():
        row["avg_price_sqft_aed"] = (
            row["avg_price_sqft_weighted"] / row["avg_price_sqft_weight"]
            if row["avg_price_sqft_weight"]
            else None
        )
        row["avg_completion_pct"] = (
            row["avg_completion_weighted"] / row["avg_completion_weight"]
            if row["avg_completion_weight"]
            else None
        )
        row["on_time_delivery_score"] = _ratio_score(
            row.get("on_time_units"), row.get("completed_units_with_schedule")
        )
        row["sales_completion_score"] = _ratio_score(
            row.get("units_sold"), row.get("registered_units")
        )
        row["historical_project_success_score"] = _ratio_score(
            row.get("completed_projects_10y"), row.get("initiated_projects_10y")
        )
        criteria, proprietary_score, coverage = _developer_criteria(row)
        ranking = DeveloperRankingResponse(
            rank=0,
            developer_name=str(row.get("developer_name") or "Unknown developer"),
            developer_id=int(row["developer_id"]) if row.get("developer_id") is not None else None,
            developer_logo_url=row.get("developer_logo_url"),
            proprietary_score=proprietary_score,
            score_coverage_pct=coverage,
            risk_badge=_risk_badge(proprietary_score, coverage),
            units_sold=int(row.get("units_sold") or 0),
            sales_transaction_count=int(row.get("sales_transaction_count") or 0),
            sales_volume_aed=float(row.get("sales_volume_aed") or 0),
            avg_price_sqft_aed=float(row["avg_price_sqft_aed"])
            if row.get("avg_price_sqft_aed") is not None
            else None,
            selling_projects=int(row.get("selling_projects") or 0),
            total_projects=int(row.get("total_projects") or 0),
            active_projects=int(row.get("active_projects") or 0),
            completed_projects=int(row.get("completed_projects") or 0),
            pipeline_units=int(row.get("pipeline_units") or 0),
            registered_units=int(row.get("registered_units") or 0),
            avg_completion_pct=float(row["avg_completion_pct"])
            if row.get("avg_completion_pct") is not None
            else None,
            on_time_delivery_score=_score_value(row.get("on_time_delivery_score")),
            sales_completion_score=_score_value(row.get("sales_completion_score")),
            historical_project_success_score=_score_value(
                row.get("historical_project_success_score")
            ),
            criteria=criteria,
        )
        rankings.append(ranking)

    if ranking_metric == "proprietary_score":
        rankings = [ranking for ranking in rankings if ranking.proprietary_score is not None]
    else:
        rankings = [
            ranking
            for ranking in rankings
            if _developer_rank_sort_value(ranking, ranking_metric) > 0
        ]

    rankings.sort(
        key=lambda item: (
            -_developer_rank_sort_value(item, ranking_metric),
            -item.sales_volume_aed,
            item.developer_name.lower(),
        )
    )
    rankings = rankings[:limit]
    for index, ranking in enumerate(rankings, start=1):
        ranking.rank = index

    return DeveloperRankingsResponse(
        rankings=rankings,
        summary=DeveloperRankingSummaryResponse(
            total_developers=len(grouped),
            period_label=period_label,
            start_date=start_date,
            end_date=end_date,
            ranking_metric=ranking_metric,
            available_score_weight_pct=sum(DEVELOPER_SCORE_WEIGHTS.values()),
            unavailable_criteria=[],
        ),
    )
