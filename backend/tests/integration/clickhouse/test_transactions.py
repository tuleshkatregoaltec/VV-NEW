import csv
import io
from datetime import date
from decimal import Decimal

from app.transaction import exports, facets, service


async def test_sales_scoping_facets_paging_and_explicit_id_csv_export(warehouse):
    fields = [
        "transaction_id",
        "instance_date",
        "property_usage_en",
        "trans_group_en",
        "procedure_name_en",
        "property_type_en",
        "property_sub_type_en",
        "rooms_en",
        "procedure_area",
        "actual_worth",
        "meter_sale_price",
        "reg_type_en",
        "area_name_en",
        "project_name_en",
        "building_name_en",
        "nearest_landmark_en",
        "nearest_metro_en",
        "nearest_mall_en",
        # Uniform layer columns (holocron market_sale_events): the page filters and lists these.
        "location_name",
        "property_type",
        "size_sqm",
    ]
    numeric = {"procedure_area", "actual_worth", "meter_sale_price", "size_sqm"}
    columns = ", ".join(
        f"{name} {'Date' if name == 'instance_date' else 'Decimal(18, 2)' if name in numeric else 'String'}"
        for name in fields
    )
    await warehouse.command(
        f"CREATE TABLE ch_transactions ({columns}) ENGINE = MergeTree ORDER BY transaction_id"
    )

    def row(identifier, group, room):
        values = dict.fromkeys(fields, "")
        values.update(
            transaction_id=identifier,
            instance_date=date.today(),
            property_usage_en="Residential",
            trans_group_en=group,
            property_type_en="Unit",
            property_sub_type_en="Apartment",
            rooms_en=room,
            procedure_area=Decimal("70"),
            actual_worth=Decimal("700000"),
            meter_sale_price=Decimal("10000"),
            # DLD's official district differs from the location the page shows and filters on.
            area_name_en="Marsa Dubai",
            location_name="Test Area",
            property_type="Apartment",
            size_sqm=Decimal("70"),
            project_name_en="=unsafe()",
            building_name_en="Tower",
            reg_type_en="Existing",
        )
        return [values[f] for f in fields]

    await warehouse.insert(
        "ch_transactions",
        [row("a", "Sales", "Studio"), row("b", "Sales", "2 B/R"), row("c", "Mortgage", "9 B/R")],
    )
    page = await service.fetch_sales_transactions(limit=1, offset=1, area="Test Area")
    assert page.total == 2
    assert [r.transaction_id for r in page.transactions] == ["b"]
    options = await facets.get_sales_filter_options(area="Test Area")
    assert options.rooms == ["Studio", "2 B/R"]
    # One vocabulary: the area and type options are the uniform values, not DLD's raw ones.
    assert (options.areas, options.property_types) == (["Test Area"], ["Apartment"])
    assert page.transactions[0].area_name_en == "Test Area"
    filtered = await exports.export_sales_transactions(area="Test Area", file_format="csv")
    rows = list(csv.DictReader(io.StringIO(filtered.getvalue().decode("utf-8-sig"))))
    assert [r["Transaction ID"] for r in rows] == ["a", "b"]
    assert rows[0]["Project"] == "'=unsafe()"
    selected = await exports.export_sales_transactions(
        transaction_ids="c", area="Elsewhere", file_format="csv"
    )
    rows = list(csv.DictReader(io.StringIO(selected.getvalue().decode("utf-8-sig"))))
    assert [r["Transaction ID"] for r in rows] == ["c"]  # Explicit IDs retain their existing path.
