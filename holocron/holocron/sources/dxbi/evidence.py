"""Load DXBI rental and sales evidence runs from Holocron.

Replaces the developer-Mac chain (rsync -> scripts/sync_dxbi_daily_runs.sh -> .loaded marker
files, F01). The DXBI report scraper writes completed runs to
``<DXBI_EVIDENCE_DIR>/{rentals,sales}/run=<date>/`` with a ``_SUCCESS`` marker. For each run
not yet in ``dxbi_evidence_load_ledger`` this module:

1. archives the raw files to object storage with a sha256 manifest, so loads are replayable;
2. runs the existing, tested loaders (``scripts/load_dxbi_rental_events.py``,
   ``scripts/load_dxbi_sales_units.py``) with ClickHouse credentials passed by environment;
3. records the loader accounting (rows loaded / skipped / rejected) in the ledger, so rows
   dropped at load stay measurable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from holocron.contracts import ClickHouseQuery, ClickHouseQueryInserter
from holocron.platform.clickhouse_schema import ensure_schema

REPORTS = ("rentals", "sales")
LEDGER_TABLE = "dxbi_evidence_load_ledger"
RAW_PREFIX = "raw/source=dxbi_evidence"
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
_LOADERS = {"rentals": "load_dxbi_rental_events.py", "sales": "load_dxbi_sales_units.py"}
_LEDGER_COLUMNS = (
    "batch_id", "report", "run_name", "files", "raw_bytes", "manifest_key", "rows_loaded",
    "rows_skipped", "rows_rejected", "details_json", "status", "error", "loaded_at",
)  # fmt: skip


class ObjectUploader(Protocol):
    def upload_file(
        self, *, path: str | Path, key: str, content_type: str | None = None
    ) -> None: ...

    def put_json(self, *, key: str, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class EvidenceRun:
    report: str
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class LoaderResult:
    rows_loaded: int
    rows_skipped: int
    rows_rejected: int
    details: dict[str, Any]


def discover_completed_runs(root: Path) -> list[EvidenceRun]:
    """Runs the scraper has finished (``_SUCCESS`` present), oldest first."""
    runs: list[EvidenceRun] = []
    for report in REPORTS:
        base = root / report
        if not base.is_dir():
            continue
        for run_dir in sorted(base.glob("run=*")):
            if run_dir.is_dir() and (run_dir / "_SUCCESS").is_file():
                runs.append(EvidenceRun(report=report, name=run_dir.name, path=run_dir))
    return runs


def loaded_runs(clickhouse: ClickHouseQuery) -> set[tuple[str, str]]:
    rows = clickhouse.query(
        f"SELECT report, run_name FROM {LEDGER_TABLE} WHERE status = 'success'"
    ).result_rows
    return {(str(report), str(run_name)) for report, run_name in rows}


def archive_run(object_storage: ObjectUploader, run: EvidenceRun) -> dict[str, Any]:
    """Upload every file of the run and a manifest; returns the manifest summary."""
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in run.path.rglob("*") if p.is_file()):
        relative = path.relative_to(run.path).as_posix()
        key = f"{RAW_PREFIX}/report={run.report}/{run.name}/{relative}"
        object_storage.upload_file(path=path, key=key)
        files.append(
            {
                "path": relative,
                "s3_key": key,
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest_key = f"{RAW_PREFIX}/manifests/{run.report}-{run.name}.json"
    object_storage.put_json(
        key=manifest_key,
        payload={"report": run.report, "run": run.name, "files": files},
    )
    return {
        "manifest_key": manifest_key,
        "files": len(files),
        "raw_bytes": sum(file["size_bytes"] for file in files),
    }


def parse_loader_output(report: str, output: str) -> LoaderResult:
    """Read the loaders' own accounting lines.

    Rentals log ``Complete {json totals}``; sales log
    ``Sales load complete loaded=N skipped=N errors=N`` (skipped = hidden unit, leak L1).
    """
    if report == "rentals":
        match = re.search(r"Complete (\{.*\})", output)
        totals = json.loads(match.group(1)) if match else {}
        return LoaderResult(
            rows_loaded=int(totals.get("loaded_contracts", 0)),
            rows_skipped=int(totals.get("out_of_scope_rows", 0))
            + int(totals.get("profile_overlap_rows", 0)),
            rows_rejected=int(totals.get("quarantined_rows", 0)),
            details=totals,
        )
    match = re.search(r"Sales load complete loaded=(\d+) skipped=(\d+) errors=(\d+)", output)
    loaded, skipped, errors = (int(value) for value in match.groups()) if match else (0, 0, 0)
    return LoaderResult(
        rows_loaded=loaded,
        rows_skipped=skipped,
        rows_rejected=errors,
        details={"loaded": loaded, "skipped_hidden_or_no_unit": skipped, "errors": errors},
    )


def run_loader(
    report: str,
    runs: Sequence[EvidenceRun],
    *,
    env: Mapping[str, str],
    python: str = sys.executable,
) -> LoaderResult:
    command = [python, str(_SCRIPTS_DIR / _LOADERS[report])]
    if report == "rentals":
        command += ["--target", "active", "--run-id", f"holocron-{_stamp()}"]
    command += [str(run.path) for run in runs]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository scripts
        command, env=dict(env), capture_output=True, text=True, check=False
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"{report} loader failed ({result.returncode}): {output[-2000:]}")
    return parse_loader_output(report, output)


def loader_environment(clickhouse_settings: Mapping[str, Any]) -> dict[str, str]:
    """Credentials go through the environment, never the command line (visible in `ps`)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("CLICKHOUSE_")}
    env.update({key: str(value) for key, value in clickhouse_settings.items()})
    return env


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ledger_rows(
    batch_id: str,
    runs: Sequence[EvidenceRun],
    archives: Mapping[str, dict[str, Any]],
    result: LoaderResult | None,
    error: str,
) -> list[tuple[Any, ...]]:
    now = datetime.now(UTC)
    rows = []
    for index, run in enumerate(runs):
        archive = archives[run.path.as_posix()]
        # Loader totals are per batch; they are attached once, to the batch's first run.
        first = index == 0 and result is not None
        rows.append(
            (
                batch_id,
                run.report,
                run.name,
                archive["files"],
                archive["raw_bytes"],
                archive["manifest_key"],
                result.rows_loaded if first and result else 0,
                result.rows_skipped if first and result else 0,
                result.rows_rejected if first and result else 0,
                json.dumps(result.details if first and result else {}, default=str),
                "failed" if error else "success",
                error[:2000],
                now,
            )
        )
    return rows


def load_evidence(
    *,
    root: Path,
    clickhouse: ClickHouseQueryInserter,
    object_storage: ObjectUploader,
    loader_env: Mapping[str, str],
    python: str = sys.executable,
) -> dict[str, Any]:
    ensure_schema(clickhouse=clickhouse)
    done = loaded_runs(clickhouse)
    pending = [run for run in discover_completed_runs(root) if (run.report, run.name) not in done]
    summary: dict[str, Any] = {"pending_runs": len(pending)}
    for report in REPORTS:
        runs = [run for run in pending if run.report == report]
        if not runs:
            continue
        batch_id = f"{report}-{_stamp()}"
        archives = {run.path.as_posix(): archive_run(object_storage, run) for run in runs}
        try:
            result = run_loader(report, runs, env=loader_env, python=python)
        except RuntimeError as exc:
            clickhouse.insert_rows(
                table=LEDGER_TABLE,
                rows=_ledger_rows(batch_id, runs, archives, None, str(exc)),
                column_names=_LEDGER_COLUMNS,
            )
            raise
        clickhouse.insert_rows(
            table=LEDGER_TABLE,
            rows=_ledger_rows(batch_id, runs, archives, result, ""),
            column_names=_LEDGER_COLUMNS,
        )
        summary[report] = {
            "runs": [run.name for run in runs],
            "rows_loaded": result.rows_loaded,
            "rows_skipped": result.rows_skipped,
            "rows_rejected": result.rows_rejected,
            "raw_bytes": sum(archive["raw_bytes"] for archive in archives.values()),
        }
    return summary
