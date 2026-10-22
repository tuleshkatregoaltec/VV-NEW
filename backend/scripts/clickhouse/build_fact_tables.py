from __future__ import annotations

import argparse
import os
import sys
import time

from clickhouse_driver import Client

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_NATIVE_PORT", "9001"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "vitevue")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "vitevue_dev")
CLICKHOUSE_DATABASE = os.environ.get("CLICKHOUSE_DATABASE", "vitevue")
CLICKHOUSE_SECURE = os.environ.get("CLICKHOUSE_SECURE", "false").lower() in {"1", "true", "yes"}

PROJECT_FACT_SOURCE = "ch_project_fact"
AREA_FACT_SOURCE = "ch_area_fact"
PROJECT_FACT_SERVING = "ch_project_fact__serving"
AREA_FACT_SERVING = "ch_area_fact__serving"
PROJECT_FACT_BACKUP = "ch_project_fact__source_view"
AREA_FACT_BACKUP = "ch_area_fact__source_view"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local ClickHouse serving fact tables.")
    parser.add_argument("--clickhouse-host", default=CLICKHOUSE_HOST)
    parser.add_argument("--clickhouse-port", type=int, default=CLICKHOUSE_PORT)
    parser.add_argument("--clickhouse-user", default=CLICKHOUSE_USER)
    parser.add_argument("--clickhouse-password", default=CLICKHOUSE_PASSWORD)
    parser.add_argument("--clickhouse-database", default=CLICKHOUSE_DATABASE)
    parser.add_argument(
        "--clickhouse-secure", action=argparse.BooleanOptionalAction, default=CLICKHOUSE_SECURE
    )
    return parser.parse_args()


def get_client(args: argparse.Namespace) -> Client:
    return Client(
        host=args.clickhouse_host,
        port=args.clickhouse_port,
        user=args.clickhouse_user,
        password=args.clickhouse_password,
        database=args.clickhouse_database,
        secure=args.clickhouse_secure,
    )


def build_fact_tables(client: Client) -> None:
    """Materialize the canonical fact views without changing their response contract.

    The local snapshot contains fact *views*.  They are accurate but each Radar
    request expands them into scans across the raw sales and rental tables.  A
    previous builder created tables with a different schema, which made it
    unsafe to run against the current application.  This routine materializes
    the existing canonical views first, then swaps them atomically.
    """
    start = time.time()
    for fact, serving, backup, order_by in (
        (PROJECT_FACT_SOURCE, PROJECT_FACT_SERVING, PROJECT_FACT_BACKUP, "project_id"),
        (AREA_FACT_SOURCE, AREA_FACT_SERVING, AREA_FACT_BACKUP, "area_name_en"),
    ):
        _materialize_fact(client, fact=fact, serving=serving, backup=backup, order_by=order_by)

    project_count = client.execute(f"SELECT count() FROM {PROJECT_FACT_SOURCE}")[0][0]
    area_count = client.execute(f"SELECT count() FROM {AREA_FACT_SOURCE}")[0][0]
    print(
        f"Built ch_project_fact ({project_count:,} rows) and "
        f"ch_area_fact ({area_count:,} rows) in {time.time() - start:.2f}s"
    )


def _materialize_fact(
    client: Client, *, fact: str, serving: str, backup: str, order_by: str
) -> None:
    """Materialize one fact from its canonical view, whatever state the last run left.

    First build (or views just recreated): ``fact`` is the view; it moves to ``backup`` and
    the new table takes its name. Rebuild: ``fact`` is already a table, so read ``backup`` and
    swap the fresh table in. The old code dropped ``backup`` and re-copied the stale table,
    so a second run silently froze the facts (F13).
    """
    engines = dict(
        client.execute(
            "SELECT name, engine FROM system.tables "
            "WHERE database = currentDatabase() AND name IN (%(fact)s, %(backup)s)",
            {"fact": fact, "backup": backup},
        )
    )
    if engines.get(fact) == "View":
        source = fact
    elif backup in engines:
        source = backup
    else:
        raise RuntimeError(
            f"{fact} is materialized but its source view {backup} is missing; "
            "run scripts.clickhouse.create_compatibility_views first"
        )
    print(f"Materializing {fact} from {source}...")
    client.execute(f"DROP TABLE IF EXISTS {serving}")
    client.execute(
        f"CREATE TABLE {serving} ENGINE = MergeTree ORDER BY {order_by} AS SELECT * FROM {source}"
    )
    if source == fact:
        client.execute(f"DROP TABLE IF EXISTS {backup}")
        client.execute(f"RENAME TABLE {fact} TO {backup}, {serving} TO {fact}")
    else:
        client.execute(f"EXCHANGE TABLES {serving} AND {fact}")
        client.execute(f"DROP TABLE {serving}")


def main() -> int:
    build_fact_tables(get_client(parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
