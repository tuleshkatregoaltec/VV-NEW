import re
from typing import Literal

Stage = Literal["existing", "off_plan", "upcoming", "all"]

RESIDENTIAL_TYPES = ["studio", "1br", "2br", "3br", "4br", "4br+", "villa", "townhouse"]

COMMERCIAL_TYPES = ["office", "retail", "industrial", "hospitality"]


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").strip().split())


def normalize_unit_type(value: str) -> str:
    raw = normalize_whitespace(value).lower()
    if not raw or raw in {"na", "n/a", "unknown"}:
        return "other"

    patterns = [
        (r"^studio$", "studio"),
        (r"^(1|one)\s*(b\/r|br|bed(room)?s?\+?hall?)$", "1br"),
        (r"^(2|two)\s*(b\/r|br|bed(room)?s?\+?hall?)$", "2br"),
        (r"^(3|three)\s*(b\/r|br|bed(room)?s?\+?hall?)$", "3br"),
        (r"^(4|four)\s*(b\/r|br|bed(room)?s?\+?hall?)$", "4br"),
        (r"^(5|six|7|8|9|10|\d+)\s*(b\/r|br|bed(room)?s?\+?hall?)$", "4br+"),
        (r"penthouse", "4br+"),
        (r"villa", "villa"),
        (r"townhouse", "townhouse"),
        (r"office", "office"),
        (r"shop|retail|store|showroom|kiosk|restaurant", "retail"),
        (
            r"warehouse|workshop|industrial|factory|labor camp|staff accommod",
            "industrial",
        ),
        (r"hotel", "hospitality"),
    ]
    normalized = re.sub(r"[\s\-_/]+", "", raw)
    for pattern, canonical in patterns:
        if re.search(pattern, raw) or re.search(pattern, normalized):
            return canonical
    return "other"


def normalize_project_stage(value: str) -> Stage:
    raw = normalize_whitespace(value).upper()
    if raw in {"FINISHED"}:
        return "existing"
    if raw in {"ACTIVE"}:
        return "off_plan"
    if raw in {"NOT_STARTED", "PENDING", "CONDITIONAL_ACTIVATING"}:
        return "upcoming"
    return "all"
