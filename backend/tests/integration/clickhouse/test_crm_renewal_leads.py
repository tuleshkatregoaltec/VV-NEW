"""Renewal leads (requirements 9 Oct 2026): Leads inbox = Renewals filtered by area and window.

The JVC example: an agent on JVC with 60 days forward and 30 days back sees JVC leases ending
in the next 60 days or ended in the last 30, never another area's, while Assets › Renewals
keeps every lease regardless of any agent's settings.
"""

from datetime import date, datetime, timedelta, timezone

from app.crm import opportunity_queries, renewal_leads

AS_OF = date(2026, 10, 9)
JVC = "Jumeirah Village Circle"
ALL_LEASES = dict(
    window=None,
    area=None,
    property_type=None,
    project=None,
    building=None,
    limit=50,
    offset=0,
    as_of=AS_OF,
)
CANDIDATE_COLUMNS = [
    "contract_key",
    "unit_candidate_key",
    "unit_cohort_key",
    "area_name",
    "building_name",
    "property_type",
    "bedrooms",
    "annual_rent_aed",
    "lease_start",
    "lease_end",
    "last_scraped_at",
    "unit_number",
]


def _lease(key, unit, area, days_from_as_of, *, start_days=-365, unit_number=None):
    end = AS_OF + timedelta(days=days_from_as_of)
    return [
        key,
        unit,
        f"cohort-{unit}",
        area,
        "Tower",
        "Apartment",
        "1 Bed",
        80_000,
        end + timedelta(days=start_days),
        end,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        f"U-{unit}" if unit_number is None else unit_number,
    ]


async def _install(install_serving, warehouse):
    await install_serving(
        tables=(
            "dxbi_rental_events",
            "dxbi_rental_unit_candidates",
            "dxbi_sales_unit_events",
            "dxbi_rental_sale_matches",
        ),
        views=(),
    )
    # Production builds this as a view over the geography tables; leases here are unmapped.
    await warehouse.command("""CREATE TABLE geo_crm_rental_hierarchy (
        registry_area_name String, registry_project_name String, registry_building_name String,
        source_grain String, display_building_name String, display_project_name String,
        display_area_name String, canonical_location_id String, match_status String,
        coordinate_precision String, latitude Nullable(Float64), longitude Nullable(Float64)
    ) ENGINE = MergeTree ORDER BY registry_area_name""")
    await warehouse.insert(
        "dxbi_rental_unit_candidates",
        [
            _lease("jvc-ends-day-60", "unit-a", JVC, 60),
            # An older lease on the same unit must not become a second inbox entry.
            _lease("jvc-unit-a-previous", "unit-a", JVC, -305, start_days=-730),
            _lease("jvc-ended-day-30", "unit-b", JVC, -30),
            _lease("jvc-ends-day-61", "unit-c", JVC, 61),
            _lease("jvc-ended-day-31", "unit-d", JVC, -31),
            _lease("marina-ends-day-10", "unit-e", "Dubai Marina", 10),
        ],
        column_names=CANDIDATE_COLUMNS,
    )
    # unit-b is matched to one exact unit bought long before its lease, so once its lease ends
    # with no later lease registered it is a vacant (unlet) unit.
    await warehouse.insert(
        "dxbi_rental_sale_matches",
        [["unit-b", "U-unit-b", date(2020, 1, 1), "unique"]],
        column_names=["unit_candidate_key", "unit_number", "last_purchase_date", "match_status"],
    )


async def test_jvc_agent_sees_only_jvc_leases_inside_both_windows(install_serving, warehouse):
    await _install(install_serving, warehouse)

    rows, counts = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=50, offset=0
    )

    # Both window edges are inclusive; day 61 ahead and day 31 back are outside.
    assert [row["event_key"] for row in rows] == ["jvc-ended-day-30", "jvc-ends-day-60"]
    assert counts[0]["total"] == 2
    # The day-60 lease crossed into the forward window today.
    assert counts[0]["entered_today"] == 1


async def test_agent_without_the_area_gets_none_of_its_leases(install_serving, warehouse):
    await _install(install_serving, warehouse)

    rows, counts = await opportunity_queries.fetch_renewal_leads(
        AS_OF, ["Dubai Marina"], forward_days=60, lookback_days=30, limit=50, offset=0
    )

    assert [row["event_key"] for row in rows] == ["marina-ends-day-10"]
    assert counts[0]["total"] == 1


async def test_windows_are_configurable_not_fixed_to_the_example(install_serving, warehouse):
    await _install(install_serving, warehouse)

    rows, _ = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=90, lookback_days=0, limit=50, offset=0
    )

    assert [row["event_key"] for row in rows] == ["jvc-ends-day-60", "jvc-ends-day-61"]


async def test_assets_renewals_keeps_every_lease_regardless_of_agent_settings(
    install_serving, warehouse
):
    await _install(install_serving, warehouse)

    renewals = await renewal_leads.list_renewals(**ALL_LEASES)

    assert {lead.lead_id for lead in renewals.leads} == {
        "jvc-ends-day-60",
        "jvc-ended-day-30",
        "jvc-ends-day-61",
        "jvc-ended-day-31",
        "marina-ends-day-10",
    }
    assert renewals.total == 5


async def test_area_picker_lists_dataset_areas_with_upcoming_counts(install_serving, warehouse):
    await _install(install_serving, warehouse)

    rows = await opportunity_queries.fetch_renewal_areas(AS_OF)

    assert [(row["area"], row["upcoming_90_days"]) for row in rows] == [
        ("Dubai Marina", 1),
        (JVC, 2),
    ]


async def test_lease_registered_under_another_area_name_follows_its_display_area(
    install_serving, warehouse
):
    await _install(install_serving, warehouse)
    # DLD registers part of JVC as "Al Barsha South Fourth"; the geography crosswalk shows it as
    # JVC, the name the agent picked, so narrowing leases by area early must not drop it.
    await warehouse.insert(
        "dxbi_rental_unit_candidates",
        [_lease("jvc-registry-name", "unit-f", "Al Barsha South Fourth", 5)],
        column_names=CANDIDATE_COLUMNS,
    )
    await warehouse.insert(
        "geo_crm_rental_hierarchy",
        [
            [
                "Al Barsha South Fourth",
                "",
                "Tower",
                "building",
                "",
                "",
                JVC,
                "loc-1",
                "approved",
                "building",
                None,
                None,
            ]
        ],
    )

    rows, counts = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=50, offset=0
    )

    assert "jvc-registry-name" in [row["event_key"] for row in rows]
    assert counts[0]["total"] == 3


async def test_a_page_reports_the_total_of_every_match_not_just_its_rows(
    install_serving, warehouse
):
    await _install(install_serving, warehouse)

    rows, counts = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=1, offset=0
    )

    assert len(rows) == 1
    assert (counts[0]["total"], counts[0]["entered_today"]) == (2, 1)


async def test_a_lease_without_a_unit_number_never_becomes_a_lead(install_serving, warehouse):
    await _install(install_serving, warehouse)
    await warehouse.insert(
        "dxbi_rental_unit_candidates",
        [_lease("jvc-no-unit-number", "unit-g", JVC, 20, unit_number="")],
        column_names=CANDIDATE_COLUMNS,
    )

    rows, _ = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=50, offset=0
    )

    assert "jvc-no-unit-number" not in [row["event_key"] for row in rows]


async def test_vacant_units_for_leads_and_running_leases_for_the_inbox(install_serving, warehouse):
    await _install(install_serving, warehouse)

    vacant, _ = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=50, offset=0, kind="vacant"
    )
    upcoming, _ = await opportunity_queries.fetch_renewal_leads(
        AS_OF, [JVC], forward_days=60, lookback_days=30, limit=50, offset=0, kind="upcoming"
    )

    assert [row["event_key"] for row in vacant] == ["jvc-ended-day-30"]
    assert [row["event_key"] for row in upcoming] == ["jvc-ends-day-60"]


async def test_assets_renewals_opens_on_vacant_units_of_the_last_60_days(
    install_serving, warehouse
):
    await _install(install_serving, warehouse)

    renewals = await renewal_leads.list_renewals(**{**ALL_LEASES, "window": "vacant"})

    # unit-d ended 31 days ago but its unit is not matched to one exact unit: not shown as vacant.
    assert [lead.lead_id for lead in renewals.leads] == ["jvc-ended-day-30"]
    assert renewals.total == 1


async def test_renewals_filters_narrow_by_area_and_list_projects_for_it(install_serving, warehouse):
    await _install(install_serving, warehouse)

    renewals = await renewal_leads.list_renewals(**{**ALL_LEASES, "area": "Dubai Marina"})

    assert [lead.lead_id for lead in renewals.leads] == ["marina-ends-day-10"]
    assert renewals.property_types == ["Apartment"]
    # Buildings are only listed once a project is picked.
    assert renewals.buildings == []


async def test_a_page_of_renewals_still_reports_the_full_total(install_serving, warehouse):
    await _install(install_serving, warehouse)

    renewals = await renewal_leads.list_renewals(**{**ALL_LEASES, "limit": 2})

    assert (len(renewals.leads), renewals.total) == (2, 5)
