"""Read-only live data census for the Vitevue data supply chain.

Traces every production-facing dataset through
SOURCE -> EXTRACTION -> RAW -> BRONZE -> PUBLICATION -> SERVING -> BACKEND -> API -> FRONTEND
and writes docs/data-pipelines/DATA_SOURCE_HEALTH.{json,md}.

Every live probe is read-only: ClickHouse system tables and SELECTs, S3 list/get on
manifests, SELECTs on Dagster and app Postgres, HTTP GETs. Probes whose credentials
are absent are recorded as UNKNOWN instead of guessed. Static facts (what the code
alone proves) are kept separate from live observations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from holocron.platform.doctor import _read_env_file, _truthy

CENSUS_VERSION = 1
UNKNOWN_ENV = "UNKNOWN — REQUIRES CREDENTIAL / ENVIRONMENT"
STAGES = (
    "source",
    "extraction",
    "raw",
    "bronze",
    "publication",
    "serving",
    "backend",
    "api",
    "frontend",
)
CLASSES = (
    "AVAILABLE",
    "PARTIAL",
    "STALE",
    "MISSING",
    "PIPELINE_FAILED",
    "SCHEMA_DRIFT",
    "DOWNSTREAM_BROKEN",
    "UNKNOWN",
)
_HOLOCRON_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _HOLOCRON_ROOT.parent
SCHEMA_SQL = _HOLOCRON_ROOT / "holocron" / "lib" / "sql" / "clickhouse_schema.sql"
FRESHNESS_COLUMNS = ("loaded_at", "materialized_at", "scraped_at", "last_scraped_at")
# Columns that describe a load, not the record; excluded when testing for exact duplicates.
LOAD_METADATA_COLUMNS = frozenset(
    {
        "row_key",
        "run_id",
        "scraped_at",
        "loaded_at",
        "materialized_at",
        "last_scraped_at",
        "mode",
        "category",
        "endpoint",
        "date_window_start",
        "date_window_end",
        "page_index",
        "row_index",
        "source_system",
        "source_file",
        "source_row_index",
        "raw_json",
        "raw_manifest_s3_key",
        "source_account",
        "source_shard_date",
        "source_run_id",
    }
)
_SECRET_KEY = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|URL|HEADER)", re.I)


@dataclass(frozen=True, slots=True)
class Dataset:
    """One production-facing dataset and the static facts of its path.

    `static_breaks` holds only breaks the code proves regardless of live state:
    (stage, classification, evidence).
    """

    id: str
    pipelines: tuple[str, ...]
    name: str
    provider: str
    producer: str
    cadence: str
    sources: tuple[str, ...] = ()
    extra_jobs: tuple[str, ...] = ()
    raw_location: str = ""
    tables: tuple[str, ...] = ()
    key: tuple[str, ...] = ()
    event_column: str | None = None
    split_column: str | None = None
    quality_columns: tuple[str, ...] = ()
    serving: tuple[str, ...] = ()
    required_columns: tuple[tuple[str, tuple[str, ...]], ...] = ()
    postgres: tuple[tuple[str, str | None], ...] = ()
    api: tuple[str, ...] = ()
    frontend: tuple[str, ...] = ()
    sla_hours: float | None = None
    product: bool = True
    static_breaks: tuple[tuple[str, str, str], ...] = ()
    notes: tuple[str, ...] = ()


_DLD_UNUSED = "no ch_* view or backend query reads this table"
_ANALYTICS_TODO = (
    "api",
    "PARTIAL",
    "GET /api/v1/analytics/sales and /rentals always return trends=[], by_property_type=[], "
    "by_rooms=[], by_reg_type=[], by_master_project=[] (TODO in backend/app/analytics/service.py "
    "around lines 840 and 943); TransactionVolumeModal renders by_property_type only when non-empty, "
    "so that breakdown never appears although the rows exist in ch_transactions/ch_rent_contracts",
)

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="dld_transactions",
        pipelines=("P01", "P02"),
        name="DLD sales transactions",
        provider="Dubai Land Department (Open Data API + Dubai Pulse historic CSV)",
        producer="holocron:dld_od_transactions (daily) + holocron:dld_pulse_historic (manual)",
        cadence="daily 04:00 Asia/Dubai",
        sources=("dld_od_transactions", "dld_pulse_historic"),
        tables=("dld_od_transactions_bronze",),
        key=("transaction_number",),
        event_column="instance_date",
        split_column="source_system",
        quality_columns=(
            "trans_value",
            "procedure_area",
            "area_en",
            "building_name_en",
            "meter_sale_price",
        ),
        serving=("ch_transactions",),
        api=(
            "/api/v1/transactions/sales",
            "/api/v1/analytics/sales",
            "/api/v1/analytics/market-trend",
        ),
        frontend=("/transactions/sales", "/analytics", "/dashboard"),
        sla_hours=48,
        static_breaks=(
            (
                "api",
                "PARTIAL",
                "TransactionDetailsModal (mounted on /transactions/sales) calls "
                "GET /api/v1/transactions/{id}/match-units and "
                "/api/v1/transactions/units/{id}/comparable (frontend/src/lib/api/"
                "transaction-matching.ts); neither route exists in backend app.routes",
            ),
            _ANALYTICS_TODO,
        ),
        notes=(
            "Pulse-only columns (building_name_en, meter_sale_price, reg_type_*, "
            "no_of_parties_role_*) are added by ALTER TABLE and default to ''/0 for "
            "open-data rows; empty % is split by source_system below.",
            "F24: daily job re-fetches a 3-day lookback with no checkpoint.",
        ),
    ),
    Dataset(
        id="dld_rents",
        pipelines=("P01", "P02"),
        name="DLD rent contracts (Ejari)",
        provider="Dubai Land Department",
        producer="holocron:dld_od_rents (daily) + holocron:dld_pulse_historic (manual)",
        cadence="daily 04:10 Asia/Dubai",
        sources=("dld_od_rents", "dld_pulse_historic"),
        tables=("dld_od_rents_bronze",),
        key=("contract_number",),
        event_column="registration_date",
        split_column="source_system",
        quality_columns=("annual_amount", "actual_area", "area_en", "tenant_type_en"),
        serving=("ch_rent_contracts",),
        api=("/api/v1/transactions/rentals", "/api/v1/analytics/rentals"),
        frontend=("/transactions/rentals", "/analytics"),
        sla_hours=48,
        static_breaks=(_ANALYTICS_TODO,),
    ),
    Dataset(
        id="dld_projects",
        pipelines=("P01", "P02", "P20"),
        name="DLD projects (+ project/area fact serving)",
        provider="Dubai Land Department",
        producer="holocron:dld_od_projects + dld_pulse_historic; facts via backend/scripts/clickhouse",
        cadence="daily 04:20 Asia/Dubai; facts manual",
        sources=("dld_od_projects", "dld_pulse_historic"),
        tables=("dld_od_projects_bronze",),
        key=("project_number",),
        event_column="adoption_date",
        split_column="source_system",
        quality_columns=("project_en", "developer_en", "area_en", "project_status"),
        serving=("ch_projects", "ch_geo_projects", "ch_project_fact", "ch_area_fact"),
        api=(
            "/api/v1/projects/list",
            "/api/v1/analytics/projects",
            "/api/v1/areas",
            "/api/v1/supply/developer-rankings",
        ),
        frontend=("/projects", "/analytics", "/radar", "/developers"),
        sla_hours=48,
        notes=(
            "F03: window-equality delete lets daily sliding windows accumulate duplicates; "
            "the duplicate-key measurement below is the live check.",
            "F13: build_fact_tables.py renames the ch_project_fact/ch_area_fact views away; "
            "the census records the live object type of both.",
        ),
    ),
    Dataset(
        id="dld_developers",
        pipelines=("P01", "P02"),
        name="DLD developers",
        provider="Dubai Land Department",
        producer="holocron:dld_od_developers + dld_pulse_historic",
        cadence="daily 04:50 Asia/Dubai",
        sources=("dld_od_developers", "dld_pulse_historic"),
        tables=("dld_od_developers_bronze",),
        key=("developer_number",),
        split_column="source_system",
        quality_columns=("developer_en", "license_number"),
        serving=("ch_developers",),
        api=("/api/v1/supply/developer-rankings",),
        frontend=("/developers",),
        sla_hours=48,
        notes=("F03 applies (window-equality delete).",),
    ),
    Dataset(
        id="dld_buildings",
        pipelines=("P01", "P02"),
        name="DLD buildings",
        provider="Dubai Land Department",
        producer="holocron:dld_od_buildings + dld_pulse_historic",
        cadence="daily 04:40 Asia/Dubai",
        sources=("dld_od_buildings", "dld_pulse_historic"),
        tables=("dld_od_buildings_bronze",),
        key=("building_number",),
        split_column="source_system",
        quality_columns=("project_en", "area_en", "floors"),
        serving=("ch_buildings",),
        api=("/api/v1/feasibility/plots",),
        frontend=("/projects", "/feasibility"),
        sla_hours=48,
        notes=("F03 applies (window-equality delete).",),
    ),
    Dataset(
        id="dld_units",
        pipelines=("P01", "P02"),
        name="DLD units",
        provider="Dubai Land Department",
        producer="holocron:dld_od_units + dld_pulse_historic",
        cadence="daily 05:20 Asia/Dubai",
        sources=("dld_od_units", "dld_pulse_historic"),
        tables=("dld_od_units_bronze",),
        key=("property_id",),
        split_column="source_system",
        quality_columns=("unit_number", "building_number", "actual_area", "project_en"),
        serving=("ch_units",),
        api=("/api/v1/analytics/project/summary",),
        frontend=("/analytics", "/projects"),
        sla_hours=48,
        notes=("F03 applies (window-equality delete).",),
    ),
    Dataset(
        id="dld_valuations",
        pipelines=("P01",),
        name="DLD valuations",
        provider="Dubai Land Department",
        producer="holocron:dld_od_valuations",
        cadence="daily 04:30 Asia/Dubai",
        sources=("dld_od_valuations",),
        tables=("dld_od_valuations_bronze",),
        key=("procedure_year", "procedure_number"),
        event_column="instance_date",
        product=False,
        sla_hours=48,
        notes=(_DLD_UNUSED,),
    ),
    Dataset(
        id="dld_lands",
        pipelines=("P01", "P02"),
        name="DLD lands",
        provider="Dubai Land Department",
        producer="holocron:dld_od_lands + dld_pulse_historic",
        cadence="daily 05:10 Asia/Dubai",
        sources=("dld_od_lands", "dld_pulse_historic"),
        tables=("dld_od_lands_bronze",),
        key=("land_number",),
        split_column="source_system",
        product=False,
        sla_hours=48,
        notes=(_DLD_UNUSED,),
    ),
    Dataset(
        id="dld_brokers",
        pipelines=("P01",),
        name="DLD brokers",
        provider="Dubai Land Department",
        producer="holocron:dld_od_brokers",
        cadence="daily 05:30 Asia/Dubai",
        sources=("dld_od_brokers",),
        tables=("dld_od_brokers_bronze",),
        key=("broker_number",),
        product=False,
        sla_hours=48,
        notes=(_DLD_UNUSED,),
    ),
    Dataset(
        id="dda_land_plots",
        pipelines=("P13",),
        name="DDA planning layers (land plots)",
        provider="Dubai Development Authority GIS",
        producer="holocron:dda_planning_layers",
        cadence="weekly Sun 06:00 Asia/Dubai (spec metadata says Mon 07:00)",
        sources=("dda_planning_layers",),
        tables=("dda_land_plots",),
        key=("plot_number",),
        quality_columns=("project_name", "plot_area_sqm", "max_gfa_sqm", "land_use", "coordinates"),
        serving=("ch_gis_land_plots",),
        api=("/api/v1/feasibility/plots", "/api/v1/projects/dda-plots"),
        frontend=("/feasibility", "/radar"),
        sla_hours=24 * 8,
        notes=(
            "F08: DDA loader can emit stub rows; empty % of plot attributes is the live check.",
            "Other 10 DDA layers are loaded but unconsumed (see dda_secondary_layers).",
        ),
    ),
    Dataset(
        id="dda_secondary_layers",
        pipelines=("P13",),
        name="DDA secondary planning layers",
        provider="Dubai Development Authority GIS",
        producer="holocron:dda_planning_layers",
        cadence="weekly",
        sources=("dda_planning_layers",),
        tables=(
            "dda_project_areas",
            "dda_subproject_areas",
            "dda_plot_building_limits",
            "dda_plot_podium_limits",
            "dda_plot_features",
            "dda_frozen_plots",
            "dda_landuse_symbols",
            "dda_plot_built_to_lines",
            "dda_plot_arcades",
            "dda_plot_retail",
        ),
        key=("object_id",),
        product=False,
        sla_hours=24 * 8,
        notes=("No backend consumer.",),
    ),
    Dataset(
        id="dxbi_map_buildings",
        pipelines=("P04",),
        name="DXBInteract map buildings",
        provider="DXBInteract",
        producer="holocron:dxbi_transactions (heatmap tables)",
        cadence="daily 06:00 Asia/Dubai",
        sources=("dxbi_transactions",),
        tables=("dxbi_heatmap_buildings_bronze",),
        key=("response_key",),
        serving=("ch_geo_buildings",),
        api=("/api/v1/projects/radar/building-pins",),
        frontend=("/radar", "/projects"),
        sla_hours=48,
        notes=("F11: DXBI extractor runs headless=False; the image has no Xvfb.",),
    ),
    Dataset(
        id="dxbi_other_bronze",
        pipelines=("P04",),
        name="DXBInteract transactions + other heatmap tables",
        provider="DXBInteract",
        producer="holocron:dxbi_transactions",
        cadence="daily 06:00 Asia/Dubai",
        sources=("dxbi_transactions",),
        tables=(
            "dxbi_transactions_bronze",
            "dxbi_heatmap_rental_buildings_bronze",
            "dxbi_heatmap_building_statuses_bronze",
            "dxbi_heatmap_chessboards_bronze",
            "dxbi_fam_map_buildings_bronze",
            "dxbi_heatmap_errors_bronze",
        ),
        product=False,
        sla_hours=48,
        notes=("No backend consumer.",),
    ),
    Dataset(
        id="dxbi_rental_evidence",
        pipelines=("P05", "P06", "P21", "P22"),
        name="DXBI rental evidence (CRM owner/lead evidence)",
        provider="DXBInteract",
        producer="off-Dagster: ph-box systemd dxbi_rental_backfill.py -> Mac launchd sync_market_data -> Mac ClickHouse",
        cadence="daily (systemd + Mac launchd 09:00)",
        raw_location="ph-box /root/data/dxbi-daily (not in object storage)",
        tables=("dxbi_rental_events", "dxbi_rental_unit_candidates", "dxbi_rental_sale_matches"),
        key=("event_key",),
        event_column="lease_start",
        quality_columns=("building_name", "annual_rent_aed", "unit_candidate_key", "size_sqft"),
        serving=(
            "dxbi_rental_events",
            "dxbi_rental_unit_candidates",
            "dxbi_rental_sale_matches",
            "geo_crm_rental_hierarchy",
        ),
        api=("/api/v1/crm/dashboard",),
        frontend=("/crm", "/crm/report/[leadId]", "/radar"),
        sla_hours=48,
        notes=(
            "F01: published only from the developer Mac; production ClickHouse has no "
            "in-platform producer for these tables.",
            "*_v2 tables and dxbi_rental_run_reconciliation are written but unconsumed.",
        ),
    ),
    Dataset(
        id="dxbi_sales_evidence",
        pipelines=("P05", "P21"),
        name="DXBI sales unit events",
        provider="DXBInteract",
        producer="off-Dagster: Mac sync_market_data loaders",
        cadence="daily (Mac launchd 09:00)",
        raw_location="ph-box /root/data/dxbi-daily (not in object storage)",
        tables=("dxbi_sales_unit_events",),
        key=("sale_key",),
        event_column="transaction_date",
        quality_columns=("building_name", "sale_amount_aed", "unit_number"),
        serving=("dxbi_sales_unit_events",),
        api=("/api/v1/crm/dashboard", "/api/v1/listings"),
        frontend=("/crm", "/listings", "/radar"),
        sla_hours=48,
        notes=("F01 applies.",),
    ),
    Dataset(
        id="pf_listings",
        pipelines=("P09",),
        name="Property Finder listings",
        provider="Property Finder",
        producer="holocron:pf_listings",
        cadence="daily 03:00 Asia/Dubai (spec metadata says manual)",
        sources=("pf_listings",),
        tables=("pf_listings_bronze",),
        key=("run_id", "property_key"),
        quality_columns=("listing_id", "price_json", "lat", "lng", "building_location_id"),
        serving=("pf_listings_bronze",),
        api=("/api/v1/listings",),
        frontend=("/listings", "/radar"),
        sla_hours=48,
        notes=(
            "Backend freshness = max(scraped_at) (backend/app/listings/queries.py).",
            "F18: withdrawn listings are never marked inactive.",
        ),
    ),
    Dataset(
        id="pf_listing_details",
        pipelines=("P09", "P10"),
        name="Property Finder listing details + media",
        provider="Property Finder",
        producer="holocron:pf_listings (detail chunks), pf_listing_media_assets_backfill",
        cadence="manual",
        sources=("pf_listings",),
        extra_jobs=("pf_listing_media_assets_backfill",),
        tables=("pf_listing_details_bronze",),
        key=("run_id", "detail_key"),
        product=False,
        notes=("No backend consumer.",),
    ),
    Dataset(
        id="geography",
        pipelines=("P08", "P19"),
        name="Geography crosswalk (PF locations -> canonical nodes)",
        provider="Property Finder locations + derived",
        producer="holocron:pf_locations (manual) + geography_crosswalk_refresh (manual job)",
        cadence="manual",
        sources=("pf_locations",),
        extra_jobs=("geography_crosswalk_refresh",),
        tables=(
            "pf_locations_bronze",
            "geo_location_nodes",
            "geo_location_closure",
            "geo_entity_links",
        ),
        key=("location_id",),
        serving=(
            "geo_location_nodes",
            "geo_location_closure",
            "geo_mapped_source_facts",
            "geo_crm_rental_hierarchy",
        ),
        api=("/api/v1/projects/radar/overview",),
        frontend=("/radar", "/crm"),
        notes=("No schedule; freshness depends on manual geography_crosswalk_refresh runs.",),
    ),
    Dataset(
        id="reelly_projects",
        pipelines=("P11",),
        name="Reelly off-plan project catalogue",
        provider="Reelly",
        producer="holocron:reelly_supply",
        cadence="daily 02:00 Asia/Dubai",
        sources=("reelly_supply",),
        tables=("reelly_projects_bronze",),
        key=("run_id", "project_key"),
        serving=("reelly_projects_bronze", "offplan_reelly_mapped_source_facts"),
        required_columns=(
            (
                "reelly_projects_bronze",
                (
                    "region",
                    "area_name",
                    "latitude",
                    "longitude",
                    "project_name",
                    "developer_name",
                    "sale_status",
                    "units_in_sale",
                    "min_price_aed",
                    "max_price_aed",
                    "completion_date",
                    "readiness_progress",
                    "website_url",
                ),
            ),
        ),
        api=("/api/v1/supply/upcoming-projects", "/api/v1/supply/catalogue/projects"),
        frontend=("/upcoming", "/upcoming/[projectId]"),
        sla_hours=48,
        static_breaks=(
            (
                "bronze",
                "SCHEMA_DRIFT",
                "Every supply query on reelly_projects_bronze applies reelly_dubai_project_scope() "
                "(backend/app/supply/scope.py) which reads plain columns region, area_name, latitude, "
                "longitude; upcoming-projects also selects project_name, sale_status, min_price_aed, ... "
                "The Holocron DDL/loader writes only project_key, run_id, scraped_at, project_id, "
                "delta_reason, list_item_json, raw_json (no ALTER adds the others). Backend tests "
                "hand-write a wider table (F02).",
            ),
        ),
    ),
    Dataset(
        id="reelly_unit_types",
        pipelines=("P23",),
        name="Reelly project unit types",
        provider="Reelly (presumed)",
        producer="none in repository",
        cadence="unknown",
        tables=("reelly_project_unit_types_bronze",),
        serving=("reelly_project_unit_types_bronze",),
        api=("/api/v1/supply/upcoming-projects",),
        frontend=("/upcoming",),
        static_breaks=(
            (
                "bronze",
                "MISSING",
                "backend/app/supply/queries.py reads reelly_project_unit_types_bronze; no DDL, "
                "loader or script in the repository creates or writes it (F02).",
            ),
        ),
    ),
    Dataset(
        id="silver_reelly_projects",
        pipelines=("P23",),
        name="Silver Reelly projects",
        provider="Reelly (presumed)",
        producer="none in repository",
        cadence="unknown",
        tables=("silver_reelly_projects",),
        serving=("silver_reelly_projects",),
        api=("/api/v1/projects/radar",),
        frontend=("/projects", "/radar"),
        static_breaks=(
            (
                "bronze",
                "MISSING",
                "backend/app/project/queries.py and radar_queries.py read silver_reelly_projects FINAL; "
                "no DDL, loader or script in the repository creates or writes it (F02).",
            ),
        ),
    ),
    Dataset(
        id="reelly_documents",
        pipelines=("P11", "P12"),
        name="Reelly documents / availability matrix / media",
        provider="Reelly",
        producer="holocron:reelly_supply + reelly_supply_*_assets_backfill",
        cadence="daily 02:30 (availability) / manual (media)",
        sources=("reelly_supply",),
        tables=("reelly_documents_bronze", "reelly_matrix_availability_bronze"),
        product=False,
        notes=("No backend consumer.",),
    ),
    Dataset(
        id="news",
        pipelines=("P14", "P15"),
        name="News articles",
        provider="RSS feeds (NEWS_FEED_URLS)",
        producer="holocron:news_feeds poll (15 min) -> manual news_publish_backfill / upload API -> Postgres",
        cadence="every 15 min (poll only)",
        sources=("news_feeds",),
        extra_jobs=("news_publish_backfill", "news_thumbnail_assets_backfill"),
        postgres=(("news_articles", "publish_date"),),
        api=("/api/v1/news/articles", "/api/v1/news/summary"),
        frontend=("/dashboard",),
        sla_hours=24,
        static_breaks=(
            (
                "publication",
                "PARTIAL",
                "news_feeds_poll_and_hydrate does not select the publish asset; news_articles is only "
                "written by the manual news_publish_backfill job or the upload/import path (F12).",
            ),
        ),
    ),
    Dataset(
        id="silver_dld",
        pipelines=("P18",),
        name="Silver DLD transactions / rent contracts",
        provider="derived from DLD bronze",
        producer="holocron:silver_*_refresh",
        cadence="daily 06:00/06:10 (overlapping silver_refresh)",
        extra_jobs=(
            "silver_transactions_refresh",
            "silver_rent_contracts_refresh",
            "silver_refresh",
        ),
        tables=("silver_transactions", "silver_rent_contracts"),
        key=("row_key",),
        product=False,
        sla_hours=48,
        notes=("No backend consumer; ch_* views read bronze directly.",),
    ),
    Dataset(
        id="bayut_listings",
        pipelines=("P17",),
        name="Bayut listings (probe / raw only)",
        provider="Bayut",
        producer="holocron:bayut_listings (manual, no bronze loader)",
        cadence="manual",
        sources=("bayut_listings",),
        product=False,
        notes=("Raw-only source: no bronze table, no consumer.",),
    ),
    Dataset(
        id="dld_mashrooi",
        pipelines=("P03",),
        name="DLD Mashrooi (demo)",
        provider="Dubai Land Department",
        producer="holocron:dld_mashrooi (manual, no bronze loader)",
        cadence="manual",
        sources=("dld_mashrooi",),
        product=False,
        notes=("Demo source (D6): no bronze table, no consumer.",),
    ),
    Dataset(
        id="crm_user_uploads",
        pipelines=("P24",),
        name="CRM contact imports (user uploads)",
        provider="end users",
        producer="backend /api/v1/crm/contact-imports",
        cadence="on demand",
        postgres=(
            ("crm_contact_imports", "created_at"),
            ("crm_contact_lists", "created_at"),
            ("crm_property_contact_claims", None),
        ),
        api=("/api/v1/crm/contact-lists",),
        frontend=("/crm",),
    ),
)

# GET probes that need no path parameters. Endpoints that need parameters are recorded
# as not probed rather than guessed.
_ITEM_KEYS = (
    "items",
    "data",
    "results",
    "rows",
    "articles",
    "projects",
    "listings",
    "points",
    "pins",
)


# ---------------------------------------------------------------------------- static


def parse_expected_schema(sql: str) -> dict[str, list[str]]:
    """Columns per table from CREATE TABLE bodies plus ALTER TABLE ADD COLUMN."""
    schema: dict[str, list[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS ([a-z0-9_]+)\s*\((.*?)\)\s*ENGINE", sql, re.S | re.I
    ):
        columns = schema.setdefault(match.group(1), [])
        for line in match.group(2).splitlines():
            line = line.strip()
            if (
                not line
                or line.startswith("--")
                or re.match(r"(INDEX|PROJECTION|CONSTRAINT)\b", line, re.I)
            ):
                continue
            column = re.match(r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+\S", line)
            if column and column.group(1) not in columns:
                columns.append(column.group(1))
    for match in re.finditer(
        r"ALTER TABLE ([a-z0-9_]+) ADD COLUMN IF NOT EXISTS `?([A-Za-z_][A-Za-z0-9_]*)`?", sql, re.I
    ):
        columns = schema.setdefault(match.group(1), [])
        if match.group(2) not in columns:
            columns.append(match.group(2))
    return schema


def parse_views(sql: str) -> list[str]:
    return re.findall(r"CREATE OR REPLACE VIEW ([a-z0-9_]+)", sql, re.I)


def backend_consumers(objects: Sequence[str]) -> dict[str, list[str]] | None:
    app_dir = _REPO_ROOT / "backend" / "app"
    if not app_dir.is_dir():
        return None
    texts = {p: p.read_text(encoding="utf-8") for p in app_dir.rglob("*.py")}
    return {
        obj: sorted(
            str(path.relative_to(_REPO_ROOT))
            for path, text in texts.items()
            if re.search(rf"\b{re.escape(obj)}\b", text)
        )
        for obj in objects
    }


def backend_routes(env: Mapping[str, str]) -> list[str] | None:
    """Paths registered on the FastAPI app (any method), or None when not importable."""
    backend_dir = _REPO_ROOT / "backend"
    python = backend_dir / ".venv" / "bin" / "python"
    if not python.exists():
        return None
    import subprocess

    script = (
        "from app.main import app\n"
        "for r in app.routes:\n"
        "    if getattr(r, 'methods', None): print(r.path)\n"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", script],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=120,
            env={**env, "ENVIRONMENT": env.get("ENVIRONMENT", "local")},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def frontend_paths() -> list[str] | None:
    api_dir = _REPO_ROOT / "frontend" / "src" / "lib" / "api"
    if not api_dir.is_dir():
        return None
    paths: set[str] = set()
    for path in api_dir.glob("*.ts"):
        for raw in re.findall(
            r"['\"`](/api/v1/[A-Za-z0-9/_{}$.()-]+)", path.read_text(encoding="utf-8")
        ):
            paths.add(re.sub(r"\$\{[^}]*\}", "{p}", raw))
    return sorted(paths)


def frontend_only_paths(front: Sequence[str], back: Sequence[str]) -> list[str]:
    """Frontend wrapper paths with no matching backend route."""

    def norm(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{p}", path.rstrip("/"))

    backend = {norm(p) for p in back}
    return sorted(
        norm(p)
        for p in front
        if norm(p) not in backend and not any(b.startswith(norm(p) + "/") for b in backend)
    )


def dagster_inventory() -> dict[str, Any]:
    from dagster import ScheduleDefinition

    from holocron.orchestration.definitions import defs
    from holocron.sources import registry

    schedules = [
        {
            "name": schedule.name,
            "job": schedule.job_name,
            "cron": schedule.cron_schedule,
            "default_status": str(schedule.default_status).rsplit(".", 1)[-1],
        }
        for schedule in defs.schedules or []
        if isinstance(schedule, ScheduleDefinition)
    ]
    jobs = sorted(getattr(job, "name", str(job)) for job in defs.jobs or [])
    sources = [
        {
            "name": source.name,
            "provider": source.provider,
            "cadence": source.cadence,
            "checkpoint_strategy": source.checkpoint_strategy,
            "bronze_tables": [table.name for table in source.bronze_tables],
            "has_bronze_loader": source.bronze_loader is not None,
        }
        for source in registry.list_sources()
    ]
    return {"schedules": schedules, "jobs": jobs, "sources": sources}


def jobs_for_source(source: str, jobs: Sequence[str]) -> list[str]:
    return [job for job in jobs if job == source or job.startswith(source + "_")]


def dataset_jobs(dataset: Dataset, jobs: Sequence[str]) -> list[str]:
    found = [j for s in dataset.sources for j in jobs_for_source(s, jobs)]
    return found + [j for j in dataset.extra_jobs if j in jobs and j not in found]


# ---------------------------------------------------------------------------- live


def _scrub(text: str, env: Mapping[str, str]) -> str:
    for key, value in env.items():
        if value and len(value) >= 6 and _SECRET_KEY.search(key):
            text = text.replace(value, "***")
    return text[:500]


def _q(identifier: str) -> str:
    return "`" + identifier.replace("`", "") + "`"


def probe_clickhouse(
    env: Mapping[str, str], datasets: Sequence[Dataset], expected: Mapping[str, list[str]]
) -> dict[str, Any]:
    required = ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_DATABASE")
    if any(not env.get(key) for key in required):
        return {"access": UNKNOWN_ENV, "detail": "CLICKHOUSE_* not set"}
    import clickhouse_connect

    connect: dict[str, Any] = {
        "host": env["CLICKHOUSE_HOST"],
        "port": int(env["CLICKHOUSE_PORT"]),
        "username": env["CLICKHOUSE_USER"],
        "password": env.get("CLICKHOUSE_PASSWORD", ""),
        "database": env["CLICKHOUSE_DATABASE"],
        "secure": _truthy(env.get("CLICKHOUSE_SECURE", "false")),
        "connect_timeout": 10,
        "send_receive_timeout": 300,
    }
    started = time.monotonic()
    try:
        # readonly=2 blocks writes but still allows the per-query timeout setting.
        client = clickhouse_connect.get_client(
            **connect, settings={"readonly": 2, "max_execution_time": 240}
        )
        client.command("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - diagnostic command
        try:
            # A user already in a readonly=1 profile rejects setting overrides; it is read-only anyway.
            client = clickhouse_connect.get_client(**connect)
            client.command("SELECT 1")
        except Exception:  # noqa: BLE001
            return {"access": UNKNOWN_ENV, "detail": "connection failed: " + _scrub(str(exc), env)}

    def rows(sql: str) -> list[Sequence[Any]]:
        return list(client.query(sql).result_rows)

    tables = {
        name: {
            "engine": engine,
            "total_rows": total_rows,
            "total_bytes": total_bytes,
            "metadata_modified": str(modified),
        }
        for name, engine, total_rows, total_bytes, modified in rows(
            "SELECT name, engine, total_rows, total_bytes, metadata_modification_time "
            "FROM system.tables WHERE database = currentDatabase()"
        )
    }
    columns: dict[str, list[str]] = {}
    for table, column in rows(
        "SELECT table, name FROM system.columns WHERE database = currentDatabase() ORDER BY table, position"
    ):
        columns.setdefault(table, []).append(column)

    objects: dict[str, Any] = {}

    def measure(obj: str, dataset: Dataset | None, primary: bool) -> dict[str, Any]:
        info: dict[str, Any] = {"exists": obj in tables}
        if obj not in tables:
            return info
        info.update(tables[obj])
        live_columns = columns.get(obj, [])
        info["columns"] = live_columns
        if obj in expected:
            info["missing_vs_ddl"] = [c for c in expected[obj] if c not in live_columns]
            info["unexpected_vs_ddl"] = [c for c in live_columns if c not in expected[obj]]
        if dataset:
            for table, needed in dataset.required_columns:
                if table == obj:
                    info["missing_required_by_backend"] = [
                        c for c in needed if c not in live_columns
                    ]
        try:
            info["row_count"] = rows(f"SELECT count() FROM {_q(obj)}")[0][0]
            info["query_ok"] = True
        except Exception as exc:  # noqa: BLE001
            info["query_ok"] = False
            info["query_error"] = _scrub(str(exc), env)
            return info
        fresh = next((c for c in FRESHNESS_COLUMNS if c in live_columns), None)
        info["freshness_column"] = fresh
        if fresh and info["row_count"]:
            info["freshness_column"] = fresh
            info["latest_record"] = str(
                rows(
                    f"SELECT max(parseDateTimeBestEffortOrNull(toString({_q(fresh)}))) FROM {_q(obj)}"
                )[0][0]
            )
        if not (dataset and primary and info["row_count"]):
            return info
        try:
            if dataset.event_column and dataset.event_column in live_columns:
                low, high = rows(
                    f"SELECT min(d), max(d) FROM (SELECT parseDateTimeBestEffortOrNull(toString({_q(dataset.event_column)})) AS d "
                    f"FROM {_q(obj)}) WHERE d IS NOT NULL AND d > '1971-01-01'"
                )[0]
                info["event_coverage"] = {
                    "column": dataset.event_column,
                    "min": str(low),
                    "max": str(high),
                }
            key = [c for c in dataset.key if c in live_columns]
            if key and len(key) == len(dataset.key):
                tuple_sql = ", ".join(_q(c) for c in key)
                info["duplicate_keys"] = {
                    "key": key,
                    "extra_rows": rows(
                        f"SELECT count() - uniqExact(tuple({tuple_sql})) FROM {_q(obj)}"
                    )[0][0],
                }
            # Key collisions can be legitimate (multi-property rent contracts, versions).
            # Exact duplicates — identical in every business column after dropping load
            # metadata — are the reload/overlap defect (F03). Snapshot-per-run tables
            # (key contains run_id) keep repeats by design and are skipped.
            business = [c for c in live_columns if c not in LOAD_METADATA_COLUMNS]
            if "run_id" not in dataset.key and business:
                info["exact_duplicates"] = {
                    "compared_columns": len(business),
                    "extra_rows": rows(
                        f"SELECT count() - uniqExact(tuple({', '.join(_q(c) for c in business)})) "
                        f"FROM {_q(obj)}"
                    )[0][0],
                }
            quality = [c for c in (*dataset.key, *dataset.quality_columns) if c in live_columns]
            if quality:
                exprs = ", ".join(
                    f"round(100 * countIf(isNull({_q(c)}) OR toString({_q(c)}) IN ('', '0', '[]', '{{}}')) / count(), 2)"
                    for c in quality
                )
                split = dataset.split_column if dataset.split_column in live_columns else None
                if split:
                    result = rows(
                        f"SELECT toString({_q(split)}), count(), {exprs} FROM {_q(obj)} GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
                    )
                    info["empty_or_default_pct"] = {
                        str(r[0]): {"rows": r[1], **dict(zip(quality, r[2:], strict=True))}
                        for r in result
                    }
                else:
                    result = rows(f"SELECT {exprs} FROM {_q(obj)}")[0]
                    info["empty_or_default_pct"] = {"all": dict(zip(quality, result, strict=True))}
        except Exception as exc:  # noqa: BLE001
            info["measure_error"] = _scrub(str(exc), env)
        return info

    for dataset in datasets:
        for index, obj in enumerate(
            (*dataset.tables, *[s for s in dataset.serving if s not in dataset.tables])
        ):
            if obj not in objects:
                objects[obj] = measure(obj, dataset, primary=index == 0)

    ledger: dict[str, Any] = {}
    if "bronze_load_ledger" in tables:
        for source, table, last_at, last_status, last_error, last_success in rows(
            "SELECT source, table_name, max(loaded_at), argMax(status, loaded_at), argMax(error, loaded_at), "
            "maxIf(loaded_at, status = 'success') FROM bronze_load_ledger GROUP BY source, table_name"
        ):
            ledger[f"{source}:{table}"] = {
                "last_loaded_at": str(last_at),
                "last_status": last_status,
                "last_error": _scrub(last_error or "", env),
                "last_success_at": str(last_success),
            }
    known = set(expected) | {o for d in datasets for o in (*d.tables, *d.serving)}
    return {
        "access": "ok",
        "database_objects": len(tables),
        "database_bytes": sum(int(t["total_bytes"] or 0) for t in tables.values()),
        "objects": objects,
        "bronze_load_ledger": ledger,
        "unowned_objects": sorted(set(tables) - known),
        "probe_seconds": round(time.monotonic() - started, 2),
    }


def probe_object_storage(env: Mapping[str, str], sources: Sequence[str]) -> dict[str, Any]:
    required = (
        "OBJECT_STORAGE_ACCESS_KEY_ID",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "OBJECT_STORAGE_BUCKET",
    )
    if any(not env.get(key) for key in required):
        return {"access": UNKNOWN_ENV, "detail": "OBJECT_STORAGE_* not set"}
    import boto3
    from botocore.config import Config

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=env["OBJECT_STORAGE_ACCESS_KEY_ID"],
            aws_secret_access_key=env["OBJECT_STORAGE_SECRET_ACCESS_KEY"],
            region_name=env.get("OBJECT_STORAGE_REGION") or "auto",
            endpoint_url=env.get("OBJECT_STORAGE_ENDPOINT_URL") or None,
            config=Config(
                s3={
                    "addressing_style": "path"
                    if _truthy(env.get("OBJECT_STORAGE_USE_PATH_STYLE", "true"))
                    else "auto"
                },
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2},
            ),
        )
        bucket = env["OBJECT_STORAGE_BUCKET"]
        client.head_bucket(Bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        return {"access": UNKNOWN_ENV, "detail": "connection failed: " + _scrub(str(exc), env)}

    result: dict[str, Any] = {"access": "ok", "bucket": bucket, "sources": {}}
    for source in sources:
        entry: dict[str, Any] = {}
        try:
            manifests = []
            for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=f"raw/source={source}/manifests/"
            ):
                manifests.extend(page.get("Contents", []))
            entry["manifest_count"] = len(manifests)
            if manifests:
                latest = max(manifests, key=lambda o: o["LastModified"])
                entry["latest_manifest_key"] = latest["Key"]
                entry["latest_manifest_modified"] = latest["LastModified"].isoformat()
                body = json.loads(
                    client.get_object(Bucket=bucket, Key=latest["Key"])["Body"].read()
                )
                files = body.get("files", [])
                entry["latest_manifest"] = {
                    "run_id": body.get("run_id"),
                    "extract_date": body.get("extract_date"),
                    "created_at": body.get("created_at"),
                    "mode": (body.get("metadata") or {}).get("mode"),
                    "files": len(files),
                    "rows": sum(int(f.get("row_count") or 0) for f in files),
                    "bytes": sum(int(f.get("size_bytes") or 0) for f in files),
                    "files_with_sha256": sum(1 for f in files if f.get("sha256")),
                }
        except Exception as exc:  # noqa: BLE001
            entry["error"] = _scrub(str(exc), env)
        result["sources"][source] = entry
    return result


def _pg_url(url: str) -> str:
    return re.sub(r"^postgres(ql)?\+\w+://", "postgresql://", url)


def probe_dagster(
    env: Mapping[str, str], jobs: Sequence[str], schedules: Sequence[str]
) -> dict[str, Any]:
    url = env.get("DAGSTER_POSTGRES_URL", "").strip()
    if not url:
        return {"access": UNKNOWN_ENV, "detail": "DAGSTER_POSTGRES_URL not set"}
    import psycopg

    schedule_state: dict[str, str] | str = {}
    try:
        with psycopg.connect(_pg_url(url), connect_timeout=10) as connection:
            connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pipeline_name, status, start_time, end_time, create_timestamp FROM runs "
                    "WHERE pipeline_name = ANY(%s) ORDER BY create_timestamp DESC",
                    (list(jobs),),
                )
                runs = cursor.fetchall()
            try:
                with connection.cursor() as cursor:
                    # Schedule names live inside the serialized instigator body.
                    cursor.execute(
                        "SELECT status, instigator_body FROM instigators WHERE instigator_type = 'SCHEDULE'"
                    )
                    for status, body in cursor.fetchall():
                        name = re.search(r'"(?:instigator_name|job_name)":\s*"([^"]+)"', body or "")
                        if name and name.group(1) in schedules:
                            schedule_state[name.group(1)] = status
            except Exception as exc:  # noqa: BLE001
                connection.rollback()
                schedule_state = "unreadable: " + _scrub(str(exc), env)
    except Exception as exc:  # noqa: BLE001
        return {"access": UNKNOWN_ENV, "detail": "query failed: " + _scrub(str(exc), env)}

    per_job: dict[str, Any] = {}
    for job, status, start, end, created in runs:
        entry = per_job.setdefault(job, {"runs": 0, "failures": 0})
        entry["runs"] += 1
        entry["failures"] += status == "FAILURE"
        runtime = round(end - start, 1) if start and end else None
        if "last_run" not in entry:
            entry["last_run"] = {"status": status, "created": str(created), "runtime_s": runtime}
        if status == "SUCCESS" and "last_success" not in entry:
            entry["last_success"] = {"created": str(created), "runtime_s": runtime}
    return {"access": "ok", "jobs": per_job, "schedule_state": schedule_state}


def probe_app_postgres(env: Mapping[str, str], datasets: Sequence[Dataset]) -> dict[str, Any]:
    url = (env.get("PLATFORM_POSTGRES_URL") or env.get("POSTGRES_URL") or "").strip()
    if not url:
        return {"access": UNKNOWN_ENV, "detail": "PLATFORM_POSTGRES_URL / POSTGRES_URL not set"}
    import psycopg
    from psycopg import sql

    tables: dict[str, Any] = {}
    try:
        with psycopg.connect(_pg_url(url), connect_timeout=10) as connection:
            connection.read_only = True
            for dataset in datasets:
                for table, ts in dataset.postgres:
                    with connection.cursor() as cursor:
                        try:
                            query = sql.SQL("SELECT count(*), {} FROM {}").format(
                                sql.SQL("max({})").format(sql.Identifier(ts))
                                if ts
                                else sql.SQL("NULL"),
                                sql.Identifier(table),
                            )
                            cursor.execute(query)
                            count, latest = cursor.fetchone() or (0, None)
                            tables[table] = {
                                "row_count": count,
                                "latest_record": str(latest) if latest else None,
                            }
                        except Exception as exc:  # noqa: BLE001
                            connection.rollback()
                            tables[table] = {"error": _scrub(str(exc), env)}
    except Exception as exc:  # noqa: BLE001
        return {"access": UNKNOWN_ENV, "detail": "connection failed: " + _scrub(str(exc), env)}
    return {"access": "ok", "tables": tables}


def _count_items(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in _ITEM_KEYS:
            if isinstance(payload.get(key), list):
                return len(payload[key])
        if isinstance(payload.get("total"), int):
            return payload["total"]
    return None


def probe_api(env: Mapping[str, str], base: str, datasets: Sequence[Dataset]) -> dict[str, Any]:
    if not base:
        return {"access": UNKNOWN_ENV, "detail": "no API base (--api-base / CENSUS_API_BASE)"}
    header = env.get("CENSUS_API_AUTH_HEADER", "")
    headers = {"Accept": "application/json"}
    if ":" in header:
        name, value = header.split(":", 1)
        headers[name.strip()] = value.strip()
    results: dict[str, Any] = {}
    for path in sorted({p for d in datasets for p in d.api}):
        request = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="GET")
        started = time.monotonic()
        entry: dict[str, Any] = {}
        try:
            # Cold CRM reads were recorded at ~180s; allow them to finish so latency is measured.
            with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310 - GET on configured base
                body = response.read()
                entry["status"] = response.status
                entry["bytes"] = len(body)
                try:
                    entry["items"] = _count_items(json.loads(body))
                except ValueError:
                    entry["items"] = None
        except urllib.error.HTTPError as exc:
            entry["status"] = exc.code
        except Exception as exc:  # noqa: BLE001
            entry["error"] = _scrub(str(exc), env)
        entry["latency_ms"] = round(1000 * (time.monotonic() - started))
        results[path] = entry
    if all(r.get("status") in (401, 403) for r in results.values()):
        return {
            "access": UNKNOWN_ENV,
            "detail": "API reachable but unauthenticated (set CENSUS_API_AUTH_HEADER)",
            "endpoints": results,
        }
    if all("error" in r for r in results.values()):
        return {"access": UNKNOWN_ENV, "detail": "API unreachable", "endpoints": results}
    return {"access": "ok", "endpoints": results}


def load_recorded_evidence(path: Path) -> dict[str, Any]:
    """Normalise a recorded verification report ({features: [...], browser: {pages: [...]}}).

    This is the format written by the handoff verification (verification-report.json).
    Only paths, statuses, timings and scalar counts are kept.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    api: dict[str, Any] = {}
    for feature in data.get("features", []):
        route = str(feature.get("path", "")).split("?", 1)[0]
        summary = {
            k: v
            for k, v in feature.items()
            if k not in ("path", "status", "seconds", "fields")
            and isinstance(v, (int, float, str, bool))
        }
        api[route] = {
            "status": feature.get("status"),
            "seconds": feature.get("seconds"),
            "summary": summary,
        }
    pages = {
        page["page"]: {
            "status": page.get("status"),
            "data_status": page.get("data_status"),
            "api_responses": page.get("api_responses", []),
        }
        for page in (data.get("browser") or {}).get("pages", [])
        if str(page.get("page", "")).startswith("/")
    }
    return {"source": path.name, "api": api, "pages": pages}


# ---------------------------------------------------------------------------- assess


def _age_hours(value: str | None, now: datetime) -> float | None:
    if not value or value in ("None", "NaT"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round((now - parsed).total_seconds() / 3600, 1)


def assess(
    dataset: Dataset, live: Mapping[str, Any], static: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Per-stage evidence and classification for one dataset.

    Stage status is one of ok / broken / partial / stale / unknown / n/a. `classification`
    uses live evidence only (UNKNOWN when there is none); `static_classification` is what
    the code alone proves.
    """
    stages: dict[str, dict[str, Any]] = {s: {"status": "unknown", "evidence": []} for s in STAGES}
    live_marks: list[str] = []
    live_ok = False

    def mark(stage: str, status: str, evidence: str, cls: str | None = None) -> None:
        nonlocal live_ok
        entry = stages[stage]
        rank = ("unknown", "n/a", "ok", "stale", "partial", "broken")
        if rank.index(status) >= rank.index(entry["status"]):
            entry["status"] = status
            if cls:
                entry["classification"] = cls
        entry["evidence"].append(evidence)
        if status in ("broken", "partial", "stale"):
            entry.setdefault("problems", []).append(evidence)
        if evidence.startswith("static:"):
            return
        if cls and status in ("broken", "partial", "stale"):
            live_marks.append(cls)
        if status == "ok" and stage in ("bronze", "serving", "api"):
            live_ok = True

    ch, s3, dagster, pg, api = (
        live[k] for k in ("clickhouse", "object_storage", "dagster", "app_postgres", "api")
    )
    # Downstream rows prove an upstream stage ran somewhere, even when the probed
    # Dagster instance or bucket holds no trace of it (e.g. a restored snapshot).
    has_data = any(
        (ch.get("objects") or {}).get(t, {}).get("row_count") for t in dataset.tables
    ) or any((pg.get("tables") or {}).get(t, {}).get("row_count") for t, _ in dataset.postgres)

    # SOURCE / EXTRACTION — judged on the most recent run across all of the dataset's jobs,
    # so an old failed manual backfill does not mask a healthy daily job.
    jobs = dataset_jobs(dataset, static["jobs"])
    if jobs:
        mark(
            "source",
            "unknown",
            "provider not probed directly: the census never calls third-party providers "
            "(licensing decision D1 pending); availability is inferred only from run history",
        )
    if not jobs and dataset.postgres and not dataset.sources:
        mark("source", "n/a", f"user-generated via {dataset.producer}")
        mark("extraction", "n/a", "no extraction: rows are written by the application")
    elif not jobs:
        off = dataset.producer if dataset.producer != "none in repository" else "no producer"
        mark("source", "unknown", f"not observable from this platform ({off})")
        mark("extraction", "unknown", "off-platform or absent producer; no run history in Dagster")
    elif dagster.get("access") == "ok":
        seen = {j: dagster["jobs"][j] for j in jobs if j in dagster["jobs"]}
        if not seen and has_data:
            mark(
                "extraction",
                "unknown",
                f"no runs for {', '.join(jobs)} in this Dagster instance, but the tables hold data: "
                "run history lives in another instance and cannot be verified here",
            )
        elif not seen:
            mark(
                "extraction", "broken", f"no Dagster runs recorded for {', '.join(jobs)}", "MISSING"
            )
        else:
            job, info = max(seen.items(), key=lambda item: item[1]["last_run"]["created"])
            last = info["last_run"]
            success = max(
                (i["last_success"]["created"] for i in seen.values() if "last_success" in i),
                default=None,
            )
            evidence = f"latest run {job}: {last['status']} at {last['created']} ({last['runtime_s']}s); last success any job: {success}"
            if last["status"] == "SUCCESS":
                mark("extraction", "ok", evidence)
                mark("source", "ok", f"{job} fetched successfully")
            elif last["status"] == "FAILURE":
                mark("extraction", "broken", evidence, "PIPELINE_FAILED")
            else:
                mark("extraction", "unknown", evidence)
    else:
        mark("extraction", "unknown", f"Dagster run history: {dagster.get('access')}")

    # RAW
    if dataset.raw_location:
        mark("raw", "unknown", f"raw lives at {dataset.raw_location}")
    elif not dataset.sources:
        mark("raw", "n/a" if dataset.postgres else "unknown", "no raw layer for this producer")
    elif s3.get("access") == "ok":
        entries = {source: s3["sources"].get(source, {}) for source in dataset.sources}
        for source, entry in entries.items():
            if entry.get("error"):
                mark("raw", "unknown", f"{source}: {entry['error']}")
            elif entry.get("manifest_count"):
                m = entry["latest_manifest"]
                mark(
                    "raw",
                    "ok",
                    f"{source}: {entry['manifest_count']} manifests; latest {entry['latest_manifest_modified']} ({m['files']} files, {m['rows']} rows, {m['files_with_sha256']}/{m['files']} sha256)",
                )
        empty = [
            s for s, e in entries.items() if not e.get("error") and not e.get("manifest_count")
        ]
        where = f"bucket {s3.get('bucket')}: no manifests under raw/source=<s>/manifests/ for {', '.join(empty)}"
        if empty and len(empty) == len(entries) and has_data:
            # Bronze has data, so raw was kept somewhere else (another bucket or host disk).
            mark(
                "raw",
                "unknown",
                where
                + "; bronze has rows, so raw lineage is held elsewhere and is not replayable from this store",
            )
        elif empty and len(empty) == len(entries):
            mark("raw", "broken", where, "MISSING")
        elif empty:
            mark("raw", "ok", f"no manifests yet for secondary source(s) {', '.join(empty)}")
    else:
        mark("raw", "unknown", f"object storage: {s3.get('access')}")

    # BRONZE
    for stage, cls, evidence in dataset.static_breaks:
        if stage == "bronze":
            mark(
                "bronze",
                "broken" if cls in ("MISSING", "SCHEMA_DRIFT") else "partial",
                "static: " + evidence,
                cls,
            )
    if dataset.tables and ch.get("access") == "ok":
        for index, table in enumerate(dataset.tables):
            info = ch["objects"].get(table, {})
            if not info.get("exists"):
                mark("bronze", "broken", f"{table}: table does not exist", "MISSING")
                continue
            if not info.get("query_ok", True):
                mark(
                    "bronze",
                    "broken",
                    f"{table}: query failed: {info.get('query_error')}",
                    "SCHEMA_DRIFT",
                )
                continue
            if not info.get("row_count"):
                mark("bronze", "broken", f"{table}: 0 rows", "MISSING")
                continue
            mark(
                "bronze",
                "ok",
                f"{table}: {info['row_count']} rows, {info.get('total_bytes')} bytes, latest {info.get('latest_record')}"
                + (
                    ""
                    if info.get("freshness_column")
                    else " (no load-timestamp column: freshness and lineage unmeasurable)"
                ),
            )
            if info.get("missing_required_by_backend"):
                mark(
                    "bronze",
                    "broken",
                    f"{table}: backend-required columns missing live: {info['missing_required_by_backend']}",
                    "SCHEMA_DRIFT",
                )
            if info.get("missing_vs_ddl"):
                mark(
                    "bronze",
                    "partial",
                    f"{table}: columns in DDL but not live: {info['missing_vs_ddl']}",
                    "SCHEMA_DRIFT",
                )
            if info.get("unexpected_vs_ddl"):
                # Recorded, not classified: extra live columns do not break readers, but
                # show the live table was shaped by a producer other than the repo DDL.
                mark(
                    "bronze",
                    "ok",
                    f"{table}: live has {len(info['unexpected_vs_ddl'])} columns not in repo DDL: "
                    f"{', '.join(info['unexpected_vs_ddl'][:15])}",
                )
            key_dup = info.get("duplicate_keys", {}).get("extra_rows")
            if key_dup:
                mark(
                    "bronze",
                    "ok",
                    f"{table}: {key_dup} rows share key {info['duplicate_keys']['key']} "
                    "(measurement only; the key may legitimately repeat)",
                )
            exact = info.get("exact_duplicates", {}).get("extra_rows")
            if exact:
                mark(
                    "bronze",
                    "partial",
                    f"{table}: {exact} exact duplicate rows (identical across "
                    f"{info['exact_duplicates']['compared_columns']} business columns)",
                    "PARTIAL",
                )
            coverage = info.get("event_coverage") or {}
            if coverage.get("max") and coverage["max"] > now.strftime("%Y-%m-%d %H:%M:%S"):
                mark(
                    "bronze",
                    "ok",
                    f"{table}: anomaly — {coverage['column']} reaches {coverage['max']}, after the census time",
                )
            age = _age_hours(info.get("latest_record"), now)
            if index == 0 and dataset.sla_hours and age is not None and age > dataset.sla_hours:
                mark(
                    "bronze",
                    "stale",
                    f"{table}: latest record {age}h old (SLA {dataset.sla_hours}h)",
                    "STALE",
                )
    elif dataset.tables:
        mark("bronze", "unknown", f"ClickHouse: {ch.get('access')}")
    if dataset.postgres:
        if pg.get("access") == "ok":
            for table, _ in dataset.postgres:
                info = pg["tables"].get(table, {})
                if info.get("error"):
                    mark("bronze", "broken", f"postgres {table}: {info['error']}", "MISSING")
                elif not info.get("row_count"):
                    mark("bronze", "broken", f"postgres {table}: 0 rows", "MISSING")
                else:
                    mark(
                        "bronze",
                        "ok",
                        f"postgres {table}: {info['row_count']} rows, latest {info.get('latest_record')}",
                    )
                    age = _age_hours(info.get("latest_record"), now)
                    if dataset.sla_hours and age is not None and age > dataset.sla_hours:
                        mark(
                            "bronze",
                            "stale",
                            f"postgres {table}: latest {age}h old (SLA {dataset.sla_hours}h)",
                            "STALE",
                        )
        else:
            mark("bronze", "unknown", f"app Postgres: {pg.get('access')}")

    # PUBLICATION
    for stage, cls, evidence in dataset.static_breaks:
        if stage == "publication":
            mark("publication", "partial", "static: " + evidence, cls)
    if ch.get("access") == "ok":
        entries = {
            k: v for k, v in ch["bronze_load_ledger"].items() if k.split(":")[0] in dataset.sources
        }
        for key, info in entries.items():
            status = "ok" if info["last_status"] == "success" else "broken"
            mark(
                "publication",
                status,
                f"ledger {key}: {info['last_status']} at {info['last_loaded_at']} {info['last_error']}".strip(),
                None if status == "ok" else "PIPELINE_FAILED",
            )
        if dataset.sources and not entries:
            mark(
                "publication",
                "unknown",
                "no bronze_load_ledger rows (only bronze_replay writes the ledger)",
            )

    # SERVING
    if dataset.serving and ch.get("access") == "ok":
        for obj in dataset.serving:
            info = ch["objects"].get(obj, {})
            if not info.get("exists"):
                mark("serving", "broken", f"{obj}: does not exist", "DOWNSTREAM_BROKEN")
            elif not info.get("query_ok", True):
                mark(
                    "serving",
                    "broken",
                    f"{obj}: query failed: {info.get('query_error')}",
                    "DOWNSTREAM_BROKEN",
                )
            elif not info.get("row_count"):
                mark("serving", "broken", f"{obj}: 0 rows", "MISSING")
            else:
                mark("serving", "ok", f"{obj} ({info.get('engine')}): {info['row_count']} rows")
    elif dataset.serving:
        mark("serving", "unknown", f"ClickHouse: {ch.get('access')}")
    else:
        mark("serving", "n/a", "no serving object")

    # BACKEND (static: which files query the serving objects)
    consumers = static["backend_consumers"]
    if consumers is None:
        mark("backend", "unknown", "backend source not present")
    elif not dataset.product:
        mark("backend", "n/a", "not consumed by the backend")
    elif dataset.postgres:
        mark("backend", "ok", "static: backend owns these Postgres tables")
    else:
        files = sorted({f for obj in dataset.serving for f in consumers.get(obj, [])})
        mark(
            "backend",
            "ok" if files else "broken",
            "static: queried in " + (", ".join(files) or "no backend file"),
            None if files else "DOWNSTREAM_BROKEN",
        )

    # API
    for stage, cls, evidence in dataset.static_breaks:
        if stage == "api":
            mark("api", "partial", "static: " + evidence, cls)
    if not dataset.product:
        mark("api", "n/a", "not exposed")
    elif api.get("access") == "ok":
        for path in dataset.api:
            result = api["endpoints"].get(path, {})
            status = result.get("status")
            if status == 200 and result.get("items") == 0:
                mark("api", "broken", f"GET {path}: 200 with 0 items", "MISSING")
            elif status == 200:
                mark(
                    "api",
                    "ok",
                    f"GET {path}: 200, items={result.get('items')}, {result.get('latency_ms')}ms",
                )
            elif status in (400, 422):
                mark("api", "unknown", f"GET {path}: {status} (probe needs parameters)")
            else:
                mark(
                    "api",
                    "broken",
                    f"GET {path}: {status or result.get('error')}",
                    "DOWNSTREAM_BROKEN",
                )
    else:
        mark("api", "unknown", f"API: {api.get('access')}")

    # Recorded observations of the same environment (e.g. a snapshot's verification
    # report). Labelled "recorded", never presented as a probe made by this census.
    recorded = live.get("recorded") or {}
    if dataset.product and api.get("access") != "ok":
        for path in dataset.api:
            entry = (recorded.get("api") or {}).get(path)
            if not entry:
                continue
            status = entry.get("status")
            detail = f"recorded ({recorded['source']}): GET {path} {status} in {entry.get('seconds')}s; {entry.get('summary')}"
            if status == 200:
                mark("api", "ok", detail)
            else:
                mark("api", "broken", detail, "DOWNSTREAM_BROKEN")

    # FRONTEND (static breaks + recorded browser checks; no live browser here)
    for stage, cls, evidence in dataset.static_breaks:
        if stage == "frontend":
            mark("frontend", "broken", "static: " + evidence, cls)
    if dataset.product:
        for route in dataset.frontend:
            page = (recorded.get("pages") or {}).get(route)
            if not page:
                continue
            calls = ", ".join(f"{c['path']} {c['status']}" for c in page.get("api_responses", []))
            detail = f"recorded ({recorded['source']}): page {route} status {page.get('status')}, data {page.get('data_status')}; calls: {calls}"
            if page.get("status") == 200 and page.get("data_status") == 200:
                mark("frontend", "ok", detail)
            else:
                mark("frontend", "broken", detail, "DOWNSTREAM_BROKEN")
    if not dataset.product:
        mark("frontend", "n/a", "not in product")
    elif stages["frontend"]["status"] == "unknown":
        mark(
            "frontend",
            "unknown",
            "routes "
            + ", ".join(dataset.frontend)
            + " (render not verified; needs browser session)",
        )

    first_break = next(
        (s for s in STAGES if stages[s]["status"] in ("broken", "partial", "stale")), None
    )
    static_marks = [cls for _, cls, _ in dataset.static_breaks]
    classification = _pick(live_marks) or ("AVAILABLE" if live_ok else "UNKNOWN")
    return {
        "stages": stages,
        "first_break_stage": first_break,
        "classification": classification,
        "static_classification": _pick(static_marks),
    }


def _pick(marks: Sequence[str | None]) -> str | None:
    """Most severe classification: missing data beats drift beats failure beats degraded."""
    order = ("MISSING", "SCHEMA_DRIFT", "PIPELINE_FAILED", "DOWNSTREAM_BROKEN", "STALE", "PARTIAL")
    present = [m for m in marks if m]
    return min(present, key=order.index) if present else None


# ---------------------------------------------------------------------------- report


def build_report(
    env: Mapping[str, str],
    api_base: str,
    *,
    live_enabled: bool = True,
    as_of: datetime | None = None,
    label: str = "",
    recorded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated = datetime.now(UTC)
    # Staleness is measured against as_of: for a snapshot environment pass the snapshot
    # time, otherwise the snapshot's own age is misreported as pipeline staleness.
    now = as_of or generated
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    expected = parse_expected_schema(sql)
    inventory = dagster_inventory()
    mapped_sources = {s for d in DATASETS for s in d.sources}
    datasets = list(DATASETS) + [
        Dataset(
            id=f"unmapped:{s['name']}",
            pipelines=(),
            name=s["name"],
            provider=s["provider"],
            producer=f"holocron:{s['name']}",
            cadence=str(s["cadence"]),
            sources=(s["name"],),
            tables=tuple(s["bronze_tables"]),
            product=False,
            notes=(
                "Registered Holocron source not mapped to a census dataset; add it to DATASETS.",
            ),
        )
        for s in inventory["sources"]
        if s["name"] not in mapped_sources
    ]
    all_objects = sorted(
        {o for d in datasets for o in (*d.tables, *d.serving, *(t for t, _ in d.postgres))}
    )
    routes = backend_routes(env)
    front = frontend_paths()
    static = {
        "jobs": inventory["jobs"],
        "backend_consumers": backend_consumers(all_objects),
    }
    schedules = [s["name"] for s in inventory["schedules"]]
    sources = sorted({s for d in datasets for s in d.sources})
    unknown = {"access": UNKNOWN_ENV, "detail": "live probes disabled (--static-only)"}
    live = {
        "clickhouse": probe_clickhouse(env, datasets, expected) if live_enabled else unknown,
        "object_storage": probe_object_storage(env, sources) if live_enabled else unknown,
        "dagster": probe_dagster(env, inventory["jobs"], schedules) if live_enabled else unknown,
        "app_postgres": probe_app_postgres(env, datasets) if live_enabled else unknown,
        "api": probe_api(env, api_base, datasets) if live_enabled else unknown,
        "recorded": recorded or {},
    }
    results = []
    for dataset in datasets:
        assessment = assess(dataset, live, static, now)
        results.append(
            {
                "id": dataset.id,
                "pipelines": list(dataset.pipelines),
                "name": dataset.name,
                "provider": dataset.provider,
                "producer": dataset.producer,
                "cadence": dataset.cadence,
                "product_facing": dataset.product,
                "sources": list(dataset.sources),
                "jobs": dataset_jobs(dataset, inventory["jobs"]),
                "raw_location": dataset.raw_location
                or ", ".join(f"raw/source={s}/" for s in dataset.sources)
                or None,
                "tables": list(dataset.tables),
                "serving": list(dataset.serving),
                "api": list(dataset.api),
                "frontend": list(dataset.frontend),
                "backend_consumers": sorted(
                    {
                        f
                        for o in (*dataset.serving, *(tbl for tbl, _ in dataset.postgres))
                        for f in (static["backend_consumers"] or {}).get(o, [])
                    }
                ),
                "notes": list(dataset.notes),
                "static_breaks": [
                    {"stage": s, "classification": c, "evidence": e}
                    for s, c, e in dataset.static_breaks
                ],
                **assessment,
            }
        )
    return {
        "census_version": CENSUS_VERSION,
        "generated_at": generated.isoformat(timespec="seconds"),
        "staleness_as_of": now.isoformat(timespec="seconds"),
        "target_environment": label or env.get("ENVIRONMENT") or "unspecified",
        "environment": {
            name: probe.get("access") for name, probe in live.items() if name != "recorded"
        },
        "environment_detail": {
            name: probe.get("detail")
            for name, probe in live.items()
            if name != "recorded" and probe.get("detail")
        },
        "recorded_evidence": {
            "source": (recorded or {}).get("source"),
            "api_paths": len((recorded or {}).get("api") or {}),
            "pages": len((recorded or {}).get("pages") or {}),
        },
        "static": {
            "schedules": inventory["schedules"],
            "sources": inventory["sources"],
            "expected_tables": sorted(expected),
            "expected_views": parse_views(sql),
            "backend_routes": len(set(routes)) if routes is not None else UNKNOWN_ENV,
            "frontend_calls_without_backend_route": frontend_only_paths(front, routes)
            if routes is not None and front is not None
            else UNKNOWN_ENV,
        },
        "live": live,
        "datasets": results,
    }


def _cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Mapping[str, Any]) -> str:
    datasets = report["datasets"]
    env = report["environment"]
    live = report["live"]
    out: list[str] = []
    add = out.append
    add("# Data Source Health — Live Data Census")
    add("")
    add(
        "> Generated by `holocron/holocron/platform/census.py` (`just census`). Do not edit by hand; "
        "re-run the census. JSON twin: [`DATA_SOURCE_HEALTH.json`](DATA_SOURCE_HEALTH.json)."
    )
    add("")
    add(
        f"- Generated at: `{report['generated_at']}` (census v{report['census_version']}); target environment: `{report['target_environment']}`; staleness measured as of `{report['staleness_as_of']}`"
    )
    add(
        "- Method: read-only probes only — ClickHouse `system.*` + `SELECT`, object-storage list/get of manifests, "
        "Dagster `runs`/`instigators` `SELECT`, app Postgres `SELECT count/max`, API `GET`. Static facts come from "
        "the repository (Dagster definitions, `clickhouse_schema.sql`, backend source, FastAPI route table, frontend API wrappers)."
    )
    add(
        "- `classification` uses live evidence only; `static` is what the code proves regardless of live state. "
        f"`{UNKNOWN_ENV}` means the probe had no access — nothing is inferred."
    )
    add("")
    add("## Environment access for this run")
    add("")
    add("| Probe | Access | Detail |")
    add("|---|---|---|")
    for name, access in env.items():
        add(f"| {name} | {_cell(access)} | {_cell(report['environment_detail'].get(name))} |")
    rec = report["recorded_evidence"]
    if rec["source"]:
        add(
            f"| recorded evidence | `{rec['source']}` | {rec['api_paths']} API paths, {rec['pages']} browser pages "
            "recorded against the same environment; cited as `recorded (...)`, not probed by this run |"
        )
    add("")
    add("## Summary")
    add("")
    add(
        "| Dataset | Pipelines | Product | Producer | Classification (live) | Static | First break | Rows (primary) | Latest record |"
    )
    add("|---|---|---|---|---|---|---|---|---|")
    for d in datasets:
        primary = (
            (live["clickhouse"].get("objects") or {}).get(d["tables"][0], {}) if d["tables"] else {}
        )
        add(
            f"| `{d['id']}` | {_cell(', '.join(d['pipelines']))} | {'yes' if d['product_facing'] else 'no'} | "
            f"{_cell(d['producer'])} | **{d['classification']}** | {_cell(d['static_classification'])} | "
            f"{_cell(d['first_break_stage'])} | {_cell(primary.get('row_count'))} | {_cell(primary.get('latest_record'))} |"
        )
    add("")
    add("## Per-dataset trace")
    add("")
    add("`SOURCE → EXTRACTION → RAW → BRONZE → PUBLICATION → SERVING → BACKEND → API → FRONTEND`")
    for d in datasets:
        add("")
        add(f"### `{d['id']}` — {d['name']}")
        add("")
        add(f"- Provider: {d['provider']}; producer: {d['producer']}; cadence: {d['cadence']}")
        add(f"- Jobs: {_cell(', '.join(d['jobs']))}; raw: {_cell(d['raw_location'])}")
        add(f"- Tables: {_cell(', '.join(d['tables']))}; serving: {_cell(', '.join(d['serving']))}")
        add(f"- Backend: {_cell(', '.join(d['backend_consumers']))}")
        add(f"- API: {_cell(', '.join(d['api']))}; frontend: {_cell(', '.join(d['frontend']))}")
        for note in d["notes"]:
            add(f"- Note: {note}")
        add("")
        add("| Stage | Status | Evidence |")
        add("|---|---|---|")
        for stage in STAGES:
            info = d["stages"][stage]
            add(
                f"| {stage} | {info['status']}{' (' + info['classification'] + ')' if info.get('classification') else ''} | {_cell('; '.join(info['evidence']))} |"
            )
        primary = (
            (live["clickhouse"].get("objects") or {}).get(d["tables"][0], {}) if d["tables"] else {}
        )
        for label, key in (
            ("Event coverage", "event_coverage"),
            ("Duplicate keys", "duplicate_keys"),
            ("Empty/default %", "empty_or_default_pct"),
        ):
            if primary.get(key):
                add("")
                add(f"{label}: `{json.dumps(primary[key], default=str)}`")

    def section(title: str, rows: list[str]) -> None:
        add("")
        add(f"### {title}")
        add("")
        out.extend(rows or ["- None identified in this run."])

    def why(d: Mapping[str, Any], stage: str) -> str:
        """Stage classification plus only the evidence that shows the problem."""
        info = d["stages"][stage]
        evidence = info.get("problems") or [e for e in info["evidence"] if UNKNOWN_ENV not in e]
        return f"{info.get('classification', info['status'])} at {stage}: " + "; ".join(evidence)

    def downstream_ok(d: Mapping[str, Any]) -> bool:
        return all(d["stages"][s]["status"] == "ok" for s in ("serving", "api", "frontend"))

    add("")
    add("## Findings")
    product = [d for d in datasets if d["product_facing"]]
    available = [d for d in product if d["classification"] == "AVAILABLE"]
    section(
        "A. Working end-to-end sources",
        [
            f"- `{d['id']}` — serving, API and frontend all observed ok"
            + (f"; static: {d['static_classification']}" if d["static_classification"] else "")
            for d in available
            if downstream_ok(d) and not d["first_break_stage"]
        ]
        + [
            f"- `{d['id']}` — data available through serving; not verified end-to-end "
            f"(api: {d['stages']['api']['status']}, frontend: {d['stages']['frontend']['status']})"
            + (f"; static: {d['static_classification']}" if d["static_classification"] else "")
            for d in available
            if not (downstream_ok(d) and not d["first_break_stage"])
        ],
    )
    section(
        "B. Pipeline/data-quality problems",
        [
            f"- `{d['id']}` — {why(d, d['first_break_stage'])}"
            for d in datasets
            if d["first_break_stage"] in ("source", "extraction", "raw", "bronze")
            and d["static_classification"] != "MISSING"
        ],
    )
    section(
        "C. Downstream problems",
        [
            f"- `{d['id']}` — {why(d, s)}"
            for d in datasets
            for s in ("publication", "serving", "backend", "api", "frontend")
            if d["stages"][s]["status"] in ("broken", "partial", "stale")
        ],
    )
    section(
        "D. Missing producers",
        [
            f"- `{d['id']}` — {b['evidence']}"
            for d in datasets
            for b in d["static_breaks"]
            if b["classification"] == "MISSING"
        ]
        + [
            f"- Live object with no known producer: `{o}`"
            for o in live["clickhouse"].get("unowned_objects", [])
        ],
    )
    section(
        "E. Unverifiable sources",
        [
            f"- Probe `{name}` — {access}: {report['environment_detail'].get(name, '')}"
            for name, access in env.items()
            if access != "ok"
        ]
        + [
            f"- `{d['id']}` — no live evidence; unknown stages: "
            + ", ".join(s for s in STAGES if d["stages"][s]["status"] == "unknown")
            for d in datasets
            if d["classification"] == "UNKNOWN"
        ]
        + [
            f"- `{d['id']}` — {', '.join(s for s in ('source', 'extraction', 'raw') if d['stages'][s]['status'] == 'unknown')} "
            f"unverifiable ({d['producer']})"
            for d in datasets
            if d["classification"] != "UNKNOWN"
            and any(d["stages"][s]["status"] == "unknown" for s in ("source", "extraction", "raw"))
        ],
    )
    fe_only = report["static"]["frontend_calls_without_backend_route"]
    section(
        "F. Broken frontend/backend paths",
        [
            f"- `{d['id']}` ({', '.join(d['frontend'])}) — {why(d, s)}"
            for d in product
            for s in ("serving", "backend", "api", "frontend")
            if d["stages"][s]["status"] in ("broken", "partial")
        ]
        + (
            [f"- Frontend calls with no backend route: {', '.join(f'`{p}`' for p in fe_only)}"]
            if isinstance(fe_only, list) and fe_only
            else []
        ),
    )
    ordered = sorted(
        (d for d in datasets if d["first_break_stage"]),
        key=lambda d: (STAGES.index(d["first_break_stage"]), d["id"]),
    )
    fixes = []
    if any(v != "ok" for v in env.values()):
        fixes.append(
            "0. **Access** — provide read-only credentials for: "
            + ", ".join(k for k, v in env.items() if v != "ok")
            + " and re-run the census; every UNKNOWN below depends on it."
        )
    fixes += [
        f"{i}. **{d['first_break_stage']}** — `{d['id']}`"
        + (" (product)" if d["product_facing"] else " (not product-facing)")
        + f": {why(d, d['first_break_stage'])}"
        for i, d in enumerate(ordered, start=1)
    ]
    section("G. Highest-impact fixes (ordered by first broken stage in the chain)", fixes)
    measurements = []
    ch = live["clickhouse"]
    if ch.get("access") == "ok":
        measurements.append(
            f"- ClickHouse: {ch['database_objects']} objects, {ch['database_bytes']} bytes on disk; census probe time {ch['probe_seconds']}s"
        )
        for d in datasets:
            for t in d["tables"]:
                info = ch["objects"].get(t, {})
                if info.get("exists"):
                    measurements.append(
                        f"- `{t}`: {info.get('row_count')} rows, {info.get('total_bytes')} bytes"
                    )
    s3 = live["object_storage"]
    if s3.get("access") == "ok":
        for source, entry in s3["sources"].items():
            m = entry.get("latest_manifest")
            if m:
                measurements.append(
                    f"- raw `{source}` latest run: {m['files']} files, {m['rows']} rows, {m['bytes']} bytes ({entry['manifest_count']} manifests total)"
                )
    dg = live["dagster"]
    if dg.get("access") == "ok":
        for job, info in sorted(dg["jobs"].items()):
            last = info.get("last_success") or {}
            measurements.append(
                f"- job `{job}`: {info['runs']} runs, {info['failures']} failures, last success runtime {last.get('runtime_s')}s"
            )
    api = live["api"]
    for path, r in (api.get("endpoints") or {}).items() if api.get("access") == "ok" else ():
        measurements.append(
            f"- `GET {path}`: status {r.get('status')}, {r.get('bytes')} bytes, {r.get('latency_ms')}ms"
        )
    recorded = live.get("recorded") or {}
    for path, r in sorted((recorded.get("api") or {}).items()):
        measurements.append(
            f"- recorded ({recorded['source']}) `GET {path}`: status {r['status']}, {r['seconds']}s"
        )
    if not measurements:
        measurements.append(
            f"- {UNKNOWN_ENV}: no live runtime, volume, storage or network measurements were collectable in this run. CPU, peak memory and network per run are not recorded by any existing tooling (Dagster run stats hold only wall-clock)."
        )
    section("H. Production deployment measurements", measurements)
    add("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only live data census (writes DATA_SOURCE_HEALTH.{json,md})."
    )
    parser.add_argument(
        "--env-file", action="append", default=[], help="Env file(s); later files override earlier."
    )
    parser.add_argument(
        "--api-base", default="", help="API base URL for GET probes (or CENSUS_API_BASE)."
    )
    parser.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "docs" / "data-pipelines"),
        help="Output directory; '-' prints JSON to stdout.",
    )
    parser.add_argument("--static-only", action="store_true", help="Skip all live probes.")
    parser.add_argument(
        "--as-of",
        default="",
        help="ISO timestamp staleness is measured against (use the snapshot time for a restored snapshot).",
    )
    parser.add_argument(
        "--environment-label",
        default="",
        help="Human label for the probed environment, e.g. 'handoff snapshot 2026-09-21'.",
    )
    parser.add_argument(
        "--recorded-evidence",
        default="",
        help="Recorded API/browser checks of the same environment (handoff verification-report.json format).",
    )
    args = parser.parse_args(argv)

    env: dict[str, str] = dict(os.environ)
    for path in args.env_file:
        env.update(_read_env_file(Path(path)))
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    if as_of and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    report = build_report(
        env,
        args.api_base or env.get("CENSUS_API_BASE") or env.get("PUBLIC_API_BASE_URL", ""),
        live_enabled=not args.static_only,
        as_of=as_of,
        label=args.environment_label,
        recorded=load_recorded_evidence(Path(args.recorded_evidence))
        if args.recorded_evidence
        else None,
    )
    if args.out_dir == "-":
        json.dump(report, sys.stdout, indent=2, default=str)
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "DATA_SOURCE_HEALTH.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "DATA_SOURCE_HEALTH.md").write_text(render_markdown(report), encoding="utf-8")
    print(
        f"wrote {out_dir / 'DATA_SOURCE_HEALTH.json'} and .md; environment: {report['environment']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
