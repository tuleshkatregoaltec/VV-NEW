"""DLD project delivery over the repository bronze DDL and the ch_projects serving view.

Expected values are counted by hand from the rows below.
"""

from datetime import date, datetime

import pytest

from app.project.delivery import get_delivery_summary

REFERENCE = date(2026, 10, 3)  # 24-month horizon ends 2028-10-03
COLUMNS = [
    "row_key", "run_id", "scraped_at", "source_system", "project_number", "project_id",
    "project_en", "end_date", "completion_date", "cancellation_date", "project_status",
    "cnt_unit", "developer_en", "developer_name", "area_en", "master_project_en",
]  # fmt: skip
PULSE = ("dld-pulse-historic-2026-05-16", "2026-05-16T15:34:16+00:00", "dubai_pulse")
OPEN = ("2026-09-20T00-20-05Z", "2026-09-20T00:20:05+00:00", "dld_open_data")
SOUTH, SOUTH_MASTER = "Madinat Al Mataar", "Dubai South Residential District"


def project(key, snapshot, number, project_id, name, end, units, developer, status="ACTIVE",
            completed=None, cancelled=None, area=SOUTH, master=SOUTH_MASTER):  # fmt: skip
    return [
        key, *snapshot, number, project_id, name, end, completed, cancelled, status, units,
        developer, developer, area, master,
    ]  # fmt: skip


ROWS = [
    # Same project in the Pulse snapshot and the later open-data snapshot: served once, with
    # the open-data values (120 units) and the Pulse project_id.
    project("p1-pulse", PULSE, 100, 555, "", datetime(2027, 6, 30), 100, "Dev A"),
    project("p1-open", OPEN, 100, 0, "Alpha", datetime(2027, 6, 30), 120, "Dev A"),
    # Open-data row without a master project: only its official district is known.
    project("p2", OPEN, 101, 0, "Beta", datetime(2028, 1, 31), 80, "Dev B", master=""),
    # Repeated identical row inside one run: counted once.
    project("p10-a", OPEN, 109, 0, "Kappa", datetime(2027, 3, 31), 20, "Dev B"),
    project("p10-b", OPEN, 109, 0, "Kappa", datetime(2027, 3, 31), 20, "Dev B"),
    project("p3", OPEN, 102, 0, "Gamma", datetime(2029, 6, 30), 50, "Dev A"),
    project("p4", OPEN, 103, 0, "Delta", None, 30, "Dev C"),
    project("p5", OPEN, 104, 0, "Epsilon", datetime(2026, 5, 31), 40, "Dev C"),
    project("p6", OPEN, 105, 0, "Zeta", datetime(2026, 1, 31), 60, "Dev D",
            completed=datetime(2026, 1, 1)),
    project("p7", OPEN, 106, 0, "Eta", datetime(2027, 1, 31), 10, "Dev D",
            cancelled=datetime(2025, 1, 1)),
    project("p8", OPEN, 107, 0, "Theta", datetime(2027, 1, 31), 70, "Dev A", status="FINISHED"),
    project("p9", OPEN, 108, 0, "Iota", datetime(2027, 1, 1), 999, "Dev E",
            area="Al Barsha South Fourth", master="Jumeirah Village Circle"),
]  # fmt: skip


@pytest.fixture
async def register(warehouse, install_serving):
    await install_serving(tables=("dld_od_projects_bronze",), views=("ch_projects",))
    await warehouse.insert("dld_od_projects_bronze", ROWS, column_names=COLUMNS)
    return warehouse


async def test_serving_view_has_one_row_per_project_number_and_keeps_its_id(register):
    rows = (
        await register.query(
            "SELECT project_number, project_id, project_name_en, no_of_units FROM ch_projects "
            "WHERE project_number IN ('100', '109') ORDER BY project_number"
        )
    ).result_rows
    assert rows == [("100", 555, "Alpha", 120), ("109", 0, "Kappa", 20)]
    total = (await register.query("SELECT count() FROM ch_projects")).result_rows[0][0]
    assert total == 10  # 12 bronze rows, 10 project numbers


async def test_developer_share_of_units_due_in_the_next_two_years(register):
    result = await get_delivery_summary(
        location="Dubai South",
        delivery_statuses=("scheduled_in_horizon",),
        group_by="developer",
        reference_date=REFERENCE,
    )
    assert result.horizon_end == date(2028, 10, 3)
    # Due by 2028-10-03 in Dubai South: Alpha 120 (Dev A), Kappa 20 (Dev B) = 140 units.
    # Beta names only the district Madinat Al Mataar (which also holds Dubai World Central),
    # so it is disclosed, not attributed to Dubai South.
    assert [(r.group, r.project_count, r.units, r.share_of_units) for r in result.rows] == [
        ("Dev A", 1, 120, round(120 / 140, 4)),
        ("Dev B", 1, 20, round(20 / 140, 4)),
    ]
    assert "1 projects in Madinat Al Mataar have no master project and are not counted" in " ".join(
        result.limitations
    )
    district = await get_delivery_summary(
        location="Madinat Al Mataar",
        delivery_statuses=("scheduled_in_horizon",),
        group_by="developer",
        reference_date=REFERENCE,
    )
    assert sum(r.units for r in district.rows) == 220


async def test_missing_and_overdue_dates_are_separated_from_future_delivery(register):
    result = await get_delivery_summary(
        location="Dubai South", group_by="delivery_status", reference_date=REFERENCE
    )
    by_status = {row.group: (row.project_count, row.units) for row in result.by_delivery_status}
    assert by_status == {
        "scheduled_in_horizon": (2, 140),
        "completed": (1, 60),
        "scheduled_later": (1, 50),
        "overdue": (1, 40),
        "missing_date": (1, 30),
        "cancelled": (1, 10),
    }
