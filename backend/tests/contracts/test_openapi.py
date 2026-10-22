import json
from pathlib import Path

from conftest import get_app


def test_openapi_preserves_frontend_contract():
    expected = json.loads((Path(__file__).parent / "fixtures/openapi.json").read_text())
    assert get_app().openapi() == expected
