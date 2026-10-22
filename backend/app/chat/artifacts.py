import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.analytics import charts
from app.analytics.charts import (
    clamp_int,
    clean_text,
    dimension_label,
    metric_label,
    positive_int,
)
from app.listings.models import ListingMode
from app.listings.service import list_listings_from_clickhouse
from app.listings.service import resolve_listing_area as _resolve_listing_area

logger = logging.getLogger(__name__)
SALE_LISTING_INTENT_RE = re.compile(
    r"\b(buy|buying|purchase|sales?|for[-\s]?sale|off[-\s]?plan|resale)\b",
    re.IGNORECASE,
)


def _format_history_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _artifact_point_summary(point: dict[str, Any], metric_label: str | None) -> str:
    label = str(point.get("label") or "Unknown")
    value = _format_history_value(point.get("value"))
    record_count = point.get("record_count")
    records = f", {_format_history_value(record_count)} records" if record_count is not None else ""
    metric = metric_label or "value"
    return f"{label}: {metric} {value}{records}"


def _artifact_history_context(meta_data: dict[str, Any] | None) -> str | None:
    artifacts = (meta_data or {}).get("artifacts")
    if not isinstance(artifacts, list):
        return None

    lines: list[str] = []
    for artifact in artifacts[-3:]:
        if not isinstance(artifact, dict) or artifact.get("status") != "ready":
            continue

        artifact_type = artifact.get("type")
        query = artifact.get("query") if isinstance(artifact.get("query"), dict) else {}
        data = artifact.get("data") if isinstance(artifact.get("data"), dict) else {}

        if artifact_type == "custom_chart":
            title = str(artifact.get("title") or data.get("metric_label") or "Chart")
            subtitle = str(artifact.get("subtitle") or "")
            metric_label = str(data.get("metric_label") or "value")
            sort_direction = str(
                data.get("sort_direction") or query.get("sort_direction") or "desc"
            )
            points = data.get("points") if isinstance(data.get("points"), list) else []
            series = data.get("series") if isinstance(data.get("series"), list) else []

            if points:
                row_summaries = [
                    _artifact_point_summary(point, metric_label)
                    for point in points[:5]
                    if isinstance(point, dict)
                ]
            else:
                row_summaries = []
                for item in series[:5]:
                    if not isinstance(item, dict):
                        continue
                    series_points = (
                        item.get("points") if isinstance(item.get("points"), list) else []
                    )
                    latest_point = series_points[-1] if series_points else {}
                    if isinstance(latest_point, dict):
                        row_summaries.append(
                            _artifact_point_summary(
                                {**latest_point, "label": item.get("label")},
                                metric_label,
                            )
                        )

            if row_summaries:
                scope = f" ({subtitle})" if subtitle else ""
                lines.append(
                    f"Rendered chart{scope}: {title}; sort {sort_direction}; "
                    f"displayed rows in order: {'; '.join(row_summaries)}."
                )

        elif artifact_type == "listing_results":
            title = str(artifact.get("title") or "Listings")
            mode = str(query.get("mode") or "")
            area = str(query.get("area") or "Dubai")
            total = data.get("total")
            lines.append(
                f"Rendered listings card: {title}; mode {mode or 'unknown'}; "
                f"area {area}; exact matches {_format_history_value(total)}."
            )

        elif artifact_type == "market_trend":
            title = str(artifact.get("title") or "Market trend")
            area = str(query.get("area") or "Dubai")
            total = data.get("total_transactions")
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            period_start = summary.get("period_start")
            period_end = summary.get("period_end")
            period = (
                f"; period {period_start} to {period_end}" if period_start and period_end else ""
            )
            lines.append(
                f"Rendered market trend card: {title}; area {area}; "
                f"transactions {_format_history_value(total)}{period}."
            )

    if not lines:
        return None
    return "\n".join(lines)


def _listing_mode(value: Any) -> ListingMode:
    return "sale" if str(value).lower() == "sale" else "rent"


def _message_requests_sale_inventory(message: str | None) -> bool:
    if not message:
        return False
    return bool(SALE_LISTING_INTENT_RE.search(message))


def _bedroom_values(args: dict[str, Any]) -> list[str] | None:
    exact = clean_text(args.get("bedrooms"))
    if exact:
        return [exact.lower() if exact.lower() == "studio" else exact]

    min_bedrooms = positive_int(args.get("bedrooms_min"))
    max_bedrooms = positive_int(args.get("bedrooms_max"))
    if min_bedrooms is None and max_bedrooms is None:
        return None

    start = min_bedrooms if min_bedrooms is not None else 0
    end = max_bedrooms if max_bedrooms is not None else start
    if end < start:
        start, end = end, start
    return [str(value) for value in range(start, min(end, 10) + 1)]


def _artifact_shell(tool_name: str, tool_call_id: str, args: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    if tool_name == "show_listings":
        mode = _listing_mode(args.get("mode"))
        area = clean_text(args.get("area"))
        title = "Rental listings" if mode == "rent" else "Sales listings"
        subtitle = area or "Dubai"
        return {
            "id": f"{tool_name}:{tool_call_id}",
            "type": "listing_results",
            "status": "loading",
            "title": title,
            "subtitle": subtitle,
            "created_at": now,
            "query": args,
        }
    if tool_name == "show_market_trend":
        area = clean_text(args.get("area"))
        return {
            "id": f"{tool_name}:{tool_call_id}",
            "type": "market_trend",
            "status": "loading",
            "title": "Market trend",
            "subtitle": area or "Dubai market",
            "created_at": now,
            "query": args,
        }
    if tool_name == "show_chart":
        dataset = clean_text(args.get("dataset")) or "sales"
        group_by = clean_text(args.get("group_by")) or "area"
        metric = clean_text(args.get("metric")) or "transaction_count"
        return {
            "id": f"{tool_name}:{tool_call_id}",
            "type": "custom_chart",
            "status": "loading",
            "title": metric_label(metric),
            "subtitle": f"{dataset.title()} · {dimension_label(group_by)}",
            "created_at": now,
            "query": args,
        }
    return {
        "id": f"{tool_name}:{tool_call_id}",
        "type": "unknown",
        "status": "loading",
        "title": tool_name.replace("_", " ").title(),
        "created_at": now,
        "query": args,
    }


def _listing_subtitle(args: dict[str, Any], total: int) -> str:
    parts: list[str] = []
    area = clean_text(args.get("area"))
    if area:
        parts.append(area)
    bedrooms = _bedroom_values(args)
    if bedrooms:
        parts.append(f"{'-'.join(bedrooms)} bed" if len(bedrooms) > 1 else f"{bedrooms[0]} bed")
    price_min = positive_int(args.get("price_min"))
    price_max = positive_int(args.get("price_max"))
    if price_min is not None or price_max is not None:
        low = f"AED {price_min:,}" if price_min is not None else "Any"
        high = f"AED {price_max:,}" if price_max is not None else "Any"
        suffix = "/mo" if args.get("price_basis") == "monthly" else ""
        parts.append(f"{low}-{high}{suffix}")
    parts.append(f"{total:,} matches")
    return " · ".join(parts)


async def _fetch_listing_artifact(
    db: AsyncSession,
    artifact: dict[str, Any],
    args: dict[str, Any],
    user_message: str | None = None,
) -> dict[str, Any]:
    mode = _listing_mode(args.get("mode"))
    requested_mode = mode
    if mode == "sale" and not _message_requests_sale_inventory(user_message):
        mode = "rent"
    limit = clamp_int(args.get("limit"), default=12, min_value=1, max_value=24)
    price_basis = "monthly" if str(args.get("price_basis")).lower() == "monthly" else "raw"
    price_period = clean_text(args.get("price_period"))
    raw_area = clean_text(args.get("area"))
    property_type = clean_text(args.get("property_type"))
    bedrooms = _bedroom_values(args)
    price_min = positive_int(args.get("price_min"))
    price_max = positive_int(args.get("price_max"))

    async def load_for_mode(next_mode: ListingMode):
        resolved_area = await _resolve_listing_area(db, next_mode, raw_area)
        result = await list_listings_from_clickhouse(
            mode=next_mode,
            limit=limit,
            offset=0,
            area=resolved_area,
            property_type=property_type,
            bedrooms_in=bedrooms,
            price_min=price_min,
            price_max=price_max,
            price_period=price_period,
            price_basis=price_basis,
        )
        return resolved_area, result

    area, listings = await load_for_mode(mode)
    fallback_from_mode: ListingMode | None = None
    if requested_mode != mode:
        fallback_from_mode = requested_mode

    payload = listings.model_dump(mode="json")
    subtitle_args = {**args, "area": area, "mode": mode}
    title = "Rental listings" if mode == "rent" else "Sales listings"
    return {
        **artifact,
        "status": "ready",
        "title": title,
        "subtitle": _listing_subtitle(subtitle_args, listings.total),
        "query": {
            "mode": mode,
            "area": area,
            "property_type": property_type,
            "bedrooms": bedrooms,
            "price_min": price_min,
            "price_max": price_max,
            "price_basis": price_basis,
            "limit": limit,
            "fallback_from_mode": fallback_from_mode,
        },
        "data": payload,
    }


async def _fetch_artifact(
    db: AsyncSession,
    tool_name: str,
    tool_call_id: str,
    args: dict[str, Any],
    user_message: str | None = None,
) -> dict[str, Any] | None:
    if tool_name not in {"show_listings", "show_market_trend", "show_chart"}:
        return None

    artifact = _artifact_shell(tool_name, tool_call_id, args)
    try:
        if tool_name == "show_listings":
            return await _fetch_listing_artifact(db, artifact, args, user_message)
        if tool_name == "show_market_trend":
            return await _fetch_market_trend_artifact(artifact, args)
        if tool_name == "show_chart":
            return await _fetch_custom_chart_artifact(artifact, args)
    except Exception as exc:
        logger.exception("Failed to build chat artifact for %s", tool_name)
        return {**artifact, "status": "error", "error": str(exc)}
    return None


async def _fetch_market_trend_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.market_trend(args)}


async def _fetch_sales_chart_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.sales_chart(args)}


async def _fetch_rental_chart_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.rental_chart(args)}


async def _fetch_project_chart_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.project_chart(args)}


async def _fetch_area_yield_chart_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.area_yield_chart(args)}


async def _fetch_custom_chart_artifact(
    artifact: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    return {**artifact, "status": "ready", **await charts.custom_chart(args)}
