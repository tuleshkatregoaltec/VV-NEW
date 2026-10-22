"""Recover property identity from a registry-backed file location and sale evidence."""

from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from app.crm import owner_queries
from app.crm.identity import (
    ParsedContact,
    _location_key,
    _location_tokens,
    _normalize_unit,
    _usable_project_name,
)
from app.crm.import_events import has_sale_evidence


def property_details(evidence: dict) -> dict[str, str]:
    """Expose only property evidence, never raw contact fields, to report rows."""
    fields = evidence.get("source_fields", {})
    details = {
        f"source_{field}": str(fields.get(field) or "")
        for field in (
            "area_name",
            "master_project",
            "project_name",
            "building_name",
            "unit_number",
            "property_type",
            "completion_status",
            "land_number",
            "building_number",
        )
    }
    resolution = evidence.get("property_identity_resolution", {})
    observed = evidence.get("sales_match", {})
    details.update(
        file_location=", ".join(evidence.get("file_context", {}).get("locations", [])),
        registry_property_type=str(observed.get("property_type") or ""),
        registry_completion_status=str(observed.get("market_status") or ""),
        identity_method=str(resolution.get("method") or ""),
        matched_sale_date=str(resolution.get("resolved", {}).get("transaction_date") or ""),
    )
    return details


def _tokens(value: str) -> tuple[str, ...]:
    return _location_tokens(value.replace("_", " "))


def _contains(container: tuple[str, ...], part: tuple[str, ...]) -> bool:
    return bool(part) and any(
        container[i : i + len(part)] == part for i in range(len(container) - len(part) + 1)
    )


async def prepare_file_context(candidates: list[ParsedContact]) -> None:
    filenames = {c.evidence.get("source_filename", "") for c in candidates} - {""}
    if not candidates:
        return
    locations = await owner_queries.fetch_import_locations()
    names = {str(r["name"]) for r in locations if _usable_project_name(str(r["name"]))}
    names_by_tokens = defaultdict(list)
    related_locations = set()
    for row in locations:
        area, project = _tokens(row.get("area_name", "")), _tokens(row.get("project_name", ""))
        if area and project:
            related_locations.add(frozenset((area, project)))
    for name in sorted(names):
        names_by_tokens[_tokens(name)].append(name)
    contexts = {}
    for filename in filenames:
        tokens = _tokens(Path(filename).stem)
        matches = [
            name for name in names if len(_tokens(name)) >= 2 and _contains(tokens, _tokens(name))
        ]
        # "Damac Hills" is a different community, not an alias of "Damac Hills 2".
        matches = [
            name
            for name in matches
            if not any(
                _tokens(name) != _tokens(other) and _contains(_tokens(other), _tokens(name))
                for other in matches
            )
        ]
        # Several independent locations in a filename cannot establish one scope.
        if len({_tokens(name) for name in matches}) == 1:
            contexts[filename] = {"locations": sorted(matches), "source": "filename"}
    for candidate in candidates:
        file_context = contexts.get(candidate.evidence.get("source_filename", ""))
        fields = candidate.evidence.get("source_fields", {})
        context = None
        # Exact source entities take precedence over a filename that may contain
        # several communities. Never turn "Damac Hills 1591" into "Damac Hills".
        for field in ("project_name", "master_project", "area_name"):
            value = _tokens(str(fields.get(field) or ""))
            matches = names_by_tokens.get(value, [])
            if matches:
                context = {"locations": matches, "source": field}
                break
        if context and file_context and context["locations"] != file_context["locations"]:
            context["filename_locations"] = file_context["locations"]
            if any(
                frozenset((_tokens(source), _tokens(filename))) in related_locations
                for source in context["locations"]
                for filename in file_context["locations"]
            ):
                # A legal area and marketed project may both be valid. Expand
                # only when the warehouse itself supplies their relationship.
                context["locations"] = sorted(set(context["locations"] + file_context["locations"]))
                context["scope_relationship"] = "registry_area_project"
        context = context or file_context
        if context:
            candidate.evidence["file_context"] = dict(context)


def in_file_context(candidate: ParsedContact, row: dict) -> bool:
    names = candidate.evidence.get("file_context", {}).get("locations", [])
    return not names or any(
        _tokens(str(row.get(field) or "")) == _tokens(name)
        for field in ("area_name", "project_name")
        for name in names
    )


def _compatible_details(candidate: ParsedContact, row: dict) -> bool:
    fields = candidate.evidence.get("source_fields", {})
    source_units = {
        _normalize_unit(value)
        for value in (
            candidate.unit_number,
            str(candidate.evidence.get("source_unit_number") or ""),
        )
    } - {"", "0"}
    if source_units and _normalize_unit(row.get("unit_number")) not in source_units:
        return False
    # Broad DLD legal categories such as Residential/Land do not distinguish villas
    # from apartments. Only explicit physical types may exclude a candidate.
    source_type = str(fields.get("property_type") or "").casefold().strip()
    observed_type = str(row.get("property_type") or "").casefold().strip()
    types = {
        "villa": {"villa", "townhouse"},
        "villas": {"villa", "townhouse"},
        "townhouse": {"villa", "townhouse"},
        "townhouses": {"villa", "townhouse"},
        "apartment": {"apartment"},
        "apartments": {"apartment"},
        "flat": {"apartment"},
        "flats": {"apartment"},
    }
    if source_type in types and observed_type and observed_type not in types[source_type]:
        return False
    statuses = {
        "offplan": "offplan",
        "off plan": "offplan",
        "off-plan": "offplan",
        "ready": "ready",
        "completed": "ready",
    }
    source_status = statuses.get(str(fields.get("completion_status") or "").casefold().strip())
    observed_status = statuses.get(str(row.get("market_status") or "").casefold().strip())
    if source_status and observed_status and source_status != observed_status:
        return False
    # Compare project/building details with the whole observed hierarchy. A legal
    # area can differ from the marketed community and is not a contradiction.
    for field in ("building_name", "project_name"):
        value = _usable_project_name(str(fields.get(field) or ""))
        if not value:
            continue
        if field == "building_name" and _normalize_unit(value) in source_units:
            # Some villa exports repeat the property code under BuildingName 2.
            # It has already been checked as an identifier, not a location name.
            continue
        source_tokens = _tokens(value)
        hierarchy = tuple(
            token
            for observed in ("area_name", "project_name", "building_name")
            for token in _tokens(str(row.get(observed) or ""))
        )
        if not _contains(hierarchy, source_tokens) and not any(
            _contains(_tokens(str(row.get(observed) or "")), source_tokens)
            for observed in ("area_name", "project_name", "building_name")
        ):
            return False
    return True


async def recover_from_file_context(candidates: list[ParsedContact]) -> None:
    """Exact amount + bounded date + one property in the file's registry location.

    Query only the source date range, then load the identified property's full
    history through the regular sales resolver. Multiple properties remain unresolved.
    A shifted date is explicitly provisional even when only one property fits.
    """
    groups = defaultdict(list)
    for candidate in candidates:
        if candidate.evidence.get("property_identity_resolution"):
            continue
        locations = candidate.evidence.get("file_context", {}).get("locations", [])
        if not has_sale_evidence(candidate):
            reason = "This source event is not a sale acquisition/disposal; its amount cannot identify a purchase."
        elif not candidate.transaction_date or not candidate.transaction_price_aed:
            reason = "No usable sale date and amount are available for transaction matching."
        elif not locations:
            reason = "No unique registry community/project could be identified from the source fields or filename."
        else:
            groups[tuple(locations)].append(candidate)
            continue
        candidate.evidence["context_lookup"] = {"status": "not_eligible", "reason": reason}

    for locations, group in groups.items():
        dates = [c.transaction_date for c in group if c.transaction_date]
        rows = await owner_queries.fetch_import_context_sales(
            {
                "locations": list(locations),
                "start_date": min(dates) - timedelta(days=14),
                "end_date": max(dates) + timedelta(days=14),
            }
        )
        by_price = defaultdict(list)
        for row in rows:
            if row.get("unit_number") and (row.get("building_name") or row.get("project_name")):
                by_price[int(row.get("sale_amount_aed") or 0)].append(row)
        for candidate in group:
            nearby = [
                row
                for row in by_price.get(candidate.transaction_price_aed, [])
                if abs((row["transaction_date"] - candidate.transaction_date).days) <= 14
            ]
            # An unrelated exact-date sale must not hide a compatible sale a few
            # days away. Apply supplied property details before choosing the window.
            compatible = [row for row in nearby if _compatible_details(candidate, row)]
            exact = [
                row for row in compatible if row["transaction_date"] == candidate.transaction_date
            ]
            pool = exact or nearby
            fitting = exact or compatible
            properties = defaultdict(list)
            for row in fitting:
                key = (
                    row.get("area_key"),
                    row.get("building_key")
                    or _location_key(row.get("building_name") or row.get("project_name") or ""),
                    row["unit_key"],
                )
                properties[key].append(row)
            count = len(properties)
            lookup = {
                "status": "matched" if count == 1 else "ambiguous" if count > 1 else "no_match",
                "property_candidates": count,
                "window_days": 0 if exact else 14,
                "locations": list(locations),
                "reason": (
                    f"{count} properties match the sale date/amount and available details in {locations[0]}; no unit was guessed."
                    if count > 1
                    else f"Sale candidates in {locations[0]} contradict the supplied unit, project, property type or completion status."
                    if pool and not fitting
                    else f"No sale with this amount within 14 days was found in available {locations[0]} sales coverage."
                ),
            }
            candidate.evidence["context_lookup"] = lookup
            if count != 1:
                continue
            events = next(iter(properties.values()))
            selected = min(
                events, key=lambda r: abs((r["transaction_date"] - candidate.transaction_date).days)
            )
            # A repeated same-price sale on different dates cannot identify which
            # acquisition is meant solely by proximity.
            if not exact and len({r["transaction_date"] for r in events}) > 1:
                lookup.update(
                    status="ambiguous",
                    reason="Several sale dates fit the same property and amount; the acquisition date needs review.",
                )
                continue
            source = {
                field: getattr(candidate, field)
                for field in ("area_name", "project_name", "building_name", "unit_number")
            }
            candidate.area_name = str(selected.get("area_name") or "")
            candidate.project_name = str(selected.get("project_name") or "")
            candidate.building_name = str(selected.get("building_name") or candidate.project_name)
            candidate.unit_number = _normalize_unit(selected["unit_number"])
            candidate.evidence["property_identity_resolution"] = {
                "method": "file_context_transaction_signature",
                "score": 90 if exact else 80,
                "date_delta_days": abs(
                    (selected["transaction_date"] - candidate.transaction_date).days
                ),
                "transaction_candidate_count": count,
                "source": source,
                "resolved": {
                    **{field: getattr(candidate, field) for field in source},
                    "sale_key": str(selected.get("sale_key") or ""),
                    "transaction_date": selected["transaction_date"].isoformat(),
                    "property_type": str(selected.get("property_type") or ""),
                    "market_status": str(selected.get("market_status") or ""),
                },
            }
            lookup["reason"] = (
                "One property matches the file location, sale amount, date window and supplied details."
            )
    _reject_conflicting_source_matches(candidates)


def _reject_conflicting_source_matches(candidates: list[ParsedContact]) -> None:
    """Warehouse uniqueness is insufficient when the source describes several properties.

    A partial registry may expose only one of a batch of same-price sales. Never
    assign that observed unit to every source plot, or merge several source sale
    dates into its one acquisition. Direct unit identifiers remain independent
    evidence; only identities inferred without a source unit are withdrawn.
    """
    by_sale = defaultdict(list)
    for candidate in candidates:
        resolution = candidate.evidence.get("property_identity_resolution", {})
        sale_key = resolution.get("resolved", {}).get("sale_key")
        if sale_key:
            by_sale[sale_key].append(candidate)
    for sale_key, group in by_sale.items():
        dates = {c.transaction_date for c in group if c.transaction_date}
        lands = {
            _normalize_unit(c.evidence.get("source_fields", {}).get("land_number")) for c in group
        } - {"", "0"}
        if len(dates) <= 1 and len(lands) <= 1:
            continue
        for candidate in group:
            resolution = candidate.evidence["property_identity_resolution"]
            source = resolution.get("source", {})
            if _normalize_unit(source.get("unit_number")) not in {"", "0"}:
                continue
            if not source:
                continue
            for field in ("area_name", "project_name", "building_name", "unit_number"):
                setattr(candidate, field, str(source.get(field) or ""))
            candidate.evidence.pop("property_identity_resolution", None)
            candidate.evidence["context_lookup"] = {
                "status": "source_conflict",
                "rejected_sale_key": sale_key,
                "source_land_numbers": len(lands),
                "source_sale_dates": len(dates),
                "reason": (
                    f"The same registry sale fits source rows with {len(lands)} land numbers "
                    f"and {len(dates)} sale dates. Without a unit identifier, these records "
                    "cannot safely be assigned to that one property."
                ),
            }
