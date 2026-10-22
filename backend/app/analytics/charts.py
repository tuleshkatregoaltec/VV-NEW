"""Reusable market chart results, independent of chat envelopes and persistence."""

from typing import Any

from app.analytics import chart_queries
from app.analytics.metrics import (
    _chart_points,
    _chart_series,
    _chart_series_summary,
    _chart_summary,
    _chart_type,
    _flatten_chart_series,
    dimension_label,
    metric_label,
)
from app.analytics.metrics import clamp_int as clamp_int
from app.analytics.metrics import clean_text as clean_text
from app.analytics.metrics import positive_int as positive_int


async def market_trend(args: dict[str, Any]) -> dict[str, Any]:
    (
        rows,
        area,
        days,
        interval,
        property_usage,
        property_type,
    ) = await chart_queries.fetch_market_trend_rows(args)
    points = [
        {
            "date": str(row["period"]),
            "transaction_count": int(row["transaction_count"]),
            "avg_price": float(row["avg_price"] or 0),
            "avg_price_sqm": float(row["avg_price_sqm"] or 0),
        }
        for row in rows
    ]
    first = next((point for point in points if point["avg_price_sqm"] > 0), None)
    last = next((point for point in reversed(points) if point["avg_price_sqm"] > 0), None)
    change_pct = (
        round((last["avg_price_sqm"] - first["avg_price_sqm"]) / first["avg_price_sqm"] * 100, 1)
        if first and last and first["avg_price_sqm"]
        else None
    )
    total_transactions = sum(point["transaction_count"] for point in points)
    return {
        "subtitle": f"{area or 'Dubai market'} · {days} days · {total_transactions:,} transactions",
        "query": {
            "area": area,
            "property_usage": property_usage,
            "property_type": property_type,
            "days": days,
            "interval": interval,
        },
        "data": {
            "data": points,
            "total_transactions": total_transactions,
            "avg_price_sqm_change_pct": change_pct,
        },
    }


async def sales_chart(args: dict[str, Any]) -> dict[str, Any]:
    (
        rows,
        has_series,
        group_by,
        metric,
        sort_direction,
        scopes,
        is_multi_area,
        scope_label,
        area,
        master_project,
        property_usage,
        property_type,
        days,
        interval,
        limit,
        min_records,
        start_date,
        end_date,
    ) = await chart_queries.fetch_sales_chart_rows(args)
    series = (
        _chart_series(
            rows,
            time_series=group_by == "time",
            series_order=[str(scope["label"]) for scope in scopes if scope.get("label")],
        )
        if has_series
        else []
    )
    points = (
        _flatten_chart_series(series)
        if has_series
        else _chart_points(rows, time_series=group_by == "time")
    )
    chart_type = _chart_type(args.get("chart_type"), group_by)
    if series and chart_type == "doughnut":
        chart_type = "line" if group_by == "time" else "bar"
    scope_title = (
        ", ".join(str(scope["label"]) for scope in scopes) if is_multi_area else scope_label
    )
    return {
        "title": metric_label(metric),
        "subtitle": f"Sales · {dimension_label(group_by)} · {scope_title or 'Dubai'}",
        "query": {
            "dataset": "sales",
            "chart_type": chart_type,
            "group_by": group_by,
            "metric": metric,
            "area": scope_label,
            "areas": [scope["label"] for scope in scopes] if is_multi_area else [],
            "sort_direction": sort_direction,
            "min_records": min_records,
            "area_name": area,
            "master_project": master_project,
            "property_usage": property_usage,
            "property_type": property_type,
            "days": days,
            "interval": interval,
            "limit": limit,
        },
        "data": {
            "chart_type": chart_type,
            "metric": metric,
            "metric_label": metric_label(metric),
            "dimension": group_by,
            "dimension_label": dimension_label(group_by),
            "data_source": "DLD sales transactions",
            "sort_direction": sort_direction,
            "points": points,
            "series": series,
            "summary": (
                _chart_series_summary(series, period_start=start_date, period_end=end_date)
                if series
                else _chart_summary(points, period_start=start_date, period_end=end_date)
            ),
        },
    }


async def rental_chart(args: dict[str, Any]) -> dict[str, Any]:
    (
        rows,
        has_series,
        group_by,
        metric,
        sort_direction,
        scopes,
        is_multi_area,
        scope_label,
        area,
        master_project,
        property_usage,
        property_type,
        days,
        interval,
        limit,
        min_records,
        start_date,
        end_date,
    ) = await chart_queries.fetch_rental_chart_rows(args)
    series = (
        _chart_series(
            rows,
            time_series=group_by == "time",
            series_order=[str(scope["label"]) for scope in scopes if scope.get("label")],
        )
        if has_series
        else []
    )
    points = (
        _flatten_chart_series(series)
        if has_series
        else _chart_points(rows, time_series=group_by == "time")
    )
    chart_type = _chart_type(args.get("chart_type"), group_by)
    if series and chart_type == "doughnut":
        chart_type = "line" if group_by == "time" else "bar"
    scope_title = (
        ", ".join(str(scope["label"]) for scope in scopes) if is_multi_area else scope_label
    )
    return {
        "title": metric_label(metric),
        "subtitle": f"Rentals · {dimension_label(group_by)} · {scope_title or 'Dubai'}",
        "query": {
            "dataset": "rentals",
            "chart_type": chart_type,
            "group_by": group_by,
            "metric": metric,
            "area": scope_label,
            "areas": [scope["label"] for scope in scopes] if is_multi_area else [],
            "sort_direction": sort_direction,
            "min_records": min_records,
            "area_name": area,
            "master_project": master_project,
            "property_usage": property_usage,
            "property_type": property_type,
            "days": days,
            "interval": interval,
            "limit": limit,
        },
        "data": {
            "chart_type": chart_type,
            "metric": metric,
            "metric_label": metric_label(metric),
            "dimension": group_by,
            "dimension_label": dimension_label(group_by),
            "data_source": "DLD Ejari rental contracts",
            "sort_direction": sort_direction,
            "points": points,
            "series": series,
            "summary": (
                _chart_series_summary(series, period_start=start_date, period_end=end_date)
                if series
                else _chart_summary(points, period_start=start_date, period_end=end_date)
            ),
        },
    }


async def project_chart(args: dict[str, Any]) -> dict[str, Any]:
    (
        rows,
        has_series,
        group_by,
        metric,
        sort_direction,
        areas,
        is_multi_area,
        area,
        limit,
        min_records,
    ) = await chart_queries.fetch_project_chart_rows(args)
    series = (
        _chart_series(rows, time_series=group_by == "time", series_order=areas)
        if has_series
        else []
    )
    points = (
        _flatten_chart_series(series)
        if has_series
        else _chart_points(rows, time_series=group_by == "time")
    )
    chart_type = _chart_type(args.get("chart_type"), group_by)
    if series and chart_type == "doughnut":
        chart_type = "bar"
    scope_title = ", ".join(areas) if is_multi_area else area
    return {
        "title": metric_label(metric),
        "subtitle": f"Projects · {dimension_label(group_by)} · {scope_title or 'Dubai'}",
        "query": {
            "dataset": "projects",
            "chart_type": chart_type,
            "group_by": group_by,
            "metric": metric,
            "area": area,
            "areas": areas if is_multi_area else [],
            "sort_direction": sort_direction,
            "min_records": min_records,
            "limit": limit,
        },
        "data": {
            "chart_type": chart_type,
            "metric": metric,
            "metric_label": metric_label(metric),
            "dimension": group_by,
            "dimension_label": dimension_label(group_by),
            "data_source": "DLD project registry and 12-month market facts",
            "sort_direction": sort_direction,
            "points": points,
            "series": series,
            "summary": (
                _chart_series_summary(series, period_start=None, period_end=None)
                if series
                else _chart_summary(points, period_start=None, period_end=None)
            ),
        },
    }


async def area_yield_chart(args: dict[str, Any]) -> dict[str, Any]:
    (
        rows,
        days,
        limit,
        sort_direction,
        min_records,
        property_usage,
        yield_property_type,
        start_date,
        end_date,
    ) = await chart_queries.fetch_area_yield_chart_rows(args)
    points = _chart_points(rows, time_series=False)
    chart_type = _chart_type(args.get("chart_type"), "area")
    return {
        "title": metric_label("gross_yield_pct"),
        "subtitle": "Areas · Gross yield · Dubai",
        "query": {
            "dataset": "areas",
            "chart_type": chart_type,
            "group_by": "area",
            "metric": "gross_yield_pct",
            "sort_direction": sort_direction,
            "min_records": min_records,
            "property_usage": property_usage,
            "property_type": yield_property_type,
            "days": days,
            "limit": limit,
        },
        "data": {
            "chart_type": chart_type,
            "metric": "gross_yield_pct",
            "metric_label": metric_label("gross_yield_pct"),
            "dimension": "area",
            "dimension_label": dimension_label("area"),
            "data_source": "DLD sales transactions and Ejari rental contracts",
            "sort_direction": sort_direction,
            "points": points,
            "series": [],
            "summary": _chart_summary(points, period_start=start_date, period_end=end_date),
        },
    }


async def custom_chart(args: dict[str, Any]) -> dict[str, Any]:
    dataset = str(args.get("dataset") or "sales").lower()
    metric = str(args.get("metric") or "").lower()
    group_by = str(args.get("group_by") or "area").lower()
    if dataset in {"rent", "rental"}:
        dataset = "rentals"
    if dataset in {"area", "areas", "community", "communities", "yield"}:
        dataset = "areas"
    if dataset in {"project", "pipeline", "development", "developments"}:
        dataset = "projects"

    if metric in {"yield", "rental_yield", "gross_yield", "gross_yield_pct"} and group_by == "area":
        return await area_yield_chart(args)
    if dataset == "areas":
        return await area_yield_chart(args)
    if dataset == "rentals":
        return await rental_chart(args)
    if dataset == "projects":
        return await project_chart(args)
    return await sales_chart(args)
