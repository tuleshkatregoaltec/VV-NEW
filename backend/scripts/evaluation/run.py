"""Execute canonical questions over loopback HTTP with real authentication and SSE."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from scripts.evaluation.dataset import load_benchmark
from scripts.evaluation.tracing import private_directory, private_write, scrub_secrets

ROOT = Path(__file__).resolve().parents[3]


async def execute_case(client, case, timeout, parameters=None):
    record = {
        "case_id": case.id,
        "question": case.bind(parameters),
        "parameters": parameters or {},
        "unbound_parameters": case.parameters if parameters is None else [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "events": [],
        "trace_ids": {},
        "completed": False,
    }
    started = time.monotonic()
    conversation_id = None
    try:
        response = await client.post(
            "/api/v1/chat/conversations", json={"title": f"Evaluation {case.id}"}
        )
        response.raise_for_status()
        conversation_id = response.json()["id"]
        record["conversation_id"] = conversation_id  # Private only; never remote metadata.
        record["trace_ids"]["create"] = response.headers.get("x-evaluation-trace-id")
        async with asyncio.timeout(timeout):
            async with client.stream(
                "POST",
                f"/api/v1/chat/conversations/{conversation_id}/messages/stream",
                json={"message": record["question"]},
            ) as response:
                record["http_status"] = response.status_code
                record["trace_ids"]["stream"] = response.headers.get("x-evaluation-trace-id")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        record["events"].append(json.loads(line[6:]))
        record["completed"] = bool(record["events"] and record["events"][-1]["type"] == "done")
        record["answer"] = "".join(
            event.get("content", "") for event in record["events"] if event["type"] == "content"
        )
        response = await client.get(f"/api/v1/chat/conversations/{conversation_id}/messages")
        response.raise_for_status()
        # Keep user/assistant content and usage; not database message IDs or timestamps.
        record["persisted_messages"] = [
            {"role": item["role"], "content": item["content"], "meta_data": item.get("meta_data")}
            for item in response.json()
        ]
        record["trace_ids"]["reload"] = response.headers.get("x-evaluation-trace-id")
    except Exception as exc:
        record["error_type"] = type(exc).__name__
    finally:
        # Preserve normal persistence. Only delete this run's conversation through the authorized API.
        if conversation_id is not None:
            try:
                response = await client.delete(f"/api/v1/chat/conversations/{conversation_id}")
                record["cleanup_status"] = response.status_code
                record["trace_ids"]["delete"] = response.headers.get("x-evaluation-trace-id")
            except Exception as exc:
                record["cleanup_error"] = type(exc).__name__
        record["latency_seconds"] = round(time.monotonic() - started, 3)
    return record


async def run_benchmark(
    directory=None,
    case_ids=None,
    timeout=180,
    data_context="local-mutable-warehouse",
    bindings_path=None,
):
    from app.config import settings

    if settings.ENVIRONMENT != "local":
        raise ValueError("Evaluation requires the local environment")
    if not settings.FIRST_SUPERUSER or not settings.FIRST_SUPERUSER_PASSWORD:
        raise ValueError("Configure local benchmark login credentials")
    auth_url = str(settings.AUTH_URL).rstrip("/")
    if httpx.URL(auth_url).host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Benchmark login must use local auth")
    benchmark = load_benchmark()
    cases = [case for case in benchmark.cases if case_ids is None or case.id in case_ids]
    if not cases or (case_ids and set(case_ids) != {case.id for case in cases}):
        raise ValueError("Unknown/empty case selection")
    bindings = json.loads(Path(bindings_path).read_text()) if bindings_path else {}
    if not isinstance(bindings, dict) or set(bindings) - {case.id for case in cases}:
        raise ValueError("Bindings must map selected case IDs to explicit parameter values")
    for case in cases:
        case.bind(bindings.get(case.id))  # Validate before login or provider calls.
    directory = (
        Path(directory)
        if directory
        else ROOT / ".evaluation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    if directory.exists():
        raise ValueError("Use a new evidence directory; existing runs are immutable")
    private_directory(directory)
    run_id = str(uuid4())
    manifest = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "dataset_version": benchmark.version,
        "dataset_sha256": benchmark.digest,
        "case_ids": [case.id for case in cases],
        "data_context": data_context,
        "remote_telemetry": False,
        "results": [],
    }
    private_write(directory / "run.json", manifest)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    env = {**os.environ, "VV_EVALUATION_DIRECTORY": str(directory.resolve())}
    log_fd = os.open(directory / "server.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(log_fd, "w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "scripts.evaluation.server:create_app",
                "--factory",
                "--fd",
                str(listener.fileno()),
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            cwd=ROOT / "backend",
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
        )
    listener.close()
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=timeout, trust_env=False
        ) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Evaluation server failed; inspect private server.log")
                try:
                    if (await client.get("/api/v1/health")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Evaluation server did not become ready")
            async with httpx.AsyncClient(timeout=30, trust_env=False) as auth:
                response = await auth.post(
                    auth_url + "/token?grant_type=password",
                    json={
                        "email": str(settings.FIRST_SUPERUSER),
                        "password": settings.FIRST_SUPERUSER_PASSWORD,
                    },
                )
                response.raise_for_status()
                token = response.json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            secrets = (token, settings.OPENROUTER_API_KEY, settings.FIRST_SUPERUSER_PASSWORD)
            for case in cases:
                record = await execute_case(client, case, timeout, bindings.get(case.id))
                record = json.loads(scrub_secrets(json.dumps(record), secrets))
                record["run_id"] = run_id
                private_write(directory / f"{case.id}.json", record)
                manifest["results"].append(
                    {
                        "case_id": case.id,
                        "completed": record["completed"],
                        "error_type": record.get("error_type"),
                    }
                )
                private_write(directory / "run.json", manifest)
                print(
                    json.dumps(
                        {
                            "case_id": case.id,
                            "completed": record["completed"],
                            "tools": [
                                event.get("tool")
                                for event in record["events"]
                                if event["type"] == "tool_call_start"
                            ],
                            "seconds": record["latency_seconds"],
                            "error_type": record.get("error_type"),
                        }
                    ),
                    flush=True,
                )
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        private_write(directory / "run.json", manifest)
    print(f"Private evidence: {directory}", flush=True)
    return directory
