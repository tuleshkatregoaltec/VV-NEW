from holocron.platform.clickhouse_schema import iter_sql_statements
from pathlib import Path


def test_schema_parser_ignores_semicolons_in_comments_and_string_literals() -> None:
    sql = """
    -- This comment contains a semicolon; it is not a query.
    CREATE TABLE example (value String DEFAULT 'one;two');
    -- Another comment.
    CREATE VIEW example_view AS SELECT 'it''s;valid' AS value;
    """

    statements = iter_sql_statements(sql)

    assert statements == [
        "CREATE TABLE example (value String DEFAULT 'one;two')",
        "CREATE VIEW example_view AS SELECT 'it''s;valid' AS value",
    ]


def test_rental_v2_schema_uses_contract_identity_and_exposes_health() -> None:
    schema = (
        Path(__file__).parents[1] / "holocron" / "lib" / "sql" / "clickhouse_schema.sql"
    ).read_text()

    # The v2 cutover swapped v2 into the active names; a fresh warehouse built from this file
    # must get that schema under the names the backend and loaders use, and no stale copies.
    assert "CREATE TABLE IF NOT EXISTS dxbi_rental_events_v2" not in schema
    active_events = schema.split("CREATE TABLE IF NOT EXISTS dxbi_rental_events (", 1)[1]
    assert active_events.split(";", 1)[0].count("contract_key       String,") == 1
    assert "event_key          String ALIAS contract_key" in schema
    assert "ORDER BY (lease_start, contract_key)" in schema
    assert "CREATE TABLE IF NOT EXISTS dxbi_rental_run_reconciliation" in schema
    assert "CREATE OR REPLACE VIEW dxbi_rental_ingestion_health" in schema


def test_every_view_reads_only_objects_defined_earlier_in_the_schema() -> None:
    # ensure_schema runs this file top to bottom on every load. A view over a table nothing
    # creates (silver_reelly_projects did this) makes every Holocron load fail on a fresh
    # warehouse, which is exactly what production will be.
    import re

    schema = (
        Path(__file__).parents[1] / "holocron" / "lib" / "sql" / "clickhouse_schema.sql"
    ).read_text()
    defined: set[str] = set()
    for statement in iter_sql_statements(schema):
        created = re.match(
            r"CREATE (?:TABLE IF NOT EXISTS|OR REPLACE VIEW|VIEW IF NOT EXISTS) (\w+)", statement
        )
        if statement.startswith("CREATE OR REPLACE VIEW"):
            local = set(re.findall(r"(\w+) AS \(", statement))
            read = set(re.findall(r"\b(?:FROM|JOIN)\s+(?:\w+\.)?([a-z]\w*)", statement))
            missing = read - local - defined
            assert not missing, f"{created.group(1) if created else statement[:40]}: {missing}"
        if created:
            defined.add(created.group(1))
