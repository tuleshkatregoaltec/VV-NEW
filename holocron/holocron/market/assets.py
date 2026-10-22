from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetChecksDefinition,
    MaterializeResult,
    asset,
    asset_check,
)

from holocron.market.checks import CHECKS, MarketCheck, outcome
from holocron.market.release import (
    materialize_market_rental_events,
    materialize_market_sale_events,
)
from holocron.platform.resources import ClickHouseResource

_MARKET_GROUP = "market"
# Both assets also take the ClickHouse market_release_lock; the pool keeps Dagster from
# starting a second publication that would only fail on that lock (set its limit to 1).
_MARKET_POOL = "market_release"


@asset(
    name="market_sale_events",
    group_name=_MARKET_GROUP,
    pool=_MARKET_POOL,
    deps=["dld_od_transactions_bronze", "dxbi_evidence_bronze"],
    description=(
        "Canonical sales: de-duplicated DLD transactions enriched with DXBI unit identity, plus "
        "DXBI sales DLD does not hold; every row labelled with its match status."
    ),
)
def market_sale_events_asset(context, clickhouse: ClickHouseResource) -> MaterializeResult:
    counts = materialize_market_sale_events(clickhouse=clickhouse.client(), owner=context.run_id)
    context.log.info("market_sale_events materialised: %s", counts)
    return MaterializeResult(metadata={"stage": "market", "table": "market_sale_events", **counts})


@asset(
    name="market_rental_events",
    group_name=_MARKET_GROUP,
    pool=_MARKET_POOL,
    # Rental pairing is location-keyed through the area link learned from matched sales.
    deps=["dld_od_rents_bronze", "dxbi_evidence_bronze", "market_sale_events"],
    description=(
        "Canonical rentals: de-duplicated DLD Ejari rows enriched with DXBI unit identity, plus "
        "DXBI contracts DLD does not hold; every row labelled with its match status."
    ),
)
def market_rental_events_asset(context, clickhouse: ClickHouseResource) -> MaterializeResult:
    counts = materialize_market_rental_events(clickhouse=clickhouse.client(), owner=context.run_id)
    context.log.info("market_rental_events materialised: %s", counts)
    return MaterializeResult(
        metadata={"stage": "market", "table": "market_rental_events", **counts}
    )


def _asset_check(check: MarketCheck) -> AssetChecksDefinition:
    @asset_check(asset=check.asset, name=check.name, description=check.description)
    def _run(clickhouse: ClickHouseResource) -> AssetCheckResult:
        status, value = outcome(clickhouse.client(), check)
        return AssetCheckResult(
            passed=status != "failed",
            severity=AssetCheckSeverity.ERROR,
            metadata={
                "status": status,
                "value": value,
                "op": check.op,
                "threshold": check.threshold,
            },
        )

    return _run


MARKET_ASSET_CHECKS = [_asset_check(check) for check in CHECKS]
