"""The app's sales and rentals views must never be pointed at an unpublished market layer."""

from __future__ import annotations

import pytest

from scripts.clickhouse import create_compatibility_views as views


class _Result:
    def __init__(self, value: int) -> None:
        self.result_rows = [[value]]


class _Warehouse:
    """Answers the guard's three questions per table: tables exist, releases, served rows."""

    def __init__(self, *, exists: int = 2, published: int = 1, served: int = 10) -> None:
        self.answers = {"system.tables": exists, "market_release_ledger": published}
        self.served = served
        self.commands: list[str] = []

    def query(self, sql: str, parameters: dict | None = None) -> _Result:
        if "system.tables" in sql:
            return _Result(self.answers["system.tables"])
        if "market_release_ledger" in sql:
            return _Result(self.answers["market_release_ledger"])
        return _Result(self.served)

    def command(self, sql: str) -> None:
        self.commands.append(sql)


def test_a_published_non_empty_market_layer_is_accepted() -> None:
    views.require_published_market_layer(_Warehouse())


@pytest.mark.parametrize(
    ("warehouse", "reason"),
    [
        # Fresh warehouse: Holocron never created the market tables.
        (_Warehouse(exists=1), "does not exist"),
        # Tables exist (schema applied) but the rebuild never published a release.
        (_Warehouse(published=0, served=0), "0 published release"),
        # A ledger entry without served rows would still empty every endpoint.
        (_Warehouse(served=0), "0 served rows"),
    ],
)
def test_views_are_left_unchanged_when_the_market_layer_was_never_published(
    monkeypatch, warehouse: _Warehouse, reason: str
) -> None:
    monkeypatch.setattr(views, "parse_args", lambda: type("A", (), {"dry_run": False})())
    monkeypatch.setattr(views, "get_client", lambda: warehouse)
    with pytest.raises(views.MarketLayerNotPublished, match=reason):
        views.main()
    # The refusal comes before any DDL: the previous views keep serving.
    assert warehouse.commands == []
