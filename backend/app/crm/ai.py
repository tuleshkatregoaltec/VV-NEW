from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.core.observability import operation, provider_request
from app.crm.import_parsing import _CANONICAL_FIELDS, SheetProfile
from app.crm.inbound_policy import _ALIASES

logger = logging.getLogger(__name__)


@operation
async def _llm_mapping(profile: SheetProfile) -> SheetProfile:
    prompt = {
        "task": "Map spreadsheet headers to canonical CRM owner-import fields.",
        "privacy": "Headers only. No owner names, phone numbers, emails, or row values are included.",
        "canonical_fields": sorted(_CANONICAL_FIELDS),
        "headers": [header.key for header in profile.headers],
        "heuristic_mapping": profile.field_map,
        "requirements": [
            "Return JSON only.",
            "Only use canonical fields and exact supplied header names.",
            "Do not invent a unit number when no unit column exists.",
        ],
        "response_shape": {
            "field_map": {"canonical_field": "exact header"},
            "sheet_family": "transaction_party or contact_list",
            "confidence": "number from 0 to 1",
            "warnings": ["string"],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            response = await provider_request(
                client,
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEFAULT_CHAT_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You map spreadsheet schemas. Return a single valid JSON object "
                                "and never infer or request personal data."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": 900,
                    "provider": {"zdr": True},
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            proposal = json.loads(content)
    except Exception:
        logger.warning(
            "CRM import schema mapping fell back to heuristics",
            extra={"sheet": profile.name},
            exc_info=True,
        )
        profile.mapping_source = "heuristic_fallback"
        profile.warnings.append("Automated schema confirmation was unavailable.")
        return profile

    available = {header.key for header in profile.headers}
    llm_map = proposal.get("field_map") if isinstance(proposal, dict) else {}
    if isinstance(llm_map, dict):
        for canonical, header in llm_map.items():
            if canonical in _CANONICAL_FIELDS and header in available:
                profile.field_map.setdefault(canonical, header)
    family = proposal.get("sheet_family")
    if family in {"transaction_party", "contact_list"} and profile.field_map.get("party_role"):
        profile.sheet_family = family
    confidence = proposal.get("confidence")
    if isinstance(confidence, int | float):
        profile.confidence = max(0.0, min(1.0, float(confidence)))
    warnings = proposal.get("warnings")
    if isinstance(warnings, list):
        profile.warnings.extend(str(item)[:300] for item in warnings if item)
    profile.mapping_source = "llm"
    return profile


@operation
async def _ai_extract(notes: str, known: dict[str, Any]) -> tuple[dict[str, Any], float, list[str]]:
    if not notes or not settings.OPENROUTER_API_KEY:
        return {}, 0.0, []
    prompt = {
        "task": "Extract a Dubai real-estate enquiry from redacted text.",
        "text": notes,
        "already_extracted": known,
        "allowed_intents": ["buy", "rent", "sell", "let"],
        "output": {
            "intent": "string or null",
            "criteria": {
                "locations": ["strings"],
                "property_type": "string or null",
                "bedrooms_min": "integer or null",
                "bedrooms_max": "integer or null",
                "budget_min_aed": "integer or null",
                "budget_max_aed": "integer or null",
                "timeline": "string or null",
                "financing": "string or null",
            },
            "strict_fields": ["criteria keys explicitly stated as hard requirements"],
            "confidence": "0 to 1",
        },
        "rules": ["Return JSON only", "Use null for unknown values", "Do not infer identity"],
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await provider_request(
                client,
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEFAULT_CHAT_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Extract structured property demand. Never invent missing facts.",
                        },
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": 500,
                    "provider": {"zdr": True, "data_collection": "deny"},
                },
            )
            response.raise_for_status()
            payload = json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception:
        return {}, 0.0, []
    criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    criteria = {key: value for key, value in criteria.items() if value not in (None, "", [])}
    if payload.get("intent"):
        criteria["_intent"] = payload["intent"]
    confidence = float(payload.get("confidence") or 0)
    strict = [str(item) for item in payload.get("strict_fields", []) if item]
    return criteria, max(0.0, min(1.0, confidence)), strict


@operation
async def _ai_header_mapping(headers: list[str], campaign_context: str) -> dict[str, str]:
    if not headers or not settings.OPENROUTER_API_KEY:
        return {}
    prompt = {
        "task": "Map spreadsheet columns to a real-estate lead import schema.",
        "headers": headers,
        "source_context": campaign_context,
        "allowed_fields": list(_ALIASES),
        "rules": [
            "Return JSON with a field_map object only",
            "Keys must be allowed_fields",
            "Values must exactly match one supplied header",
            "Do not map uncertain columns",
            "Campaign context describes the source and must not be treated as individual intent",
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await provider_request(
                client,
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEFAULT_CHAT_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Map headers conservatively. Never invent columns or lead facts.",
                        },
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": 600,
                    "provider": {"zdr": True, "data_collection": "deny"},
                },
            )
            response.raise_for_status()
            payload = json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception:
        return {}
    proposed = payload.get("field_map", {})
    return {
        canonical: header
        for canonical, header in proposed.items()
        if canonical in _ALIASES and header in headers
    }
