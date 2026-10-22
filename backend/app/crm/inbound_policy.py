from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.crm.inbound_models import (
    CrmInboundEnquiry,
    InboundOwnershipEnrichmentResponse,
    InboundPriorityResponse,
)

_FIRST_RESPONSE_MINUTES = 5

_QUALIFIED_STATUSES = {"qualified", "viewing", "valuation", "signed", "active", "won"}

_NON_KEY = re.compile(r"[^a-z0-9]+")

_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?971|0)?[\s().-]*5\d(?:[\s().-]*\d){7}(?!\d)")

_ID_RE = re.compile(r"\b\d{9,18}\b")

_ALIASES: dict[str, set[str]] = {
    "full_name": {"name", "fullname", "leadname", "contactname", "customername"},
    "first_name": {"firstname", "first"},
    "last_name": {"lastname", "surname", "last"},
    "phone": {"phone", "phonenumber", "mobile", "mobilenumber", "telephone"},
    "email": {"email", "emailaddress"},
    "whatsapp": {"whatsapp", "whatsappnumber"},
    "intent": {"intent", "leadtype", "enquirytype", "transactiontype", "lookingfor"},
    "source": {"source", "leadsource", "channel", "platform"},
    "campaign": {"campaign", "campaignname", "formname", "adname", "adsetname"},
    "source_lead_id": {"leadid", "sourceleadid", "recordid", "submissionid"},
    "notes": {"notes", "message", "comments", "requirements", "description", "enquiry"},
    "locations": {"area", "location", "community", "project", "preferredlocation"},
    "property_type": {"propertytype", "unittype", "assettype"},
    "bedrooms": {"bedrooms", "beds", "bedroom"},
    "budget_min": {"budgetmin", "minbudget", "minimumprice"},
    "budget_max": {"budget", "budgetmax", "maxbudget", "maximumprice", "price"},
    "timeline": {"timeline", "movingdate", "purchasedate", "when", "urgency"},
    "financing": {"financing", "paymentmethod", "mortgage", "finance"},
    "language": {"language", "preferredlanguage"},
    "consent": {"consent", "marketingconsent", "optin", "permissiontocontact"},
    "created_at": {"createdat", "createdtime", "submissiondate", "leaddate", "date"},
    "unit_number": {"unit", "unitnumber", "villanumber"},
    "building": {"building", "tower", "buildingname"},
}

_KNOWN_LOCATIONS = (
    "JBR",
    "Dubai Marina",
    "Downtown Dubai",
    "Business Bay",
    "Palm Jumeirah",
    "Jumeirah Village Circle",
    "JVC",
    "Dubai Hills",
    "Arabian Ranches",
    "Tilal Al Ghaf",
    "Damac Hills",
    "Dubai Creek Harbour",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token(value: Any) -> str:
    return _NON_KEY.sub("", str(value or "").strip().lower())


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _amount(value: Any) -> int | None:
    if isinstance(value, int | float) and value > 0:
        return int(value)
    raw = _text(value).lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(m|mn|million|k|thousand)?", raw)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2) or ""
    if suffix in {"m", "mn", "million"}:
        number *= 1_000_000
    elif suffix in {"k", "thousand"}:
        number *= 1_000
    return int(number) if number > 0 else None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raw = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return _utcnow()


def _normalize_intent(value: Any, notes: str = "") -> str | None:
    raw = f"{_text(value)} {notes}".lower()
    if any(word in raw for word in ("landlord", "list for rent", "rent out", "to let")):
        return "let"
    if any(word in raw for word in ("seller", "sell my", "want to sell", "valuation")):
        return "sell"
    if any(word in raw for word in ("tenant", "looking to rent", "for rent", "lease")):
        return "rent"
    if any(word in raw for word in ("buyer", "looking to buy", "purchase", "for sale")):
        return "buy"
    return None


def _qualification_complete(enquiry: CrmInboundEnquiry) -> bool:
    if (
        enquiry.record_kind != "opportunity"
        or enquiry.intent not in {"buy", "rent", "sell", "let"}
        or enquiry.status not in _QUALIFIED_STATUSES
    ):
        return False
    criteria = enquiry.criteria or {}
    if enquiry.intent in {"sell", "let"}:
        return bool(criteria.get("building") and criteria.get("unit_number"))
    has_location = bool(_locations(criteria))
    has_brief = any(
        criteria.get(field) not in (None, "", [])
        for field in ("budget_max_aed", "bedrooms_min", "property_type")
    )
    return has_location and has_brief


def _sla_state(enquiry: CrmInboundEnquiry) -> str:
    if enquiry.first_contact_at:
        return "responded"
    due = enquiry.first_response_due_at
    if not due:
        return "on_time"
    now = _utcnow()
    if due <= now:
        return "overdue"
    return "due_soon" if due - now <= timedelta(minutes=2) else "on_time"


def _belongs_in_inbox(enquiry: CrmInboundEnquiry) -> bool:
    """Keep the inbox operational: an untouched qualified lead still needs a response."""
    if enquiry.record_kind != "opportunity" or enquiry.status in {
        "won",
        "nurture",
        "closed_lost",
        "disqualified",
    }:
        return False
    if enquiry.first_contact_at is None:
        return True
    if enquiry.status in {"new", "assigned", "attempted", "contacted", "needs_review"}:
        return True
    # Follow-up is a calendar-date commitment in the agent's local workspace,
    # unlike response timestamps which are stored in UTC.
    return bool(enquiry.next_follow_up and enquiry.next_follow_up <= date.today())


def _redact(text: str, contact_values: list[str]) -> str:
    redacted = text
    for value in sorted((v for v in contact_values if len(v.strip()) >= 3), key=len, reverse=True):
        redacted = re.sub(re.escape(value), "[REDACTED]", redacted, flags=re.IGNORECASE)
    redacted = _EMAIL_RE.sub("[EMAIL]", redacted)
    redacted = _PHONE_RE.sub("[PHONE]", redacted)
    return _ID_RE.sub("[ID]", redacted)[:2_000]


def _deterministic_criteria(row: dict[str, Any], notes: str) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    location_value = _text(row.get("locations"))
    locations = [item.strip() for item in re.split(r"[,;/|]", location_value) if item.strip()]
    if not locations:
        lowered = notes.lower()
        locations = [location for location in _KNOWN_LOCATIONS if location.lower() in lowered]
    if locations:
        criteria["locations"] = list(dict.fromkeys(locations))

    bedroom_value = _text(row.get("bedrooms"))
    bedroom_match = re.search(r"(studio|\d+)\s*(?:bed|br|bedroom)?", bedroom_value, re.I)
    if not bedroom_match:
        bedroom_match = re.search(r"(studio|\d+)\s*(?:bed|br|bedroom)s?", notes, re.I)
    if bedroom_match:
        bedrooms = 0 if bedroom_match.group(1).lower() == "studio" else int(bedroom_match.group(1))
        criteria["bedrooms_min"] = bedrooms
        criteria["bedrooms_max"] = bedrooms

    minimum = _amount(row.get("budget_min"))
    maximum = _amount(row.get("budget_max"))
    if maximum is None:
        budget_match = re.search(
            r"(?:budget|around|up to|max(?:imum)?)[^\d]{0,12}(\d+(?:\.\d+)?\s*(?:m|mn|million|k)?)",
            notes,
            re.I,
        )
        maximum = _amount(budget_match.group(1)) if budget_match else None
    if minimum:
        criteria["budget_min_aed"] = minimum
    if maximum:
        criteria["budget_max_aed"] = maximum
    if property_type := _text(row.get("property_type")):
        criteria["property_type"] = property_type
    if timeline := _text(row.get("timeline")):
        criteria["timeline"] = timeline
    if financing := _text(row.get("financing")):
        criteria["financing"] = financing
    if unit := _text(row.get("unit_number")):
        criteria["unit_number"] = unit
    if building := _text(row.get("building")):
        criteria["building"] = building
    return criteria


def _lead_priority(
    enquiry: CrmInboundEnquiry,
    enrichment: InboundOwnershipEnrichmentResponse,
) -> InboundPriorityResponse:
    """Rank commercial follow-up priority without presenting it as a probability."""
    criteria = enquiry.criteria or {}
    opportunity_value = (
        _amount(
            criteria.get("expected_price_aed")
            or criteria.get("budget_max_aed")
            or criteria.get("budget_min_aed")
            or 0
        )
        or 0
    )
    score = 18 if enquiry.record_kind == "opportunity" and enquiry.intent != "unknown" else 8
    reasons: list[str] = []

    if opportunity_value:
        if enquiry.intent in {"rent", "let"}:
            value_points = (
                24
                if opportunity_value >= 1_000_000
                else 19
                if opportunity_value >= 500_000
                else 14
                if opportunity_value >= 300_000
                else 9
                if opportunity_value >= 150_000
                else 4
            )
            reasons.append(f"AED {opportunity_value:,} stated annual rental requirement")
        else:
            value_points = (
                24
                if opportunity_value >= 20_000_000
                else 19
                if opportunity_value >= 10_000_000
                else 14
                if opportunity_value >= 5_000_000
                else 9
                if opportunity_value >= 2_000_000
                else 4
            )
            reasons.append(f"AED {opportunity_value:,} stated transaction requirement")
        score += value_points
    else:
        reasons.append("Opportunity value has not been qualified")

    if enrichment.matched:
        reasons.extend(enrichment.reasons)
        score += 20 if enrichment.verification == "contact_exact" else 5
        score += 10 if enrichment.current_owner else 0
        score += 6 if enrichment.former_owner else 0
        score += 8 if enrichment.rental_owner else 0
        score += 8 if enrichment.multiple_property_owner else 0
        if enrichment.observed_portfolio_value_aed >= 20_000_000:
            score += 6
        elif enrichment.observed_portfolio_value_aed >= 5_000_000:
            score += 3
    else:
        reasons.append("No owner-registry relationship observed")

    score = min(score, 100)
    if enrichment.verification == "name_only":
        band = "medium"
    elif score >= 60 and enrichment.verification == "contact_exact":
        band = "high"
    elif score >= 40:
        band = "medium"
    else:
        band = "standard"
    return InboundPriorityResponse(
        band=band,
        score=score,
        opportunity_value_aed=opportunity_value,
        reasons=reasons,
    )


def _locations(criteria: dict[str, Any]) -> list[str]:
    value = criteria.get("locations", [])
    return (
        [str(item) for item in value] if isinstance(value, list) else [str(value)] if value else []
    )


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
