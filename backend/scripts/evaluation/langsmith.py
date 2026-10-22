"""Explicit metadata-only publication of already assessed, private local executions."""

import json
import re
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.observability import safe_span_fields
from scripts.evaluation.evaluate import STATUSES
from scripts.evaluation.inventory import build_inventory
from scripts.evaluation.tracing import fingerprint, private_write


def identifier(value, pattern, label):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def export_payload(directory: Path, assessment: dict):
    """No raw free text is allowed in this representation, even error descriptions."""
    dataset = identifier(assessment["dataset_sha256"], r"[0-9a-f]{64}", "dataset digest")
    model = identifier(
        assessment["runtime"]["configured_model"],
        r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.:/-]+",
        "configured model",
    )
    run_id = str(UUID(assessment["run_id"]))
    revision = fingerprint(
        {
            key: assessment[key]
            for key in (
                "evaluator_sha256",
                "criteria_sha256",
                "references_sha256",
                "reviews_sha256",
            )
        }
    )
    namespace = uuid5(UUID(run_id), revision)
    spans = [json.loads(line) for line in (directory / "spans.jsonl").read_text().splitlines()]
    runtime = assessment["runtime"]
    # Labels are taken from registered schemas in the source runtime, not model text.
    tools = {
        identifier(name, r"[a-zA-Z_][a-zA-Z0-9_]*", "registered tool") for name in runtime["tools"]
    }
    operations = set(build_inventory()["functions"])
    rows = []
    for case in assessment["cases"]:
        case_id = identifier(case["case_id"], r"q[0-9]{2,}", "case ID")
        tid = identifier(case["trace_id"], r"[0-9a-f]{32}", "trace ID")
        selected = [span for span in spans if span["context"]["trace_id"].removeprefix("0x") == tid]
        clean = []
        for span in selected:
            name, attrs = safe_span_fields(
                span["name"],
                span.get("attributes") or {},
                model=model,
                agents={runtime.get("agent_name") or "agent"},
                tools=tools,
                operations=operations,
            )
            sid = str(uuid5(namespace, tid + span["context"]["span_id"]))
            parent = (
                str(uuid5(namespace, tid + span["parent_id"])) if span.get("parent_id") else None
            )
            start, end = (
                datetime.fromisoformat(span["start_time"]),
                datetime.fromisoformat(span["end_time"]),
            )
            clean.append(
                {
                    "id": sid,
                    "parent_run_id": parent,
                    "name": name,
                    "inputs": {},
                    "outputs": {},
                    "run_type": "llm"
                    if name == "model.request"
                    else "tool"
                    if name.startswith("tool.")
                    else "chain",
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "error": "execution_error"
                    if span["status"]["status_code"] == "ERROR"
                    else None,
                    "extra": {"metadata": attrs},
                }
            )
        ids = {span["id"] for span in clean}
        roots = [span for span in clean if span["parent_run_id"] not in ids]
        if len(roots) != 1:
            raise ValueError("Expected one complete streaming trace per case")
        root = roots[0]
        root["parent_run_id"] = None
        root["name"] = f"benchmark.{case_id}"
        root["inputs"] = {"case_id": case_id}
        root["extra"]["metadata"].update(
            {
                "dataset_sha256": dataset,
                "prompt_sha256": identifier(
                    runtime["prompt_sha256"], r"[0-9a-f]{64}", "prompt digest"
                ),
                "tools_sha256": identifier(
                    runtime["tools_sha256"], r"[0-9a-f]{64}", "tools digest"
                ),
                "environment": "local",
                "content_excluded": True,
            }
        )
        for key in ("evaluator_sha256", "criteria_sha256", "references_sha256", "reviews_sha256"):
            root["extra"]["metadata"][key] = identifier(assessment[key], r"[0-9a-f]{64}", key)
        if assessment.get("git_sha"):
            root["extra"]["metadata"]["git_sha"] = identifier(
                assessment["git_sha"], r"[0-9a-f]{40,64}", "git SHA"
            )
        if assessment.get("data_context"):
            root["extra"]["metadata"]["data_context_sha256"] = fingerprint(
                assessment["data_context"]
            )
        checks = []
        for dimension, check in case["checks"].items():
            # Explicit dimension names, not arbitrary reviewer-supplied keys/comments.
            from scripts.evaluation.evaluate import REVIEW_DIMENSIONS

            known = REVIEW_DIMENSIONS | {
                "execution",
                "persistence",
                "trace_coverage",
                "tool_schema",
                "tool_execution",
                "query_execution",
                "finite_query_values",
                "evidence_availability",
                "source_coverage",
                "calendar_window",
                "capability_support",
                "parameter_binding",
            }
            if dimension not in known and not re.fullmatch(r"reference_evidence_[0-9]+", dimension):
                raise ValueError("Unknown feedback dimension")
            if check["status"] not in STATUSES or check["method"] not in {
                "deterministic",
                "review",
                "source_review",
                "human",
                "assisted_review",
            }:
                raise ValueError("Unknown feedback status/method")
            checks.append(
                {
                    "key": dimension,
                    "value": check["status"],
                    "source_info": {"method": check["method"]},
                }
            )
        example_key = fingerprint(
            {
                "case": case_id,
                "question": case["question_sha256"],
                "parameters": case["parameters_sha256"],
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "example_id": str(uuid5(NAMESPACE_URL, dataset + example_key)),
                "metadata": {
                    key: identifier(case[key], r"[0-9a-f]{64}", key)
                    for key in ("question_sha256", "parameters_sha256")
                },
                "root_id": root["id"],
                "spans": clean,
                "feedback": checks,
            }
        )
    return {
        "dataset_name": f"vitevue-canonical-{dataset}",
        "project_name": f"vitevue-local-evaluation-{run_id}-{revision[:16]}",
        "run_id": run_id,
        "rows": rows,
    }


def publish(payload, client):
    from langsmith.utils import LangSmithNotFoundError

    try:
        dataset = client.read_dataset(dataset_name=payload["dataset_name"])
    except LangSmithNotFoundError:
        dataset = client.create_dataset(
            payload["dataset_name"],
            description="Content excluded. Inspect full canonical questions and execution evidence locally.",
        )
    existing = {str(example.id) for example in client.list_examples(dataset_id=dataset.id)}
    for row in payload["rows"]:
        if row["example_id"] not in existing:
            client.create_example(
                inputs={"case_id": row["case_id"]},
                dataset_id=dataset.id,
                example_id=row["example_id"],
                metadata=row["metadata"],
            )
    project = client.create_project(
        payload["project_name"],
        reference_dataset_id=dataset.id,
        metadata={"environment": "local", "content_excluded": True},
        upsert=True,
    )
    for row in payload["rows"]:
        pending = {span["id"]: span for span in row["spans"]}
        orders = {}
        while pending:
            ready = [
                span
                for span in pending.values()
                if span["parent_run_id"] is None or span["parent_run_id"] in orders
            ]
            if not ready:
                raise ValueError("Trace graph is cyclic or incomplete")
            for span in ready:
                item = {**span}
                start = datetime.fromisoformat(item["start_time"])
                order = start.strftime("%Y%m%dT%H%M%S%fZ") + item["id"]
                if item["parent_run_id"]:
                    order = orders[item["parent_run_id"]] + "." + order
                orders[item["id"]] = order
                try:
                    client.read_run(item["id"])
                except LangSmithNotFoundError:
                    client.create_run(
                        **item,
                        project_name=payload["project_name"],
                        trace_id=row["root_id"],
                        dotted_order=order,
                        reference_example_id=row["example_id"]
                        if item["id"] == row["root_id"]
                        else None,
                    )
                pending.pop(item["id"])
        for check in row["feedback"]:
            feedback_id = uuid5(UUID(row["root_id"]), check["key"] + payload["project_name"])
            try:
                client.read_feedback(feedback_id)
            except LangSmithNotFoundError:
                client.create_feedback(
                    run_id=row["root_id"],
                    **check,
                    feedback_id=uuid5(UUID(row["root_id"]), check["key"] + payload["project_name"]),
                )
    client.flush()
    return {
        "project_id": str(project.id),
        "dataset_id": str(dataset.id),
        "project_name": payload["project_name"],
    }


def export_run(directory: Path, assessment_path: Path, upload=False):
    assessment = json.loads(assessment_path.read_text())
    if json.loads((directory / "run.json").read_text())["run_id"] != assessment["run_id"]:
        raise ValueError("Assessment and evidence run differ")
    payload = export_payload(directory, assessment)
    private_write(directory / "langsmith-preview.json", payload)
    if not upload:
        return {"uploaded": False, "preview": str(directory / "langsmith-preview.json")}
    from app.config import settings

    if not settings.LANGSMITH_API_KEY:
        raise ValueError("Set LANGSMITH_API_KEY locally before explicit sanitized upload")
    from langsmith import Client

    client = Client(
        api_key=settings.LANGSMITH_API_KEY,
        api_url=settings.LANGSMITH_ENDPOINT,
        workspace_id=settings.LANGSMITH_WORKSPACE_ID,
        auto_batch_tracing=False,
        omit_traced_runtime_info=True,
        tracing_mode="langsmith",
        tracing_sampling_rate=1.0,
    )
    receipt = publish(payload, client)
    private_write(directory / "langsmith-receipt.json", receipt)
    return {"uploaded": True, **receipt}
