import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from scripts.evaluation.langsmith import export_payload, publish


def fixture(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    tid = "1" * 32
    spans = [
        {
            "name": "SECRET_SPAN",
            "context": {"trace_id": "0x" + tid, "span_id": "0x" + "2" * 16},
            "parent_id": None,
            "start_time": now,
            "end_time": now,
            "status": {"status_code": "ERROR", "description": "SECRET_EXCEPTION"},
            "attributes": {"gen_ai.input.messages": "SECRET_PROMPT", "db.statement": "SECRET_SQL"},
            "events": [{"content": "SECRET_EVENT"}],
        }
    ]
    (tmp_path / "spans.jsonl").write_text("\n".join(json.dumps(s) for s in spans))
    assessment = {
        "dataset_sha256": "a" * 64,
        "run_id": str(uuid4()),
        "reviews_sha256": "b" * 64,
        "evaluator_sha256": "c" * 64,
        "criteria_sha256": "d" * 64,
        "references_sha256": "e" * 64,
        "runtime": {
            "configured_model": "google/model",
            "tools": {"get_market_pulse": {}},
            "prompt_sha256": "c" * 64,
            "tools_sha256": "d" * 64,
        },
        "cases": [
            {
                "case_id": "q01",
                "trace_id": tid,
                "question_sha256": "f" * 64,
                "parameters_sha256": "0" * 64,
                "observation": {"answer": "SECRET_ANSWER"},
                "checks": {
                    "grounding": {
                        "status": "unassessed",
                        "method": "review",
                        "reason": "SECRET_RATIONALE",
                    }
                },
            }
        ],
    }
    return assessment


def test_payload_is_rebuilt_without_raw_content_or_feedback_rationale(tmp_path):
    payload = export_payload(tmp_path, fixture(tmp_path))
    assert "SECRET" not in json.dumps(payload)
    row = payload["rows"][0]
    assert row["spans"][0]["inputs"] == {"case_id": "q01"}
    assert row["feedback"][0]["value"] == "unassessed"
    assert "score" not in row["feedback"][0]


def test_sdk_adapter_links_examples_experiment_nested_run_and_feedback(tmp_path):
    payload = export_payload(tmp_path, fixture(tmp_path))
    from langsmith.utils import LangSmithNotFoundError

    client = MagicMock()
    client.read_run.side_effect = LangSmithNotFoundError("missing")
    client.read_feedback.side_effect = LangSmithNotFoundError("missing")
    client.read_dataset.return_value = SimpleNamespace(id=uuid4())
    client.create_project.return_value = SimpleNamespace(id=uuid4())
    client.list_examples.return_value = []
    receipt = publish(payload, client)
    assert receipt["project_name"] == payload["project_name"]
    client.create_example.assert_called_once()
    args = client.create_run.call_args.kwargs
    assert args["reference_example_id"] == payload["rows"][0]["example_id"]
    assert args["trace_id"] == payload["rows"][0]["root_id"]
    assert args["outputs"] == {}
    assert client.create_feedback.call_args.kwargs["value"] == "unassessed"
    client.flush.assert_called_once()


def test_reassessment_keeps_example_identity_but_uses_new_run_ids(tmp_path):
    assessment = fixture(tmp_path)
    first = export_payload(tmp_path, assessment)
    assessment["reviews_sha256"] = "f" * 64
    second = export_payload(tmp_path, assessment)
    assert first["rows"][0]["example_id"] == second["rows"][0]["example_id"]
    assert first["rows"][0]["root_id"] != second["rows"][0]["root_id"]
    assert first["project_name"] != second["project_name"]


def test_real_sdk_serialization_omits_environment_metadata(tmp_path, monkeypatch):
    import requests
    from langsmith import Client

    payload = export_payload(tmp_path, fixture(tmp_path))
    captured = []

    def request(self, method, pathname, **kwargs):
        captured.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(Client, "request_with_retries", request)
    monkeypatch.setenv("LANGSMITH_METADATA", '{"private": "ENV_SECRET"}')
    client = Client(
        api_url="https://api.smith.langchain.com",
        api_key="test-only-key",
        auto_batch_tracing=False,
        omit_traced_runtime_info=True,
        tracing_sampling_rate=1.0,
        tracing_mode="langsmith",
    )
    span = payload["rows"][0]["spans"][0]
    client.create_run(**span, project_name="test")
    serialized = str(captured)
    assert captured
    assert "ENV_SECRET" not in serialized
    assert "SECRET_PROMPT" not in serialized


def test_source_verified_operation_labels_survive_without_importing_domains(tmp_path):
    assessment = fixture(tmp_path)
    path = tmp_path / "spans.jsonl"
    root = json.loads(path.read_text())
    child = {
        **root,
        "context": {**root["context"], "span_id": "0x" + "3" * 16},
        "parent_id": root["context"]["span_id"],
        "attributes": {"app.operation": "app.analytics.service.get_market_pulse"},
    }
    path.write_text(json.dumps(root) + "\n" + json.dumps(child))
    payload = export_payload(tmp_path, assessment)
    assert payload["rows"][0]["spans"][1]["name"] == "app.analytics.service.get_market_pulse"
