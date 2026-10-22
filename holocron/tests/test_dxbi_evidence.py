from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from holocron.sources.dxbi import evidence


def _run(root: Path, report: str, name: str, *, complete: bool = True) -> Path:
    run_dir = root / report / name
    (run_dir / "shards").mkdir(parents=True)
    (run_dir / "shards" / "part.jsonl").write_text('{"a": 1}\n')
    if complete:
        (run_dir / "_SUCCESS").write_text("")
    return run_dir


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.json: dict[str, dict[str, Any]] = {}

    def upload_file(self, *, path: str | Path, key: str, content_type: str | None = None) -> None:
        self.files[key] = Path(path).read_bytes()

    def put_json(self, *, key: str, payload: dict[str, Any]) -> None:
        self.json[key] = payload


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _ClickHouse:
    def __init__(self, loaded: list[tuple[str, str]]) -> None:
        self.loaded = loaded
        self.inserted: list[tuple[Any, ...]] = []

    def command(self, sql: str) -> None:
        return None

    def query(self, sql: str) -> _Result:
        return _Result(self.loaded)

    def insert_rows(
        self, *, table: str, rows: Sequence[tuple[Any, ...]], column_names: Sequence[str]
    ) -> None:
        self.inserted.extend(rows)


def test_only_completed_runs_are_discovered(tmp_path: Path) -> None:
    # A run without _SUCCESS is still being written; loading it would publish partial data.
    _run(tmp_path, "rentals", "run=2026-09-20")
    _run(tmp_path, "rentals", "run=2026-09-21", complete=False)
    _run(tmp_path, "sales", "run=2026-09-20")
    runs = evidence.discover_completed_runs(tmp_path)
    assert [(r.report, r.name) for r in runs] == [
        ("rentals", "run=2026-09-20"),
        ("sales", "run=2026-09-20"),
    ]


def test_raw_files_are_archived_with_checksums_before_loading(tmp_path: Path) -> None:
    # Raw evidence used to live only on the scraper host and a Mac (L13); the archive makes
    # every load replayable and verifiable.
    run_dir = _run(tmp_path, "sales", "run=2026-09-20")
    storage = _Storage()
    summary = evidence.archive_run(
        storage, evidence.EvidenceRun("sales", "run=2026-09-20", run_dir)
    )
    manifest = storage.json[summary["manifest_key"]]
    shard = next(f for f in manifest["files"] if f["path"] == "shards/part.jsonl")
    assert storage.files[shard["s3_key"]] == b'{"a": 1}\n'
    assert shard["sha256"] == hashlib.sha256(b'{"a": 1}\n').hexdigest()
    assert summary["files"] == 2  # shard + _SUCCESS marker


def test_loader_accounting_is_parsed_so_dropped_rows_stay_measurable() -> None:
    sales = evidence.parse_loader_output(
        "sales", "INFO Sales load complete loaded=120 skipped=15 errors=0"
    )
    assert (sales.rows_loaded, sales.rows_skipped, sales.rows_rejected) == (120, 15, 0)
    rentals = evidence.parse_loader_output(
        "rentals",
        'INFO Complete {"loaded_contracts": 90, "out_of_scope_rows": 4, '
        '"profile_overlap_rows": 6, "quarantined_rows": 1}',
    )
    assert (rentals.rows_loaded, rentals.rows_skipped, rentals.rows_rejected) == (90, 10, 1)


def test_credentials_reach_loaders_by_environment_not_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Command-line arguments are visible to every user via `ps`; the Mac script leaked the
    # ClickHouse password that way.
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"], seen["env"] = command, kwargs["env"]
        return subprocess.CompletedProcess(
            command, 0, "Sales load complete loaded=1 skipped=0 errors=0", ""
        )

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    env = evidence.loader_environment({"CLICKHOUSE_PASSWORD": "s3cret", "CLICKHOUSE_HOST": "h"})
    run = evidence.EvidenceRun("sales", "run=2026-09-20", _run(tmp_path, "sales", "run=2026-09-20"))
    evidence.run_loader("sales", [run], env=env)
    assert "s3cret" not in " ".join(seen["command"])
    assert seen["env"]["CLICKHOUSE_PASSWORD"] == "s3cret"


def test_runs_already_in_the_ledger_are_not_reloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, "rentals", "run=2026-09-20")
    _run(tmp_path, "rentals", "run=2026-09-21")
    loaded_batches: list[list[str]] = []

    def fake_loader(
        report: str, runs: list[evidence.EvidenceRun], **kwargs: Any
    ) -> evidence.LoaderResult:
        loaded_batches.append([run.name for run in runs])
        return evidence.LoaderResult(5, 0, 0, {})

    monkeypatch.setattr(evidence, "run_loader", fake_loader)
    monkeypatch.setattr(evidence, "ensure_schema", lambda **kwargs: None)
    clickhouse = _ClickHouse(loaded=[("rentals", "run=2026-09-20")])
    evidence.load_evidence(
        root=tmp_path,
        clickhouse=clickhouse,
        object_storage=_Storage(),
        loader_env={},
    )
    assert loaded_batches == [["run=2026-09-21"]]
    assert [(row[1], row[2], row[10]) for row in clickhouse.inserted] == [
        ("rentals", "run=2026-09-21", "success")
    ]
