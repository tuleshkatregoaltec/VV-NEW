"""Owner-file sale dates: the import reads more ways of writing a date, and never reads a date it
used to read differently. A sale date is how a row is checked against DLD, so a date the import
cannot read leaves the owner unverified, and a date read the wrong way round can match the wrong
sale."""

import io
from datetime import date, datetime

import openpyxl
import pytest

from app.crm.import_parsing import (
    _date_order,
    _inspect_csv,
    _inspect_xlsx,
    _parse_csv,
    _parse_date,
    _parse_xlsx,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-09-25", date(2024, 9, 25)),
        ("25/09/2024", date(2024, 9, 25)),
        ("25-09-2024", date(2024, 9, 25)),
        ("25 Sep 2024", date(2024, 9, 25)),
        ("25-Sep-2024", date(2024, 9, 25)),
        ("2024/09/25", date(2024, 9, 25)),
        ("04/03/2024", date(2024, 3, 4)),
        ("5/3/2024", date(2024, 3, 5)),
    ],
)
def test_dates_read_before_are_read_the_same(text, expected):
    # Files that import correctly today must give exactly the same dates.
    assert _parse_date(text) == expected


def test_real_excel_dates_are_unchanged():
    assert _parse_date(datetime(2024, 9, 25, 0, 0)) == date(2024, 9, 25)
    assert _parse_date(datetime(2024, 9, 25, 14, 30)) == date(2024, 9, 25)
    assert _parse_date(date(2024, 9, 25)) == date(2024, 9, 25)


@pytest.mark.parametrize(
    "text",
    [
        "25 Sept 2024",
        "25-Sept-2024",
        "September 25, 2024",
        "September 25 2024",
        "25 September 2024",
        "Sep 25, 2024",
        "sept 25, 2024",
        "2024-09-25 10:30",
        "2024-09-25 10:30:15",
        "2024-09-25T10:30:15",
        "2024-09-25 00:00:00",
        "25/09/2024 10:30",
        "25/09/2024 10:30 AM",
        "09/25/2024",
        "9/25/2024",
    ],
)
def test_dates_ignored_before_are_now_read(text):
    # These were left empty, so the owner could never be verified against a DLD sale.
    assert _parse_date(text) == date(2024, 9, 25)


@pytest.mark.parametrize(
    "text",
    ["", "null", "-", "n/a", "Sale", "31/02/2024", "13/13/2024", "2024", "45000", "971-52-9222218"],
)
def test_values_that_are_not_dates_stay_empty(text):
    assert _parse_date(text) is None


def test_unclear_day_month_follows_the_column_order():
    # 04/03/2024: 4 March in a UAE (day-first) column, 3 April in an American (month-first)
    # column, and left empty when the column mixes both, rather than guessed.
    assert _parse_date("04/03/2024", "dmy") == date(2024, 3, 4)
    assert _parse_date("04/03/2024", "mdy") == date(2024, 4, 3)
    assert _parse_date("04/03/2024", "mixed") is None
    # The same number twice is the same date either way.
    assert _parse_date("05/05/2024", "mixed") == date(2024, 5, 5)
    # A clear date is read by its own clue whatever the column says.
    assert _parse_date("25/09/2024", "mdy") == date(2024, 9, 25)
    assert _parse_date("09/25/2024", "dmy") == date(2024, 9, 25)
    assert _parse_date("25/09/2024", "mixed") == date(2024, 9, 25)


def test_date_order_of_a_column():
    assert _date_order([]) == "dmy"
    assert _date_order([datetime(2024, 9, 25), "04/03/2024", None, "null"]) == "dmy"
    assert _date_order(["04/03/2024", "25/09/2024"]) == "dmy"
    assert _date_order(["04/03/2024", "09/25/2024"]) == "mdy"
    assert _date_order(["04/03/2024", "09/25/2024 10:30"]) == "mdy"
    assert _date_order(["25/09/2024", "09/26/2024", "04/03/2024"]) == "mixed"


def _csv(*rows: str) -> bytes:
    header = "NameEn,ProcedurePartyTypeNameEn,Regis,ProcedureValue,BuildingNameEn,UnitNumber"
    return "\n".join((header, *rows)).encode()


def _import_csv(data: bytes):
    profiles = _inspect_csv(data, "owners.csv")
    candidates, _ = _parse_csv(data, "owners.csv", profiles)
    return {c.contact_name: c.transaction_date for c in candidates}, profiles[0].warnings


def test_csv_in_uae_order_reads_unclear_dates_day_first_without_warnings():
    dates, warnings = _import_csv(
        _csv(
            "Ali,Buyer,04/03/2024,1500000,Tower A,101",
            "Sara,Buyer,25/09/2024,1600000,Tower A,102",
        )
    )
    assert dates == {"Ali": date(2024, 3, 4), "Sara": date(2024, 9, 25)}
    assert not [w for w in warnings if "date" in w.lower()]


def test_csv_in_american_order_reads_every_date_month_first_and_says_so():
    dates, warnings = _import_csv(
        _csv(
            "Ali,Buyer,04/03/2024,1500000,Tower A,101",
            "Sara,Buyer,09/25/2024,1600000,Tower A,102",
        )
    )
    assert dates == {"Ali": date(2024, 4, 3), "Sara": date(2024, 9, 25)}
    assert any("month first" in w for w in warnings)


def test_csv_mixing_both_orders_leaves_unclear_dates_empty_and_warns():
    dates, warnings = _import_csv(
        _csv(
            "Ali,Buyer,04/03/2024,1500000,Tower A,101",
            "Sara,Buyer,25/09/2024,1600000,Tower A,102",
            "Omar,Buyer,09/26/2024,1700000,Tower A,103",
        )
    )
    assert dates == {"Ali": None, "Sara": date(2024, 9, 25), "Omar": date(2024, 9, 26)}
    assert any("1 unclear date" in w for w in warnings)


def test_dates_that_still_cannot_be_read_are_counted_in_a_warning():
    dates, warnings = _import_csv(
        _csv(
            "Ali,Buyer,soon,1500000,Tower A,101",
            "Sara,Buyer,null,1600000,Tower A,102",
            "Omar,Buyer,25 Sept 2024,1700000,Tower A,103",
        )
    )
    assert dates == {"Ali": None, "Sara": None, "Omar": date(2024, 9, 25)}
    assert any("1 date" in w and "could not be read" in w for w in warnings)


def test_xlsx_text_dates_follow_the_column_order_and_real_dates_are_kept():
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(
        [
            "NameEn",
            "ProcedurePartyTypeNameEn",
            "Regis",
            "ProcedureValue",
            "BuildingNameEn",
            "UnitNumber",
        ]
    )
    sheet.append(["Ali", "Buyer", "04/03/2024", 1500000, "Tower A", "101"])
    sheet.append(["Sara", "Buyer", "09/25/2024", 1600000, "Tower A", "102"])
    sheet.append(["Omar", "Buyer", datetime(2024, 1, 2), 1700000, "Tower A", "103"])
    buffer = io.BytesIO()
    book.save(buffer)
    data = buffer.getvalue()

    profiles = _inspect_xlsx(data, "owners.xlsx")
    candidates, _ = _parse_xlsx(data, "owners.xlsx", profiles)

    assert {c.contact_name: c.transaction_date for c in candidates} == {
        "Ali": date(2024, 4, 3),
        "Sara": date(2024, 9, 25),
        "Omar": date(2024, 1, 2),
    }
