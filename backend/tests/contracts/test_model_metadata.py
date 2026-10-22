import json
from pathlib import Path

from conftest import get_app
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel


def table_contract():
    get_app()  # Register the exact current runtime models, including incidental imports.
    dialect = postgresql.dialect()
    return {
        table.name: {
            "ddl": str(CreateTable(table).compile(dialect=dialect)),
            "indexes": sorted(
                str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes
            ),
        }
        for table in sorted(SQLModel.metadata.tables.values(), key=lambda item: item.name)
    }


def test_postgres_models_preserve_schema():
    expected = json.loads((Path(__file__).parent / "fixtures/model_metadata.json").read_text())
    assert table_contract() == expected


def test_registry_registers_every_table_without_loading_application_routes():
    import subprocess
    import sys

    code = """
import json
from sqlmodel import SQLModel
import app.postgres.models as registry
registered = sorted(getattr(registry, name).__tablename__ for name in registry.__all__)
print(json.dumps({'registered': registered, 'metadata': sorted(SQLModel.metadata.tables)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    actual = json.loads(result.stdout)
    expected = sorted(
        json.loads((Path(__file__).parent / "fixtures/model_metadata.json").read_text())
    )
    assert actual["registered"] == expected
    assert actual["metadata"] == expected
