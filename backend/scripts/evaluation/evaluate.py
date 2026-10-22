"""Trace-driven checks: execution, evidence and review are separate dimensions."""

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.evaluation.tracing import fingerprint, private_write

CRITERIA = Path(__file__).with_name("criteria.json")
SOURCE_TABLES = {
    "transactions": {"ch_transactions"},
    "registered_rents": {"ch_rent_contracts"},
    "listings": {"pf_listings_bronze", "silver_pf_listings", "ch_listing_fact"},
    "projects": {
        "ch_projects",
        "ch_project_fact",
        "silver_reelly_projects",
        "reelly_projects_bronze",
    },
}
REVIEW_DIMENSIONS = {
    "grounding",
    "completeness",
    "semantic_quality",
    "numerical_correctness",
    "capability_disclosure",
}
STATUSES = {"pass", "fail", "unassessed", "unsupported", "requires_parameters"}


def metric(status, reason, method="deterministic"):
    return {"status": status, "reason": reason, "method": method}


def parse(value):
    return json.loads(value) if isinstance(value, str) else value


def nonfinite(value):
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(nonfinite(item) for item in value.values())
    if isinstance(value, list):
        return any(nonfinite(item) for item in value)
    return False


def observation(record, spans):
    trace_id = record.get("trace_ids", {}).get("stream")
    selected = [
        span for span in spans if span["context"]["trace_id"].removeprefix("0x") == trace_id
    ]
    tools, queries, models = [], [], []
    for span in selected:
        attrs = span.get("attributes") or {}
        if "gen_ai.tool.name" in attrs:
            tools.append(
                {
                    "name": attrs["gen_ai.tool.name"],
                    "arguments": parse(attrs.get("tool_arguments", "{}")),
                    "result": parse(attrs.get("tool_response", "null")),
                    "span_id": span["context"]["span_id"],
                    "error": span["status"]["status_code"] == "ERROR",
                }
            )
        if attrs.get("db.system") == "clickhouse":
            queries.append(
                {
                    "tables": attrs.get("app.source.tables", []),
                    "sha256": attrs.get("app.query.sha256"),
                    "sql": attrs.get("db.statement", ""),
                    "parameters": parse(attrs.get("app.query.parameters", "{}")),
                    "result": parse(attrs.get("app.query.result", "null")),
                    "call_path": attrs.get("app.call_path", []),
                    "error": span["status"]["status_code"] == "ERROR",
                }
            )
        if "gen_ai.request.model" in attrs:
            models.append(
                {
                    key: attrs[key]
                    for key in (
                        "gen_ai.request.model",
                        "gen_ai.response.model",
                        "gen_ai.system",
                        "gen_ai.usage.input_tokens",
                        "gen_ai.usage.output_tokens",
                    )
                    if key in attrs
                }
            )
    ids = {span["context"].get("span_id") for span in selected}
    missing_parents = sorted(
        {
            span["parent_id"]
            for span in selected
            if span.get("parent_id") and span["parent_id"] not in ids
        }
    )
    tables = {table for query in queries for table in query["tables"]}
    return {
        "answer": record.get("answer", ""),
        "tools": tools,
        "queries": queries,
        "models": models,
        "sources": sorted(source for source, known in SOURCE_TABLES.items() if tables & known),
        "trace_span_count": len(selected),
        "trace_missing_parents": missing_parents,
        "trace_root_present": any(span["name"] == "application.http" for span in selected),
        "token_usage": {
            "input": sum(model.get("gen_ai.usage.input_tokens", 0) for model in models),
            "output": sum(model.get("gen_ai.usage.output_tokens", 0) for model in models),
        },
        "cost": None,
    }


def evaluate_case(record, observed, runtime, criteria):
    events = record.get("events", [])
    errors = [event for event in events if event.get("type") == "error"]
    done = sum(event.get("type") == "done" for event in events)
    completed = (
        record.get("http_status") == 200
        and done == 1
        and events[-1].get("type") == "done"
        and not errors
        and not record.get("error_type")
    )
    checks = {
        "execution": metric(
            "pass" if completed else "fail",
            "stream completed" if completed else "HTTP, stream or client failure",
        )
    }
    messages = record.get("persisted_messages", [])
    persisted = any(
        message["role"] == "user" and message["content"] == record["question"]
        for message in messages
    ) and any(
        message["role"] == "assistant" and message["content"] == observed["answer"]
        for message in messages
    )
    checks["persistence"] = metric(
        "pass" if persisted else "fail",
        "saved messages match streamed content"
        if persisted
        else "saved messages absent or different",
    )
    started_tools = [
        event.get("tool") for event in events if event.get("type") == "tool_call_start"
    ]
    trace_valid = (
        observed["trace_root_present"]
        and not observed["trace_missing_parents"]
        and bool(observed["models"])
        and Counter(started_tools) == Counter(tool["name"] for tool in observed["tools"])
    )
    checks["trace_coverage"] = metric(
        "pass" if trace_valid else "fail",
        "HTTP/model/tool spans and parent links accounted for"
        if trace_valid
        else "missing root, model, tool evidence or parent spans",
    )
    tools = observed["tools"]
    schema_errors = []
    for tool in tools:
        schema = runtime["tools"].get(tool["name"], {}).get("schema")
        if schema is None or list(Draft202012Validator(schema).iter_errors(tool["arguments"])):
            schema_errors.append(tool["name"])
    checks["tool_schema"] = metric(
        "fail" if schema_errors else "pass" if tools else "unassessed",
        "invalid/unregistered arguments"
        if schema_errors
        else "arguments satisfy registered schemas"
        if tools
        else "no tools selected",
    )
    failed_tools = any(
        tool["error"]
        or (isinstance(tool["result"], dict) and tool["result"].get("success") is False)
        for tool in tools
    )
    checks["tool_execution"] = metric(
        "fail" if failed_tools else "pass" if tools else "unassessed",
        "tool error"
        if failed_tools
        else "selected tools returned normally"
        if tools
        else "no tools selected",
    )
    checks["query_execution"] = metric(
        "fail"
        if any(query["error"] for query in observed["queries"])
        else "pass"
        if observed["queries"]
        else "unassessed",
        "only observed database calls are assessed",
    )
    checks["finite_query_values"] = metric(
        "fail"
        if any(nonfinite(query["result"]) for query in observed["queries"])
        else "pass"
        if observed["queries"]
        else "unassessed",
        "nonfinite aggregate values are unusable numeric evidence"
        if any(nonfinite(query["result"]) for query in observed["queries"])
        else "no observed nonfinite values"
        if observed["queries"]
        else "no query executed",
    )
    empty = [
        tool
        for tool in tools
        if isinstance(tool["result"], dict) and tool["result"].get("transaction_count") == 0
    ]
    availability = (
        "failed"
        if failed_tools
        else "none"
        if not tools
        else "empty"
        if len(empty) == len(tools)
        else "partial"
        if empty
        else "present"
    )
    checks["evidence_availability"] = metric(
        "fail"
        if availability == "failed"
        else "unassessed"
        if availability in {"none", "empty"}
        else "pass",
        availability,
    )
    required = set(criteria.get("required_sources", []))
    missing = required - set(observed["sources"])
    checks["source_coverage"] = metric(
        "unassessed" if not required else "fail" if missing else "pass",
        "missing: " + ", ".join(sorted(missing))
        if missing
        else "observed required source families; does not prove correct filters or facts",
    )
    window = criteria.get("calendar_window")
    # This is an observed SQL predicate, not an inference from model arguments alone.
    rolling = any(
        "instance_date >= today() - {period_days:Int32}" in query["sql"]
        for query in observed["queries"]
    )
    checks["calendar_window"] = metric(
        "fail" if window and rolling else "unassessed",
        "rolling-day SQL cannot implement the requested complete calendar window"
        if window and rolling
        else "no supported deterministic calendar-window proof",
    )
    for dimension in REVIEW_DIMENSIONS:
        checks[dimension] = metric(
            "unassessed",
            "requires evidence-based semantic/human review or authoritative numerical reference",
            "review",
        )
    checks["capability_support"] = metric(
        "unsupported" if criteria.get("capability_verified") else "unassessed",
        "; ".join(criteria.get("missing_capabilities", []))
        if criteria.get("capability_verified")
        else "capability assessment requires review after tool changes",
        "source_review",
    )
    checks["parameter_binding"] = metric(
        "requires_parameters" if record.get("unbound_parameters") else "pass",
        "unbound template retained"
        if record.get("unbound_parameters")
        else "no unresolved placeholders",
    )
    return checks


def apply_references(checks, observed, references):
    """Compare curated structured evidence; never exact-match free-form answers."""
    for index, reference in enumerate(references):
        path = reference["path"]
        if not path or path[0] not in {"tools", "queries"} or not reference.get("source"):
            raise ValueError(
                "References require a structured evidence path and authoritative source"
            )
        value = observed
        try:
            for component in path:
                value = value[component]
            kind = reference["kind"]
            if kind == "exact":
                passed = value == reference["expected"] and not nonfinite(value)
            elif kind == "range":
                passed = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and reference["min"] <= value <= reference["max"]
                )
            elif kind == "records":
                passed = isinstance(value, list) and {
                    json.dumps(row, sort_keys=True) for row in value
                } == {json.dumps(row, sort_keys=True) for row in reference["expected"]}
            else:
                raise ValueError("Unknown reference kind")
        except (KeyError, IndexError, TypeError):
            passed = False
        checks[f"reference_evidence_{index}"] = metric(
            "pass" if passed else "fail", reference["source"]
        )


def assess_run(directory: Path, references_path=None, reviews_path=None):
    manifest = json.loads((directory / "run.json").read_text())
    runtime = json.loads((directory / "runtime.json").read_text())
    spans = [json.loads(line) for line in (directory / "spans.jsonl").read_text().splitlines()]
    criteria = json.loads(CRITERIA.read_text())
    references = json.loads(Path(references_path).read_text()) if references_path else {}
    if references and (
        references.get("dataset_sha256") != manifest["dataset_sha256"]
        or references.get("data_context") != manifest["data_context"]
        or references.get("date") != manifest["started_at"][:10]
    ):
        raise ValueError("Reference dataset, date and data context must match this run")
    reviews = json.loads(Path(reviews_path).read_text()) if reviews_path else {}
    if reviews and reviews.get("run_id") != manifest["run_id"]:
        raise ValueError("Review belongs to another run")
    result = {
        "run_id": manifest["run_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "criteria_sha256": fingerprint(criteria),
        "references_sha256": fingerprint(references),
        "evaluator_sha256": fingerprint(Path(__file__).read_text()),
        "reviews_sha256": fingerprint(reviews),
        "date": manifest["started_at"][:10],
        "data_context": manifest["data_context"],
        "runtime": runtime,
        "git_sha": manifest["git_sha"],
        "created_at": datetime.now().astimezone().isoformat(),
        "cases": [],
    }
    for case_id in manifest["case_ids"]:
        record = json.loads((directory / f"{case_id}.json").read_text())
        observed = observation(record, spans)
        policy = {
            **criteria["cases"].get(case_id, {}),
            "capability_verified": runtime["tools_sha256"] == criteria["capability_tools_sha256"]
            and case_id in criteria["cases"],
        }
        checks = evaluate_case(record, observed, runtime, policy)
        apply_references(checks, observed, references.get("cases", {}).get(case_id, []))
        for dimension, review in reviews.get("cases", {}).get(case_id, {}).items():
            if (
                dimension not in REVIEW_DIMENSIONS
                or review["status"] not in STATUSES
                or review.get("method") not in {"human", "assisted_review"}
                or not review.get("reason")
                or not review.get("reviewer")
            ):
                raise ValueError(
                    "Invalid review: require dimension, status, method, reviewer and rationale"
                )
            checks[dimension] = review
        result["cases"].append(
            {
                "case_id": case_id,
                "trace_id": record["trace_ids"].get("stream"),
                "question_sha256": fingerprint(record["question"]),
                "parameters_sha256": fingerprint(record["parameters"]),
                "latency_seconds": record["latency_seconds"],
                "checks": checks,
                "observation": observed,
            }
        )
    # Separate immutable assessments when references/reviews change.
    assessment_id = fingerprint(
        {
            "run": result["run_id"],
            "evaluator": result["evaluator_sha256"],
            "criteria": result["criteria_sha256"],
            "references": references,
            "reviews": reviews,
        }
    )[:16]
    path = directory / f"assessment-{assessment_id}.json"
    private_write(path, result)
    private_write(
        directory / f"review-template-{assessment_id}.json",
        {
            "run_id": manifest["run_id"],
            "cases": {
                case["case_id"]: {
                    dimension: {
                        "status": "unassessed",
                        "method": "human",
                        "reviewer": "",
                        "reason": "",
                    }
                    for dimension in sorted(REVIEW_DIMENSIONS)
                }
                for case in result["cases"]
            },
        },
    )
    return path, result


def compare(baseline, candidate):
    keys = (
        "dataset_sha256",
        "criteria_sha256",
        "references_sha256",
        "evaluator_sha256",
        "date",
        "data_context",
    )
    mismatches = [key for key in keys if baseline[key] != candidate[key]]
    before = {case["case_id"]: case for case in baseline["cases"]}
    after = {case["case_id"]: case for case in candidate["cases"]}
    if before.keys() != after.keys():
        mismatches.append("case_ids")
    for case_id in before.keys() & after.keys():
        for key in ("question_sha256", "parameters_sha256"):
            if before[case_id][key] != after[case_id][key]:
                mismatches.append(f"{case_id}.{key}")
    if mismatches:
        return {"comparable": False, "mismatches": mismatches, "regressions": [], "changes": []}
    changes, regressions = [], []
    for case_id, old in before.items():
        new = after[case_id]
        for dimension in old["checks"].keys() | new["checks"].keys():
            prior = old["checks"].get(dimension, {"status": "unassessed", "method": "missing"})
            current = new["checks"].get(dimension, {"status": "unassessed", "method": "missing"})
            if prior["method"] != current["method"]:
                changes.append(
                    {
                        "case_id": case_id,
                        "dimension": dimension,
                        "change": "assessment_method_changed",
                    }
                )
            if prior["status"] != current["status"]:
                change = {
                    "case_id": case_id,
                    "dimension": dimension,
                    "before": prior["status"],
                    "after": current["status"],
                }
                changes.append(change)
                if prior["status"] == "pass" and current["status"] != "pass":
                    regressions.append(change)
    # A mutable warehouse label cannot establish a reproducible quality comparison.
    mutable = baseline["data_context"] == "local-mutable-warehouse"
    return {
        "comparable": not mutable,
        "mismatches": ["warehouse_snapshot_not_pinned"] if mutable else [],
        "changes": changes,
        "regressions": regressions,
    }
