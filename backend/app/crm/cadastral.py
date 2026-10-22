"""Resolve a plot independently of apartment unit and sales ownership evidence."""

import re

from app.crm import owner_queries
from app.crm.identity import ParsedContact, _location_key, _normalize_unit, _usable_project_name


def _land_number(value) -> str:
    value = _normalize_unit(value)
    return "" if value in {"", "0"} else value


def _select_plot(candidate: ParsedContact, rows: list[dict]) -> dict | None:
    fields = candidate.evidence.get("source_fields", {})
    master = _location_key(_usable_project_name(fields.get("master_project", "")))
    area = _location_key(_usable_project_name(fields.get("area_name", "")))
    project = _location_key(_usable_project_name(fields.get("project_name", "")))
    context = {
        _location_key(name)
        for name in candidate.evidence.get("file_context", {}).get("locations", [])
    }
    sub = candidate.evidence.get("source_identifiers", {}).get("land_sub_number", "")
    sub = _normalize_unit(sub)
    # A land number alone is not globally unique.
    if not master and not area and not context:
        return None
    matches = {}
    for row in rows:
        # Some exports place the exact cluster/project name under Master Project.
        # Match either hierarchy level exactly; never strip changing numeric suffixes.
        hierarchy = {
            _location_key(row.get("master_project_en", "")),
            _location_key(row.get("project_en", "")),
            _location_key(row.get("area_en", "")),
        }
        # The validated source/file context can supply a missing master project.
        # Still require the source legal area and sub-project when they exist.
        if context and not context.intersection(hierarchy):
            continue
        if (
            context
            and master not in context
            and any(master.startswith(scope) for scope in context)
            and master not in hierarchy
        ):
            # A supplied cluster such as DAMAC HILLS (2) - VARDON is more
            # specific than its master project and must agree with the plot.
            continue
        if not context and master and master not in hierarchy:
            continue
        if area and area != _location_key(row.get("area_en", "")):
            continue
        if project and project != master and project != _location_key(row.get("project_en", "")):
            continue
        observed_sub = _normalize_unit(row.get("land_sub_number")) or "0"
        if sub and sub != observed_sub:
            continue
        if not row.get("area_id"):
            continue
        key = (row["area_id"], _land_number(row["land_number"]), observed_sub)
        matches[key] = row
    return next(iter(matches.values())) if len(matches) == 1 else None


async def resolve_cadastral_plots(candidates: list[ParsedContact]) -> None:
    eligible = [
        c
        for c in candidates
        if not c.unit_number
        and _land_number(c.evidence.get("source_identifiers", {}).get("land_number"))
    ]
    numbers = sorted(
        {_land_number(c.evidence["source_identifiers"]["land_number"]) for c in eligible}
    )
    by_number = {}
    for start in range(0, len(numbers), 2000):
        for row in await owner_queries.fetch_cadastral_lands(
            {"land_numbers": numbers[start : start + 2000]}
        ):
            by_number.setdefault(_land_number(row["land_number"]), []).append(row)
    for candidate in eligible:
        number = _land_number(candidate.evidence["source_identifiers"]["land_number"])
        plot = _select_plot(candidate, by_number.get(number, []))
        if plot is None:
            continue
        sub = _normalize_unit(plot.get("land_sub_number")) or "0"
        candidate.evidence["cadastral_identity"] = {
            "kind": "land",
            "method": "exact_land_and_location",
            "property_key": f"dld-land:{plot['area_id']}:{number}:{sub}",
            "area_id": plot["area_id"],
            "area_name": plot["area_en"],
            "land_number": number,
            "land_sub_number": sub,
            # DLD sales record a villa under its plot's pre-registration code.
            "villa_code": str(plot.get("pre_registration_number") or ""),
            "master_project": plot.get("master_project_en", ""),
            "project": plot.get("project_en", ""),
            "ownership_verified": False,
        }


def _villa_code_variants(value) -> set[str]:
    """Sales keys for a pre-registration code, e.g. DH2-XV728B -> dh2-xv728b, xv728b."""
    code = re.sub(r"\s+", "", str(value or "")).lower()
    if code in {"", "0"}:
        return set()
    variants = {code}
    _, _, bare = code.partition("-")
    # A short remainder (e.g. "101") would collide with ordinary apartment numbers.
    if len(bare) >= 5 and re.search(r"[a-z]", bare) and re.search(r"\d", bare):
        variants.add(bare)
    return variants


def _same_community(identity: dict, sale: dict) -> bool:
    plot = {
        _location_key(identity.get(field) or "")
        for field in ("master_project", "project", "area_name")
    } - {""}
    observed = {_location_key(sale.get(field) or "") for field in ("area_name", "project_name")}
    return bool(plot & observed)


async def villa_sales_for_plots(candidates: list[ParsedContact]) -> dict[str, list[dict]]:
    """Sales history of each resolved plot's villa, keyed by the plot property key."""
    plots = [
        c
        for c in candidates
        if not c.unit_number and c.evidence.get("cadastral_identity", {}).get("villa_code")
    ]
    keys = sorted(
        {
            variant
            for c in plots
            for variant in _villa_code_variants(c.evidence["cadastral_identity"]["villa_code"])
        }
    )
    by_key: dict[str, list[dict]] = {}
    for start in range(0, len(keys), 2000):
        for row in await owner_queries.fetch_unit_sales({"unit_keys": keys[start : start + 2000]}):
            by_key.setdefault(str(row["unit_key"]), []).append(row)
    sales: dict[str, list[dict]] = {}
    for candidate in plots:
        identity = candidate.evidence["cadastral_identity"]
        events = {}
        for variant in _villa_code_variants(identity["villa_code"]):
            for row in by_key.get(variant, []):
                if _same_community(identity, row):
                    # The same sale can be listed under both code forms.
                    events[(row["transaction_date"], row["sale_amount_aed"])] = row
        if events:
            sales[candidate.property_key] = sorted(
                events.values(), key=lambda row: row["transaction_date"]
            )
    return sales
