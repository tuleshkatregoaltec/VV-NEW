"""Private, inspectable per-case report; content never goes to remote telemetry."""

import json
import os
from pathlib import Path


def write_report(directory: Path, assessment: dict) -> Path:
    lines = [
        "# Private benchmark execution and evaluation",
        "",
        "Full local evidence. Do not commit or upload this report.",
        "",
        f"Run: `{assessment['run_id']}`",
        f"Dataset: `{assessment['dataset_sha256']}`",
        f"Data context: `{assessment['data_context']}`",
        "",
        "No overall AI score. Unassessed is not pass; unsupported is not a model factual error.",
        "",
    ]
    available_tools = sorted(assessment["runtime"]["tools"])
    for case in assessment["cases"]:
        record = json.loads((directory / f"{case['case_id']}.json").read_text())
        obs = case["observation"]
        selected = {tool["name"] for tool in obs["tools"]}
        lines += [
            f"## {case['case_id']}",
            "",
            "Requested: " + record["question"],
            "",
            f"Agent: {assessment['runtime']['agent']}; model: {assessment['runtime']['configured_model']}",
            f"Trace: `{case['trace_id']}`; latency: {case['latency_seconds']} seconds",
            f"Selected: {', '.join(sorted(selected)) or 'none'}",
            f"Not selected: {', '.join(tool for tool in available_tools if tool not in selected)}",
            f"Observed sources: {', '.join(obs['sources']) or 'none'}",
            f"Token usage: {obs['token_usage']}; cost: unknown",
            "",
            "| Dimension | Status | Method | Evidence / limitation |",
            "| --- | --- | --- | --- |",
        ]
        for name, check in sorted(case["checks"].items()):
            reason = check["reason"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {name} | {check['status']} | {check['method']} | {reason} |")
        lines += [
            "",
            "### Actual tool calls and results",
            "",
            "```json",
            json.dumps(obs["tools"], indent=2, ensure_ascii=False),
            "```",
            "",
            "### Actual queries and sources",
            "",
            "```json",
            json.dumps(obs["queries"], indent=2, ensure_ascii=False),
            "```",
            "",
            "### Actual final response",
            "",
            record.get("answer", "No answer"),
            "",
            "### Evaluation boundary",
            "",
            "Deterministic: transport, persistence, schema, observed sources, finite evidence and recognized date predicates. Semantic review: grounding, limitation disclosure, completeness and unsupported promises. Human/reference review: market truth, ambiguous units, scope and exact values. Missing references leave numerical correctness unassessed.",
            "",
        ]
    from scripts.evaluation.tracing import fingerprint

    version = fingerprint(
        {
            key: assessment[key]
            for key in (
                "evaluator_sha256",
                "criteria_sha256",
                "references_sha256",
                "reviews_sha256",
            )
        }
    )[:16]
    path = directory / f"report-{version}.md"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write("\n".join(lines))
    return path
