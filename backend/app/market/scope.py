from typing import Literal

ProjectFilterType = Literal["project", "master", "virtual_master"]
ProjectScopeType = Literal["project", "master", "virtual_master", "market"]

# Server-side market type filters — frontend passes market_type, never raw predicates.
# See analytics_architecture.md for taxonomy rationale.
MARKET_FILTERS: dict[str, dict] = {
    "residential_units": {
        "property_type_en": "Unit",
        "property_usage_en": "Residential",
        "trans_group_en": "Sales",
    },
    "villas": {
        "property_type_en": "Villa",
        "trans_group_en": "Sales",
    },
    "commercial_units": {
        "property_type_en": "Unit",
        "property_usage_en__in": ["Commercial", "Hospitality"],
        "trans_group_en": "Sales",
    },
    "bulk_building": {
        "property_type_en": "Building",
        "trans_group_en": "Sales",
    },
    "land": {
        "property_type_en": "Land",
        "trans_group_en": "Sales",
    },
    "mortgages": {"trans_group_en": "Mortgages"},
    "gifts": {"trans_group_en": "Gifts"},
}


def project_filter_clause(filter_type: ProjectScopeType) -> str:
    """WHERE clause condition for a given filter type.

    master: matches rows with master_project_en = X OR orphaned rows whose
    project_name_en starts with X + ' - ' (absorbs inconsistently-linked sub-projects).
    """
    if filter_type == "market":
        return "1 = 1"
    if filter_type == "master":
        return """(master_project_en = {project_name:String}
            OR (lower(project_name_en) LIKE concat(lower({project_name:String}), ' - %')
                AND (master_project_en = '' OR master_project_en IS NULL)))"""
    if filter_type == "virtual_master":
        return "project_name_en LIKE {project_name:String}"
    return "project_name_en = {project_name:String}"


# Location predicate over canonical market rows (ch_transactions / ch_rent_contracts), whose
# community_key and district_name come from Holocron's evidence crosswalk; location_name is
# the uniform display location (DXBI's community name), which also places DXBI-only rows the
# crosswalk cannot. Terms come from market.reference.community_location.
LOCATION_PREDICATE = (
    "(community_key IN {loc_communities:Array(String)}"
    " OR replaceRegexpAll(lowerUTF8(trimBoth(location_name)), '[[:space:]]+', ' ')"
    " IN {loc_communities:Array(String)}"
    " OR lowerUTF8(district_name) IN {loc_districts:Array(String)})"
)
# The same terms over the DLD project register (ch_projects), which names the official
# district and master project directly.
PROJECT_LOCATION_PREDICATE = (
    "(replaceRegexpAll(lowerUTF8(trimBoth(master_project_en)), '[[:space:]]+', ' ')"
    " IN {loc_communities:Array(String)}"
    " OR lowerUTF8(area_name_en) IN {loc_districts:Array(String)})"
)


def location_parameters(location) -> dict[str, list[str]]:
    return {
        "loc_communities": list(location.communities),
        "loc_districts": list(location.districts),
    }
