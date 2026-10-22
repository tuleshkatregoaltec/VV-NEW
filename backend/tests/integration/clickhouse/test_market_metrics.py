"""Market metrics against the repository's canonical tables and serving views.

Expected numbers are worked out by hand from the fixture rows below, not produced by the
code under test. The tables come from Holocron's schema file and the views from the backend
serving script, so the test exercises the production definitions rather than fixture DDL.
Coverage comes from the served release's ledger entry, as Holocron publishes it.
"""

import json
from datetime import date, datetime

import pytest

from app.analytics.market_metrics import get_market_metrics
from app.analytics.models import MarketMetricsRequest

REFERENCE = date(2026, 10, 3)  # "last month" = September 2026
SQFT = 10.7639


SALE_COLUMNS = [
    "row_key", "transaction_id", "instance_date", "trans_group_en", "actual_worth",
    "procedure_area", "area_name_en", "master_project_en", "property_type_en",
    "property_sub_type_en", "property_usage_en", "rooms_en", "building_name_en",
    "reg_type_en", "source_system", "match_status", "is_served", "built_at", "release_id",
    "district_name", "community_key", "community_name", "geo_method",
    # Uniform layer columns, as Holocron writes them (size, one type list, display location).
    "size_sqm", "property_type", "location_name",
]  # fmt: skip
# Canonical places as Holocron's crosswalk assigns them (raw names stay in area/master).
JVC = ("Al Barsha South Fourth", "jumeirah village circle", "Jumeirah Village Circle")


def sale(key, when, group, price, area_sqm, area, master="", served=1, status="dld_only",
         source="dld_open_data", rooms="1 B/R", building="Tower A", geo=JVC,
         method="label_evidence"):  # fmt: skip
    return [
        key, key, when, group, price, area_sqm, area, master, "Unit", "Flat", "Residential",
        rooms, building, "Existing Properties", source, status, served,
        datetime(2026, 10, 2, 6, 0), "r-sales", *geo, method,
        area_sqm, "Apartment", geo[2] or geo[0],
    ]  # fmt: skip


SALES = [
    # Eligible September sales in JVC, in each source vocabulary.
    sale("S1", datetime(2026, 9, 1, 0, 0), "Sales", 1_000_000, 50, "Al Barsha South Fourth",
         "Jumeirah Village Circle", source="dubai_pulse"),
    sale("S2", datetime(2026, 9, 15, 12, 0), "Sales", 1_000_000, 100, "JUMEIRAH VILLAGE CIRCLE"),
    sale("S3", datetime(2026, 9, 30, 23, 59, 59), "Sales", 4_000_000, 100,
         "Al Barsha South Fourth", "Jumeirah Village Circle", status="matched"),
    # Not eligible: probable DXBI duplicate (unserved), mortgage, gift, zero value.
    sale("X1", datetime(2026, 9, 15, 12, 0), "Sales", 1_000_000, 100, "JUMEIRAH VILLAGE CIRCLE",
         served=0, status="dxbi_probable_duplicate", source="dxbi"),
    sale("X2", datetime(2026, 9, 10), "Mortgages", 2_000_000, 100, "JUMEIRAH VILLAGE CIRCLE"),
    sale("X3", datetime(2026, 9, 11), "Gifts", 3_000_000, 100, "JUMEIRAH VILLAGE CIRCLE"),
    sale("X4", datetime(2026, 9, 12), "Sales", 0, 100, "JUMEIRAH VILLAGE CIRCLE"),
    # Immediately outside September.
    sale("X5", datetime(2026, 8, 31, 23, 59, 59), "Sales", 9_000_000, 100,
         "JUMEIRAH VILLAGE CIRCLE"),
    sale("X6", datetime(2026, 10, 1, 0, 0), "Sales", 9_000_000, 100, "JUMEIRAH VILLAGE CIRCLE"),
    # September, another community; JBR inside DLD area Marsa Dubai is not Dubai Marina.
    sale("B1", datetime(2026, 9, 10), "Sales", 7_000_000, 100, "Business Bay", "Business Bay",
         rooms="2 B/R", building="Tower B",
         geo=("Business Bay", "business bay", "Business Bay"), method="pulse_master"),
    sale("J1", datetime(2026, 9, 10), "Mortgages", 5_000_000, 100, "Marsa Dubai",
         "Jumeriah Beach Residence  - JBR",
         geo=("Marsa Dubai", "jumeriah beach residence - jbr", "Jumeriah Beach Residence - JBR"),
         method="pulse_master"),
]  # fmt: skip

RENT_COLUMNS = [
    "row_key", "contract_id", "registration_date", "contract_start_date", "contract_end_date",
    "annual_amount", "contract_amount", "actual_area", "area_name_en", "master_project_en",
    "ejari_property_sub_type_en", "property_usage_en", "version_en", "bedrooms",
    "source_system", "match_status", "is_served", "built_at", "release_id",
    "district_name", "community_key", "community_name", "geo_method",
    "size_sqm", "property_type", "rooms_en", "location_name",
]  # fmt: skip
MARINA = ("Marsa Dubai", "dubai marina", "Dubai Marina", "project_evidence")
MARSA_DISTRICT_ONLY = ("Marsa Dubai", "", "", "district_name")


def rent(key, registered, start, end, annual, contract, version, bedrooms, area="Marsa Dubai",
         geo=MARINA):  # fmt: skip
    return [
        key, key, registered, start, end, annual, contract, 80.0, area, "", "Flat",
        "Residential", version, bedrooms, "dld_open_data", "matched" if bedrooms else "dld_only",
        1, datetime(2026, 10, 2, 6, 0), "r-rent", *geo,
        # Uniform bedroom label: DXBI's "2 Bed" / "2 Beds" are one label; unknown stays ''.
        80.0, "Apartment", f"{bedrooms.split()[0]} B/R" if bedrooms else "", geo[2] or geo[0],
    ]  # fmt: skip


RENTS = [
    rent("R1", datetime(2026, 9, 5), datetime(2026, 9, 10), datetime(2027, 9, 9), 100_000,
         100_000, "New", "2 Bed"),
    rent("R2", datetime(2026, 9, 6), datetime(2026, 9, 10), datetime(2028, 9, 9), 120_000,
         240_000, "Renewed", "2 Beds"),
    rent("R3", datetime(2026, 9, 7), datetime(2026, 9, 10), datetime(2027, 9, 9), 90_000,
         90_000, "New", ""),
    rent("R4", datetime(2026, 9, 8), datetime(2026, 9, 10), datetime(2027, 9, 9), 0, 0,
         "New", "2 Bed"),
    rent("R5", datetime(2026, 10, 1), datetime(2026, 10, 1), datetime(2027, 9, 30), 500_000,
         500_000, "New", "2 Bed"),
    # A 2-bed lease DLD locates only by the district Marsa Dubai (which also holds JBR,
    # Dubai Harbour and Bluewaters): never counted as Dubai Marina, but disclosed.
    rent("R6", datetime(2026, 9, 9), datetime(2026, 9, 10), datetime(2027, 9, 9), 95_000, 95_000,
         "New", "2 Bed", geo=MARSA_DISTRICT_ONLY),
]  # fmt: skip


def verified(*ranges: tuple[str, str]) -> list[dict[str, str]]:
    return [{"from": a, "to": b, "status": "verified"} for a, b in ranges]


async def publish_coverage(
    warehouse, release_id: str, dataset: str, ranges: list[dict], state: str = "published"
) -> None:
    """The ledger entry Holocron writes for a release (state and coverage only)."""
    await warehouse.insert(
        "market_release_ledger",
        # Each call is a newer version of the entry (ReplacingMergeTree keeps the latest).
        [[release_id, dataset, state, json.dumps({"ranges": ranges}), datetime.now(),
          datetime.now()]],
        column_names=["release_id", "dataset", "state", "coverage_json", "started_at",
                      "updated_at"],
    )  # fmt: skip


@pytest.fixture
async def served(warehouse, install_serving):
    await install_serving(
        tables=("market_sale_events", "market_rental_events", "market_release_ledger"),
        views=("ch_transactions", "ch_rent_contracts"),
    )
    await warehouse.insert("market_sale_events", SALES, column_names=SALE_COLUMNS)
    await warehouse.insert("market_rental_events", RENTS, column_names=RENT_COLUMNS)
    # Every day from the first to the last loaded record was loaded in full.
    await publish_coverage(
        warehouse, "r-sales", "market_sale_events", verified(("2026-08-31", "2026-10-01"))
    )
    await publish_coverage(
        warehouse, "r-rent", "market_rental_events", verified(("2026-09-05", "2026-10-01"))
    )
    return warehouse


def request(**fields) -> MarketMetricsRequest:
    fields.setdefault("period", {"type": "previous_month"})
    fields.setdefault("reference_date", REFERENCE)
    return MarketMetricsRequest(**fields)


ALL_SALE_METRICS = [
    "count", "total_aed", "mean_aed", "median_aed", "mean_aed_per_sqm", "median_aed_per_sqm",
    "weighted_aed_per_sqm", "mean_aed_per_sqft",
]  # fmt: skip


async def test_last_month_average_sale_price_in_jvc(served):
    result = await get_market_metrics(
        request(dataset="sales", location="JVC", metrics=ALL_SALE_METRICS)
    )
    assert (result.period.start, result.period.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert result.period.timezone == "Asia/Dubai"
    assert result.coverage.status == "complete"
    assert result.total.count == 3
    values = result.total.values
    assert values["total_aed"] == 6_000_000
    assert values["mean_aed"] == 2_000_000
    assert values["median_aed"] == 1_000_000
    # Per-sqm rates 20,000 / 10,000 / 40,000; weighted = 6,000,000 / 250 sqm.
    assert values["mean_aed_per_sqm"] == pytest.approx(70_000 / 3, abs=0.01)
    assert values["median_aed_per_sqm"] == 20_000
    assert values["weighted_aed_per_sqm"] == 24_000
    assert values["mean_aed_per_sqft"] == pytest.approx(70_000 / 3 / SQFT, abs=0.01)
    assert values["rated_count"] == 3
    assert {(m.area, m.master_project) for m in result.matched_locations} == {
        ("Al Barsha South Fourth", "Jumeirah Village Circle"),
        ("JUMEIRAH VILLAGE CIRCLE", ""),
    }
    assert result.sources == {"dubai_pulse/dld_only": 1, "dld_open_data/dld_only": 1,
                              "dld_open_data/matched": 1}  # fmt: skip
    assert "Small sample: 3 eligible records." in result.limitations


async def test_market_wide_median_of_an_even_count_averages_the_middle_pair(served):
    result = await get_market_metrics(request(dataset="sales", metrics=["median_aed", "mean_aed"]))
    # September sales: 1m, 1m, 4m (JVC) and 7m (Business Bay).
    assert result.total.count == 4
    assert result.total.values == {"median_aed": 2_500_000, "mean_aed": 3_250_000}


async def test_grouping_ranking_brackets_and_share(served):
    by_building = await get_market_metrics(
        request(dataset="sales", group_by="building", metrics=["count", "median_aed"])
    )
    assert [(row.group, row.count, row.share_of_count) for row in by_building.rows] == [
        ("Tower A", 3, 0.75),
        ("Tower B", 1, 0.25),
    ]
    bands = await get_market_metrics(
        request(
            dataset="sales",
            location="Jumeirah Village Circle",
            group_by="price_band",
            price_bands=[1_000_000, 2_000_000],
        )
    )
    # [0, 1m) none; [1m, 2m) S1, S2; >= 2m S3.
    assert [(row.group, row.count) for row in bands.rows] == [
        ("under 1,000,000", 0),
        ("1,000,000 to under 2,000,000", 2),
        ("2,000,000 and above", 1),
    ]


async def test_empty_partial_and_unavailable_periods_are_distinct(served):
    empty = await get_market_metrics(request(dataset="sales", location="Dubai Marina"))
    assert empty.coverage.status == "complete"
    assert empty.total.count == 0 and empty.total.values["mean_aed"] is None

    # October asked on 15 November: only 1 October is loaded.
    partial = await get_market_metrics(request(dataset="sales", reference_date=date(2026, 11, 15)))
    assert partial.coverage.status == "partial"
    assert (partial.coverage.covered_from, partial.coverage.covered_to) == (
        date(2026, 10, 1),
        date(2026, 10, 1),
    )
    assert partial.total.count == 1 and partial.total.values["mean_aed"] == 9_000_000
    assert partial.coverage.note in partial.limitations

    future = await get_market_metrics(request(dataset="sales", reference_date=date(2027, 1, 5)))
    assert future.coverage.status == "unavailable"
    assert future.total.count is None and future.total.values["mean_aed"] is None


async def test_monthly_series_marks_uncovered_months_unknown_not_zero(served):
    result = await get_market_metrics(
        request(
            dataset="sales",
            location="JVC",
            period={"type": "range", "start": date(2026, 8, 1), "end": date(2026, 11, 30)},
            group_by="month",
        )
    )
    assert [(row.group, row.count) for row in result.rows] == [
        ("2026-08", 1),
        ("2026-09", 3),
        ("2026-10", 1),
        ("2026-11", None),
    ]


async def test_rentals_annual_versus_contract_new_versus_renewed(served):
    two_bed = await get_market_metrics(
        request(
            dataset="rentals",
            location="Dubai Marina",
            bedrooms=["2"],
            metrics=["median_aed", "median_contract_aed", "total_contract_aed"],
            group_by="contract_version",
        )
    )
    assert two_bed.total.count == 2
    assert two_bed.total.values == {
        "median_aed": 110_000,
        "median_contract_aed": 170_000,
        "total_contract_aed": 340_000,
    }
    assert sorted((row.group, row.count, row.values["median_aed"]) for row in two_bed.rows) == [
        ("New", 1, 100_000),
        ("Renewed", 1, 120_000),
    ]
    assert any("1 of 3 records" in item for item in two_bed.limitations)
    assert any(
        item.startswith("1 records matching the other filters are located by DLD only at "
                        "district level (Marsa Dubai)")
        for item in two_bed.limitations
    )  # fmt: skip
    district = await get_market_metrics(
        request(dataset="rentals", location="Marsa Dubai", bedrooms=["2"])
    )
    assert district.total.count == 3  # the district itself includes R6

    long_terms = await get_market_metrics(
        request(dataset="rentals", location="Dubai Marina", min_term_days=367)
    )
    assert long_terms.total.count == 1 and long_terms.total.values["mean_aed"] == 120_000


async def test_previous_period_comparison_reports_change(served):
    result = await get_market_metrics(
        request(
            dataset="sales",
            location="JVC",
            metrics=["count", "median_aed"],
            compare_with_previous_period=True,
        )
    )
    assert result.comparison_period.start == date(2026, 8, 1)
    # August starts before the loaded data (31 August), so no change is claimed.
    assert result.comparison_coverage.status == "partial"
    assert result.comparison.count == 1
    assert result.change_pct == {}


async def test_api_and_chat_tool_return_the_database_result(served, client):
    """The HTTP route and the chat tool call the same operation over the same rows.

    Both are checked against the hand-computed September JVC numbers, not only each other.
    """
    from app.chat.tools import query_market_metrics

    body = {
        "dataset": "sales",
        "location": "JVC",
        "metrics": ["count", "total_aed", "mean_aed", "median_aed"],
        "period": {"type": "previous_month"},
        "reference_date": "2026-10-03",
    }
    response = await client.post("/api/v1/analytics/market-metrics", json=body)
    assert response.status_code == 200
    api = response.json()
    chat = await query_market_metrics(None, MarketMetricsRequest(**body))
    expected = {"total_aed": 6_000_000, "mean_aed": 2_000_000, "median_aed": 1_000_000}
    for result in (api, chat):
        assert result["total"]["count"] == 3
        assert result["total"]["values"] == expected
        assert (result["period"]["start"], result["period"]["end"]) == ("2026-09-01", "2026-09-30")
        assert result["coverage"]["status"] == "complete"
    assert api == chat


async def test_registered_chat_agent_executes_the_tool_against_the_database(served, client):
    """Drive the production chat agent with a scripted model (no provider call).

    The model asks for last month's JVC sales; the agent runs the registered tool, and the
    tool return it hands back to the model must equal the API result.
    """
    import json

    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    from app.chat.agent import agent

    arguments = {
        "dataset": "sales",
        "location": "JVC",
        "metrics": ["count", "mean_aed", "median_aed"],
        "period": {"type": "previous_month"},
        "reference_date": "2026-10-03",
    }
    returned = []

    def scripted(messages, info):
        tool_returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not tool_returns:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="query_market_metrics", args=arguments)]
            )
        returned.append(tool_returns[-1].content)
        return ModelResponse(parts=[TextPart(content="done")])

    with agent.override(model=FunctionModel(scripted)):
        await agent.run("What was the average sale price in JVC last month?")

    api = (await client.post("/api/v1/analytics/market-metrics", json=arguments)).json()
    assert returned and json.loads(json.dumps(returned[0])) == api
    assert api["total"]["count"] == 3
    assert api["total"]["values"] == {"mean_aed": 2_000_000, "median_aed": 1_000_000}


async def test_missing_week_between_populated_dates_is_not_complete(served):
    # Records on 1 and 30 September span the whole month, yet 11-17 September were never
    # loaded. First/last dates would call the month complete; the evidence does not.
    await publish_coverage(
        served,
        "r-sales",
        "market_sale_events",
        verified(("2026-08-31", "2026-09-10"), ("2026-09-18", "2026-10-01")),
    )
    result = await get_market_metrics(request(dataset="sales", location="JVC"))
    assert result.coverage.status == "partial"
    assert (result.coverage.verified_days, result.coverage.unverified_days) == (23, 7)
    gap = [r for r in result.coverage.ranges if r.status != "verified"]
    assert [(r.start, r.end) for r in gap] == [(date(2026, 9, 11), date(2026, 9, 17))]
    assert result.total.count == 3  # observed records, reported as partial
    assert any("2026-09-11 to 2026-09-17 unverified" in item for item in result.limitations)


async def test_verified_empty_month_is_zero_but_an_unverified_one_is_unknown(served):
    period = {"type": "range", "start": date(2026, 6, 1), "end": date(2026, 7, 31)}
    unknown = await get_market_metrics(request(dataset="sales", period=period, group_by="month"))
    assert unknown.coverage.status == "unavailable"
    assert unknown.total.count is None

    # June is now verified as fully loaded with no sales; July still has no evidence.
    await publish_coverage(
        served,
        "r-sales",
        "market_sale_events",
        verified(("2026-06-01", "2026-06-30"), ("2026-08-31", "2026-10-01")),
    )
    result = await get_market_metrics(request(dataset="sales", period=period, group_by="month"))
    assert result.coverage.status == "partial"
    assert [(row.group, row.count, row.coverage) for row in result.rows] == [
        ("2026-06", 0, "verified"),
        ("2026-07", None, "unverified"),
    ]


async def test_records_without_any_load_evidence_are_unverified_not_complete(served):
    await publish_coverage(served, "r-sales", "market_sale_events", [])
    result = await get_market_metrics(request(dataset="sales", location="JVC"))
    assert result.coverage.status == "unverified"
    assert result.total.count == 3
    assert result.change_pct == {} and result.coverage.note in result.limitations


async def test_one_answer_comes_from_one_release(served, monkeypatch):
    """A publication during the operation makes it recompute against the new release."""
    from app.analytics import market_metric_queries

    real = market_metric_queries.fetch_release_id
    calls = []

    async def publication_mid_request(dataset):
        calls.append(dataset)
        if len(calls) == 1:
            # The first check sees a new release: the computed answer is discarded.
            return "r-new"
        return await real(dataset)

    monkeypatch.setattr(market_metric_queries, "fetch_release_id", publication_mid_request)
    result = await get_market_metrics(request(dataset="sales", location="JVC"))
    assert len(calls) == 2 and result.release_id == "r-sales"
    assert not any("release changed" in item for item in result.limitations)


async def test_source_vocabularies_meet_in_one_district_and_community(served):
    """S1 (Pulse: Al Barsha South Fourth + master JVC) and S2 (open data: label
    JUMEIRAH VILLAGE CIRCLE) are one place; grouping never splits them by raw spelling."""
    by_area = await get_market_metrics(request(dataset="sales", group_by="area"))
    assert [(row.group, row.count) for row in by_area.rows] == [
        ("Al Barsha South Fourth", 3),
        ("Business Bay", 1),
    ]
    by_community = await get_market_metrics(request(dataset="sales", group_by="community"))
    assert [(row.group, row.count) for row in by_community.rows] == [
        ("Jumeirah Village Circle", 3),
        ("Business Bay", 1),
    ]


async def publish_version(warehouse, release_id: str) -> None:
    """A Holocron publication: an immutable copy of the served rows plus its ledger entry."""
    version = f"market_sale_events__r{release_id}"
    await warehouse.command(f"CREATE TABLE {version} AS market_sale_events")
    await warehouse.command(
        f"INSERT INTO {version} SELECT * REPLACE ('{release_id}' AS release_id) "
        "FROM market_sale_events"
    )
    await publish_coverage(
        warehouse, release_id, "market_sale_events", verified(("2026-08-31", "2026-10-01"))
    )


async def test_a_pinned_operation_reads_one_immutable_release_throughout(served):
    """PR #13's lease contract: every query under one pin reads the same immutable versions,
    even when a new release is published and the serving view changes mid-operation."""
    from app.market.releases import ReleaseUnavailable, pin_market_release

    await publish_version(served, "a1a1a1a1a1a1")
    async with pin_market_release() as pin:
        assert pin.release_id == "sales=a1a1a1a1a1a1" and pin.missing == ("rentals",)
        assert ("sales", date(2026, 8, 31), date(2026, 10, 1), "complete") in pin.coverage()
        first = await get_market_metrics(request(dataset="sales", location="JVC"))

        # A new release goes live: one more September JVC sale, in its own version and in
        # the mutable serving table the dashboards read.
        newer = sale(
            "S4", datetime(2026, 9, 20), "Sales", 2_000_000, 100, "JUMEIRAH VILLAGE CIRCLE"
        )
        await served.insert("market_sale_events", [newer], column_names=SALE_COLUMNS)
        await publish_version(served, "b2b2b2b2b2b2")
        await publish_coverage(
            served, "a1a1a1a1a1a1", "market_sale_events",
            verified(("2026-08-31", "2026-10-01")), state="superseded",
        )  # fmt: skip
        again = await get_market_metrics(request(dataset="sales", location="JVC", group_by="month"))

        assert first.total.count == again.total.count == 3
        assert first.release_id == again.release_id == "a1a1a1a1a1a1"
        assert first.coverage.status == "complete"
        with pytest.raises(ReleaseUnavailable):
            await get_market_metrics(request(dataset="rentals"))

    async with pin_market_release(("sales",)) as later:
        assert later.release_id == "sales=b2b2b2b2b2b2"
        assert (await get_market_metrics(request(dataset="sales", location="JVC"))).total.count == 4


async def test_an_expired_pin_fails_closed_instead_of_reading_a_dropped_version(served):
    from datetime import UTC, timedelta

    from app.market import releases

    await publish_version(served, "a1a1a1a1a1a1")
    async with releases.pin_market_release(("sales",)) as pin:
        expired = releases.PinnedRelease(
            pin.versions, pin.acquired_at, datetime.now(UTC) - timedelta(seconds=1)
        )
        token = releases._pinned.set(expired)
        try:
            with pytest.raises(releases.ReleaseLeaseExpired):
                await get_market_metrics(request(dataset="sales"))
        finally:
            releases._pinned.reset(token)
