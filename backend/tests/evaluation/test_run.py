import json

import httpx

from scripts.evaluation.dataset import Case
from scripts.evaluation.run import execute_case


async def test_explicit_binding_reaches_stream_and_persistence_without_changing_template():
    case = Case(id="q16", title="Binding", question="Compare [building]", parameters=["building"])
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/stream"):
            assert json.loads(request.content) == {"message": "Compare Explicit Tower"}
            return httpx.Response(
                200, text='data: {"type":"content","content":"Answer"}\n\ndata: {"type":"done"}\n\n'
            )
        if request.method == "POST":
            return httpx.Response(200, json={"id": "created-by-this-run"})
        return httpx.Response(
            200,
            json=[
                {"role": "user", "content": "Compare Explicit Tower"},
                {"role": "assistant", "content": "Answer"},
            ],
        )

    async with httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(handle)
    ) as client:
        result = await execute_case(client, case, 5, {"building": "Explicit Tower"})
    assert result["completed"] is True
    assert result["parameters"] == {"building": "Explicit Tower"}
    assert result["unbound_parameters"] == []
    assert result["answer"] == "Answer"
    assert case.question == "Compare [building]"
    assert requests[-1].method == "DELETE"
    assert requests[-1].url.path.endswith("created-by-this-run")


async def test_stream_failure_still_cleans_up_only_its_created_conversation():
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/stream"):
            return httpx.Response(403)
        return httpx.Response(200, json={"id": "owned-evaluation-conversation"})

    async with httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(handle)
    ) as client:
        result = await execute_case(client, Case(id="q01", title="Failure", question="Question"), 5)
    assert not result["completed"]
    assert result["error_type"] == "HTTPStatusError"
    assert calls[-1].method == "DELETE"
    assert calls[-1].url.path.endswith("owned-evaluation-conversation")
