import json
from pathlib import Path

from app.chat.models import MessageResponse
from app.feasibility.models import FeasibilityStudyUpdateRequest


def test_stored_json_extensions_and_chat_history_are_preserved():
    payload = json.loads((Path(__file__).parent / "fixtures/persisted_payloads.json").read_text())
    message = MessageResponse.model_validate(
        {
            "id": 1,
            "conversation_id": 1,
            "role": "assistant",
            "content": "Result",
            "created_at": "2026-01-01T00:00:00Z",
            "meta_data": payload["chat_metadata"],
        }
    )
    assert message.model_dump(mode="json")["meta_data"] == payload["chat_metadata"]
    request = FeasibilityStudyUpdateRequest.model_validate(payload["study_update"])
    assert request.model_dump(mode="json", exclude_unset=True) == payload["study_update"]
