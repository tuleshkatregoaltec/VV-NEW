from pathlib import Path

from dagster import MaterializeResult, MetadataValue, asset

from holocron.platform.resources import ClickHouseResource, ObjectStorageResource
from holocron.platform.settings import Settings
from holocron.sources.dxbi.evidence import load_evidence, loader_environment


@asset(
    name="dxbi_evidence_bronze",
    group_name="dxbinteract",
    description=(
        "DXBI rental and sales evidence (dxbi_rental_events, dxbi_rental_unit_candidates, "
        "dxbi_sales_unit_events, dxbi_rental_sale_matches): completed scraper runs from "
        "DXBI_EVIDENCE_DIR, archived to object storage, loaded, and accounted in "
        "dxbi_evidence_load_ledger."
    ),
)
def dxbi_evidence_bronze(
    context,
    clickhouse: ClickHouseResource,
    object_storage: ObjectStorageResource,
) -> MaterializeResult:
    settings = Settings()
    if not settings.dxbi_evidence_dir:
        raise ValueError("DXBI_EVIDENCE_DIR is not set; it must point at the scraper output root")
    summary = load_evidence(
        root=Path(settings.dxbi_evidence_dir),
        clickhouse=clickhouse.client(),
        object_storage=object_storage.client(),
        loader_env=loader_environment(
            {
                "CLICKHOUSE_HOST": settings.clickhouse_host,
                "CLICKHOUSE_PORT": settings.clickhouse_port,
                "CLICKHOUSE_USER": settings.clickhouse_user,
                "CLICKHOUSE_PASSWORD": settings.clickhouse_password,
                "CLICKHOUSE_DATABASE": settings.clickhouse_database,
            }
        ),
    )
    context.log.info("dxbi evidence load: %s", summary)
    return MaterializeResult(
        metadata={
            "stage": "bronze",
            "source": "dxbi_evidence",
            "summary": MetadataValue.json(summary),
        }
    )
