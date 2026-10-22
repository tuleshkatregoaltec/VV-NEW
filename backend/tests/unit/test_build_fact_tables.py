from typing import Any

import pytest

from scripts.clickhouse.build_fact_tables import _materialize_fact


class FakeClient:
    def __init__(self, engines: dict[str, str]) -> None:
        self.engines = engines
        self.statements: list[str] = []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple[str, str]]:
        if sql.startswith("SELECT name, engine"):
            wanted = set((params or {}).values())
            return [(name, engine) for name, engine in self.engines.items() if name in wanted]
        self.statements.append(sql)
        return []


def _build(client: FakeClient) -> None:
    _materialize_fact(
        client,
        fact="ch_project_fact",
        serving="ch_project_fact__serving",
        backup="ch_project_fact__source_view",
        order_by="project_id",
    )


def test_first_build_materializes_the_view_and_keeps_it_as_the_source() -> None:
    client = FakeClient({"ch_project_fact": "View"})
    _build(client)
    assert "AS SELECT * FROM ch_project_fact" in client.statements[1]
    assert client.statements[-1] == (
        "RENAME TABLE ch_project_fact TO ch_project_fact__source_view, "
        "ch_project_fact__serving TO ch_project_fact"
    )


def test_rebuild_reads_the_source_view_not_the_stale_table() -> None:
    # A second run used to drop the source view and copy the materialized table onto itself,
    # freezing project facts at their first build (F13).
    client = FakeClient({"ch_project_fact": "MergeTree", "ch_project_fact__source_view": "View"})
    _build(client)
    assert "AS SELECT * FROM ch_project_fact__source_view" in client.statements[1]
    assert "EXCHANGE TABLES ch_project_fact__serving AND ch_project_fact" in client.statements
    assert not any(
        s.startswith("DROP TABLE IF EXISTS ch_project_fact__source_view") for s in client.statements
    )


def test_missing_source_view_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="source view"):
        _build(FakeClient({"ch_project_fact": "MergeTree"}))
