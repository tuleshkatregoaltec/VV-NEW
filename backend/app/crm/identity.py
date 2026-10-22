from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any

from app.crm.import_models import (
    CrmPropertyContactClaim,
)

_NON_KEY = re.compile(r"[^a-z0-9]+")

_LOCATION_TOKEN = re.compile(r"[a-z0-9]+")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_UNIT_PLACEHOLDERS = {"", "-", "n/a", "na", "none", "null", "unknown"}

_NUMERIC_PROPERTY_IDENTIFIER = re.compile(r"^\d+(?:\.\d+)?$")

_VILLA_PROPERTY_IDENTIFIER = re.compile(
    r"(?:^|[-\s])(?:villa|v)[-\s]*(?=[a-z0-9.]*\d)[a-z0-9.]+$", re.IGNORECASE
)

_LOCATION_ALIAS_GROUPS = (
    ("jumeirah gate tower 1", "the address jbr 1"),
    ("jumeirah gate tower 2", "the address jbr 2"),
    ("maple", "maple 1"),
    ("maple iii", "maple 3"),
    ("park heights ii t1", "park heights 2 tower 1"),
    ("park heights ii t2", "park heights 2 tower 2"),
    ("executive residences", "executive residences 1"),
    ("club villas at dubai hills", "club villas"),
    ("mag eye phase 1", "mag eye townhouses"),
    ("mag eye 890", "mag 890"),
    ("mag eye 900", "mag 900"),
    ("mag eye 910", "mag 910"),
    ("mag eye 920", "mag 920"),
    ("ellington house", "ellington house 1"),
    ("j one - 1", "j one tower a"),
    ("canal heights", "canal heights 1"),
    ("churchill tower 1-commercial", "churchill executive"),
    ("churchill tower 2-residential", "churchill residence"),
    ("atria ra", "the atria residences"),
    ("citadel tower", "the citadel"),
    ("prive by damac (a)", "prive (a)"),
    ("prive by damac (b)", "prive (b)"),
)

_ROMAN_NUMERALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}


@dataclass
class ParsedContact:
    source_sheet: str
    source_row_number: int
    contact_name: str
    phone_primary: str | None
    phone_alternate: str | None
    email_primary: str | None
    area_name: str
    project_name: str
    building_name: str
    unit_number: str
    property_type: str
    transaction_date: date | None
    transaction_price_aed: int | None
    party_role: str
    procedure_type: str
    row_fingerprint: str
    ownership_status: str = "unmatched"
    confidence_score: int = 20
    match_reason: str = "No exact property evidence was found."
    lead_id: str | None = None
    unit_candidate_key: str | None = None
    latest_sale_date: date | None = None
    latest_sale_price_aed: int | None = None
    later_sale_date: date | None = None
    lease_end: date | None = None
    annual_rent_aed: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def has_valid_contact(self) -> bool:
        return bool(self.phone_primary or self.phone_alternate or self.email_primary)

    @property
    def property_key(self) -> str:
        location = self.building_name or self.project_name
        if not self.unit_number:
            return str(self.evidence.get("cadastral_identity", {}).get("property_key") or "")
        if not location or not self.unit_number:
            return ""
        return f"{_location_key(location)}|{self.unit_number.lower()}"


_OWNERSHIP_PRIORITY = {
    "former_owner": 6,
    "verified_current_owner": 5,
    "probable_current_owner": 4,
    "unit_linked_unverified": 3,
    "conflicting_claim": 2,
    "registry_not_observed": 1,
    "insufficient_property_data": 1,
    "unmatched": 1,
}


def _identity_text(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _owner_key(
    *,
    contact_name: str,
    phone_primary: str | None,
    phone_alternate: str | None,
    email_primary: str | None,
    property_key: str,
) -> str:
    name = _identity_text(contact_name)
    contact = phone_primary or phone_alternate or (email_primary or "").casefold()
    # A name without a contact identifier is not safe evidence that two different
    # properties share one owner. Keep those identities property-scoped.
    seed = f"{name}|{contact or property_key}"
    return hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest()


def _candidate_owner_key(candidate: ParsedContact) -> str:
    return _owner_key(
        contact_name=candidate.contact_name,
        phone_primary=candidate.phone_primary,
        phone_alternate=candidate.phone_alternate,
        email_primary=candidate.email_primary,
        property_key=candidate.property_key or f"source:{candidate.row_fingerprint}",
    )


def _candidate_preference(candidate: ParsedContact) -> tuple[int, int, int, int]:
    return (
        _OWNERSHIP_PRIORITY.get(candidate.ownership_status, 0),
        candidate.confidence_score,
        1 if candidate.has_valid_contact else 0,
        candidate.transaction_date.toordinal() if candidate.transaction_date else 0,
    )


def _dedupe_candidates(candidates: list[ParsedContact]) -> tuple[list[ParsedContact], int]:
    grouped: dict[tuple[str, str], list[ParsedContact]] = {}
    for candidate in candidates:
        property_key = candidate.property_key or f"source:{candidate.row_fingerprint}"
        grouped.setdefault((_candidate_owner_key(candidate), property_key), []).append(candidate)

    unique: list[ParsedContact] = []
    collapsed = 0
    for group in grouped.values():
        best = max(group, key=_candidate_preference)
        ordered_rows = sorted({f"{item.source_sheet}:{item.source_row_number}" for item in group})
        if len(group) > 1:
            collapsed += len(group) - 1
            best.evidence = {
                **best.evidence,
                "duplicate_count": len(group),
                "source_rows": ordered_rows,
                "alternate_claims": [
                    {
                        "source_sheet": item.source_sheet,
                        "source_row_number": item.source_row_number,
                        "transaction_date": (
                            item.transaction_date.isoformat() if item.transaction_date else None
                        ),
                        "transaction_price_aed": item.transaction_price_aed,
                        "ownership_status": item.ownership_status,
                        "confidence_score": item.confidence_score,
                    }
                    for item in group
                    if item is not best
                ],
            }
        best.phone_primary = best.phone_primary or next(
            (item.phone_primary for item in group if item.phone_primary),
            None,
        )
        best.phone_alternate = best.phone_alternate or next(
            (item.phone_alternate for item in group if item.phone_alternate),
            None,
        )
        best.email_primary = best.email_primary or next(
            (item.email_primary for item in group if item.email_primary),
            None,
        )
        unique.append(best)
    unique.sort(key=lambda item: (item.source_sheet, item.source_row_number))
    return unique, collapsed


def _drop_candidates_without_unit(
    candidates: list[ParsedContact],
) -> tuple[list[ParsedContact], int]:
    """Drop rows whose unit is still unknown after registry matching.

    Run this after matching: an exact sale signature can recover the unit even when
    the uploaded cell was empty. A row that still has no unit cannot identify a
    property, and a later import of the same owner would never replace it.
    """
    kept = [candidate for candidate in candidates if candidate.unit_number]
    return kept, len(candidates) - len(kept)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


@lru_cache(maxsize=100_000)
def _location_key(value: str) -> str:
    normalized = _canonical_location_text(value)
    return _NON_KEY.sub("", normalized)


@lru_cache(maxsize=100_000)
def _canonical_location_text(value: str) -> str:
    normalized = value.lower()
    for source, target in {
        "jumeriah": "jumeirah",
        "residences": "residence",
        "apartments": "apartment",
    }.items():
        normalized = normalized.replace(source, target)
    return normalized


@lru_cache(maxsize=100_000)
def _location_tokens(value: str) -> tuple[str, ...]:
    """Return a stable building identity token set without trusting word order."""
    tokens: list[str] = []
    for token in _LOCATION_TOKEN.findall(_canonical_location_text(value)):
        normalized = _ROMAN_NUMERALS.get(token, token)
        tokens.append(str(int(normalized)) if normalized.isdigit() else normalized)
    return tuple(tokens)


@lru_cache(maxsize=100_000)
def _location_alias_identity(value: str) -> str:
    key = _location_key(value)
    if not key:
        return ""
    for group in _LOCATION_ALIAS_GROUPS:
        canonical = _location_key(group[0])
        for alias in group:
            alias_key = _location_key(alias)
            if key == alias_key or (len(alias_key) >= 8 and key.endswith(alias_key)):
                return canonical
    return key


@lru_cache(maxsize=200_000)
def _building_match_strength(owner_location: str, sale_location: str) -> tuple[int, str]:
    """Return a conservative match score for two building labels.

    DXB Interact frequently puts the project after the building while owner
    exports put it before the building. We accept reordered labels and a single
    unambiguous appended/omitted location suffix, but never a generic fuzzy
    string match. Unit equality and a unique area are enforced by the caller.
    """
    owner_key = _location_key(owner_location)
    sale_key = _location_key(sale_location)
    if owner_key and owner_key == sale_key:
        return 3, "exact_building_key"

    owner_tokens = frozenset(_location_tokens(owner_location))
    sale_tokens = frozenset(_location_tokens(sale_location))
    if len(owner_tokens) < 3 or len(sale_tokens) < 3:
        return 0, ""

    owner_numbers = _location_number_markers(owner_location)
    sale_numbers = _location_number_markers(sale_location)
    if owner_numbers != sale_numbers:
        return 0, ""
    if owner_tokens == sale_tokens:
        return 2, "canonical_building_identity"
    if owner_tokens <= sale_tokens or sale_tokens <= owner_tokens:
        return 1, "canonical_building_variant"
    return 0, ""


@lru_cache(maxsize=100_000)
def _location_number_markers(value: str) -> frozenset[str]:
    markers: set[str] = set()
    for token in _location_tokens(value):
        markers.update(str(int(marker)) for marker in re.findall(r"\d+", token))
    return frozenset(markers)


@lru_cache(maxsize=300_000)
def _location_similarity(owner_location: str, sale_location: str) -> int:
    """Score two location labels for transaction-collision resolution."""
    if not owner_location or not sale_location:
        return 0
    if _location_alias_identity(owner_location) == _location_alias_identity(sale_location):
        return 100
    strength, _ = _building_match_strength(owner_location, sale_location)
    if strength:
        return {1: 82, 2: 92, 3: 100}[strength]

    owner_tokens = frozenset(_location_tokens(owner_location))
    sale_tokens = frozenset(_location_tokens(sale_location))
    if not owner_tokens or not sale_tokens:
        return 0
    owner_numbers = _location_number_markers(owner_location)
    sale_numbers = _location_number_markers(sale_location)
    if owner_numbers and sale_numbers and owner_numbers != sale_numbers:
        return 0

    overlap = len(owner_tokens & sale_tokens)
    if not overlap:
        return 0
    return round(75 * overlap / len(owner_tokens | sale_tokens))


def _transaction_location_score(candidate: ParsedContact, row: dict[str, Any]) -> int:
    owner_building = _usable_project_name(candidate.building_name)
    owner_project = _usable_project_name(candidate.project_name)
    owner_area = _usable_project_name(candidate.area_name)
    sale_building = str(row.get("building_name") or "")
    sale_project = str(row.get("project_name") or "")
    sale_area = str(row.get("area_name") or "")
    combined_sale = " ".join(value for value in (sale_building, sale_project) if value)

    building_score = max(
        (_location_similarity(owner_building, value) for value in (sale_building, combined_sale)),
        default=0,
    )
    project_score = max(
        (
            _location_similarity(owner_project, value)
            for value in (sale_project, sale_building, combined_sale)
        ),
        default=0,
    )
    area_score = _location_similarity(owner_area, sale_area)
    if owner_building:
        return round(0.70 * building_score + 0.25 * project_score + 0.05 * area_score)
    if owner_project:
        return round(0.90 * project_score + 0.10 * area_score)
    return area_score


def _normalize_unit(value: Any) -> str:
    unit = _text(value).upper().replace("–", "-")
    unit = re.sub(r"\s+", "", unit)
    if unit.lower() in _UNIT_PLACEHOLDERS:
        return ""
    if unit.endswith(".0") and unit[:-2].isdigit():
        unit = unit[:-2]
    if unit.startswith("TAG-") and "-" in unit:
        unit = unit.rsplit("-", 1)[-1]
    return unit


def _usable_project_name(value: str) -> str:
    """Return a project label only when it is meaningful matching context."""
    project = value.strip()
    if not project or project.lower() in _UNIT_PLACEHOLDERS:
        return ""
    return "" if _NUMERIC_PROPERTY_IDENTIFIER.fullmatch(project) else project


def _recover_embedded_property_identity(candidate: ParsedContact) -> None:
    """Recover a unit embedded in owner-export address fields when unambiguous.

    Some owner files put a complete villa identifier in the building column
    (``DE Maple 2-V-486``) while DXB Interact stores ``Maple 2`` as the
    building and the complete identifier as the unit. Other exports put a
    plain unit number in that column alongside a project name.  Recover only
    these explicit formats; arbitrary address text remains unmatched.
    """
    candidate.unit_number = _normalize_unit(candidate.unit_number)
    source_location = candidate.building_name or candidate.project_name
    edge_tower = re.match(r"^([AB])\d+$", candidate.unit_number, re.IGNORECASE)
    if _location_key(source_location) == "theedge" and edge_tower:
        source_building = candidate.building_name
        candidate.building_name = f"The Edge Tower {edge_tower.group(1).upper()}"
        candidate.evidence["address_recovery"] = {
            "source_building_name": source_building,
            "resolved_building_name": candidate.building_name,
            "resolved_unit_number": candidate.unit_number,
            "method": "project_unit_prefix_tower",
        }
    if candidate.unit_number:
        return

    source_building = candidate.building_name.strip()
    project = _usable_project_name(candidate.project_name)
    if not source_building or source_building.lower() in _UNIT_PLACEHOLDERS or not project:
        return

    source_identifier = _normalize_unit(source_building)
    is_villa_identifier = bool(_VILLA_PROPERTY_IDENTIFIER.search(source_building))
    is_numeric_identifier = source_identifier != "0" and bool(
        _NUMERIC_PROPERTY_IDENTIFIER.fullmatch(source_building)
    )
    if not (is_villa_identifier or is_numeric_identifier):
        return

    candidate.building_name = project
    candidate.unit_number = source_identifier
    candidate.evidence["address_recovery"] = {
        "source_building_name": source_building,
        "resolved_building_name": project,
        "resolved_unit_number": source_identifier,
        "method": (
            "embedded_villa_identifier" if is_villa_identifier else "embedded_numeric_identifier"
        ),
    }


def _normalize_phone(value: Any) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("971") and len(digits) >= 11:
        return f"+{digits}"
    if digits.startswith("05") and len(digits) == 10:
        return f"+971{digits[1:]}"
    if digits.startswith("5") and len(digits) == 9:
        return f"+971{digits}"
    if len(digits) < 7:
        return None
    return f"+{digits}" if raw.startswith("+") else digits


def _normalize_email(value: Any) -> str | None:
    email = _text(value).lower()
    return email if email and _EMAIL.match(email) else None


def _claim_property_key(claim: CrmPropertyContactClaim) -> str:
    if claim.property_key:
        return claim.property_key
    location = claim.building_name or claim.project_name
    if not location or not claim.unit_number:
        return f"claim:{claim.id or claim.row_fingerprint}"
    return f"{_location_key(location)}|{claim.unit_number.casefold()}"


def _claim_owner_key(claim: CrmPropertyContactClaim) -> str:
    if claim.owner_key:
        return claim.owner_key
    return _owner_key(
        contact_name=claim.contact_name,
        phone_primary=claim.phone_primary,
        phone_alternate=claim.phone_alternate,
        email_primary=claim.email_primary,
        property_key=_claim_property_key(claim),
    )


def _claim_preference(claim: CrmPropertyContactClaim) -> tuple[int, int, int, int, int, int]:
    decisive = claim.ownership_status in {
        "former_owner",
        "verified_current_owner",
        "probable_current_owner",
    }
    return (
        int(decisive),
        claim.transaction_date.toordinal() if decisive and claim.transaction_date else 0,
        _OWNERSHIP_PRIORITY.get(claim.ownership_status, 0),
        claim.confidence_score,
        1 if claim.phone_primary or claim.phone_alternate or claim.email_primary else 0,
        claim.transaction_date.toordinal() if claim.transaction_date else 0,
    )


def _dedupe_claims(
    claims: list[CrmPropertyContactClaim],
) -> list[list[CrmPropertyContactClaim]]:
    grouped: dict[tuple[str, str], list[CrmPropertyContactClaim]] = {}
    for claim in claims:
        grouped.setdefault(
            (_claim_owner_key(claim), _claim_property_key(claim)),
            [],
        ).append(claim)
    groups = list(grouped.values())
    groups.sort(
        key=lambda group: _claim_preference(max(group, key=_claim_preference)), reverse=True
    )
    return groups
