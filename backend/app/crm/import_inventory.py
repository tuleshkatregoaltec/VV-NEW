"""Source identifier counts, independent of registry matching or ownership."""

import re
from dataclasses import dataclass, field

from app.crm.identity import _location_key, _normalize_unit, _usable_project_name
from app.crm.import_models import CrmSourcePropertyCounts


def source_identifier(value: str) -> str:
    value = _normalize_unit(value)
    return "" if value.casefold() in {"", "0", "nan"} else value.casefold()


@dataclass
class SourcePropertyInventory:
    units: set[str] = field(default_factory=set)
    buildings: set[str] = field(default_factory=set)
    scoped_units: set[tuple[str, str]] = field(default_factory=set)
    property_numbers: set[str] = field(default_factory=set)
    references: set[tuple[str, str]] = field(default_factory=set)
    total_rows: int = 0
    unit_rows: int = 0
    building_fallback_rows: int = 0
    rows_without_identifiers: int = 0
    unit_rows_without_location: int = 0

    def add(self, fields: dict[str, str]) -> None:
        self.total_rows += 1
        unit = source_identifier(fields.get("unit_number", ""))
        building = source_identifier(fields.get("building_number", ""))
        property_number = source_identifier(fields.get("property_number", ""))
        location = (
            _usable_project_name(fields.get("building_name", ""))
            or _usable_project_name(fields.get("project_name", ""))
            or _usable_project_name(fields.get("master_project", ""))
        )
        identifier = unit or property_number or building
        if not unit and property_number:
            self.property_numbers.add(property_number)
        # "Building 1" can contain a cluster name, not a villa number. Preserve
        # literal column counts below, but do not count that name as one property.
        if identifier and (
            unit or property_number or any(character.isdigit() for character in identifier)
        ):
            if not unit and re.search(r"[a-z]", identifier):
                self.references.add(("", identifier))
            elif location:
                self.references.add((_location_key(location), identifier))
        if unit:
            self.units.add(unit)
            self.unit_rows += 1
            location = _usable_project_name(fields.get("building_name", "")) or (
                _usable_project_name(fields.get("project_name", ""))
            )
            if location:
                # Preserve tower/project scope: apartment 101 is not globally unique.
                self.scoped_units.add((_location_key(location), unit))
            else:
                self.unit_rows_without_location += 1
        elif building:
            self.buildings.add(building)
            self.building_fallback_rows += 1
        else:
            self.rows_without_identifiers += 1

    def merge(self, other: "SourcePropertyInventory") -> None:
        for name in ("units", "buildings", "scoped_units", "property_numbers", "references"):
            getattr(self, name).update(getattr(other, name))
        for name in (
            "total_rows",
            "unit_rows",
            "building_fallback_rows",
            "rows_without_identifiers",
            "unit_rows_without_location",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def summary(self) -> CrmSourcePropertyCounts:
        return CrmSourcePropertyCounts(
            total_rows=self.total_rows,
            distinct_unit_numbers=len(self.units),
            distinct_building_numbers_without_unit=len(self.buildings),
            distinct_identifiers=len(self.units | self.buildings),
            distinct_property_numbers_without_unit=len(self.property_numbers),
            source_property_references=len(self.references),
            identifiers_in_both_columns=len(self.units & self.buildings),
            location_scoped_units=len(self.scoped_units),
            unit_rows=self.unit_rows,
            building_fallback_rows=self.building_fallback_rows,
            rows_without_identifiers=self.rows_without_identifiers,
            unit_rows_without_location=self.unit_rows_without_location,
        )
