from datetime import date

from app.crm import owner_queries as crm_owner_queries
from app.crm.identity import ParsedContact, _location_key, _normalize_unit
from app.crm.import_parsing import _inspect_csv, _parse_csv
from app.crm.ownership import _apply_evidence
from app.crm.registry_fallback import _apply_registry_unit_fallbacks


def _contact(**overrides) -> ParsedContact:
    values = {
        "source_sheet": "TAG OLD",
        "source_row_number": 2,
        "contact_name": "Example Owner",
        "phone_primary": "+971501234567",
        "phone_alternate": None,
        "email_primary": None,
        "area_name": "Tilal Al Ghaf",
        "project_name": "Alaya",
        "building_name": "Alaya",
        "unit_number": "237",
        "property_type": "Villa",
        "transaction_date": None,
        "transaction_price_aed": None,
        "party_role": "Buyer",
        "procedure_type": "Sale",
        "row_fingerprint": "fingerprint",
    }
    values.update(overrides)
    return ParsedContact(**values)


def _sale(unit_number, building_name, sale_key, transaction_date, price=9_200_000):
    return {
        "unit_key": unit_number.lower(),
        "short_unit_key": _normalize_unit(unit_number).lower(),
        "unit_number": unit_number,
        "building_name": building_name,
        "building_key": _location_key(building_name),
        "project_name": "",
        "area_name": "Tilal Al Ghaf",
        "area_key": "tilalalghaf",
        "transaction_date": transaction_date,
        "sale_amount_aed": price,
        "sale_key": sale_key,
        "detail_url": "",
    }


def _identified(sale_key: str) -> dict:
    return {
        "property_identity_resolution": {
            "method": "near_transaction_signature_with_location",
            "score": 100,
            "resolved": {"sale_key": sale_key},
        }
    }


def _fake_registry(monkeypatch, registry: list[dict]):
    """Answer the three registry queries the fallback uses from an in-memory sales list."""
    calls: list[str] = []

    async def fake_query(sql, parameters=None):
        if "sale_key IN" in sql:
            calls.append("sale_keys")
            return [row for row in registry if row["sale_key"] in parameters["sale_keys"]]
        if "short_unit_key IN" in sql:
            calls.append("short_unit")
            return [row for row in registry if row["short_unit_key"] in parameters["unit_keys"]]
        calls.append("unit_keys")
        return [row for row in registry if row["unit_key"] in parameters["unit_keys"]]

    monkeypatch.setattr(crm_owner_queries, "query", fake_query)
    return calls


ALAYA_237 = [
    _sale("TAG-AL2-237", "Alaya", "al2-237-a", date(2023, 2, 24), 9_116_000),
    _sale("TAG-AL2-237", "Alaya", "al2-237-b", date(2023, 12, 18)),
    _sale("TAG-AL2-237", "Alaya", "al2-237-c", date(2024, 12, 25), 10_000_000),
]


async def test_sale_found_by_date_and_price_keeps_its_prefixed_registry_unit(monkeypatch):
    """Tilal Al Ghaf row 1902: date+price found TAG-AL2-237, but the unit lookup searched "237"
    and showed "No DLD sale found". The row must keep the full sales history of that villa."""
    _fake_registry(monkeypatch, ALAYA_237)
    contact = _contact(
        transaction_date=date(2023, 12, 18),
        transaction_price_aed=9_200_000,
        evidence=_identified("al2-237-b"),
    )
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts["resolved"] == 1
    assert [row["sale_key"] for row in sales[contact.property_key]] == [
        "al2-237-a",
        "al2-237-b",
        "al2-237-c",
    ]
    fallback = contact.evidence["registry_unit_fallback"]
    assert fallback["method"] == "identified_sale_registry_unit"
    assert fallback["registry_unit_number"] == "TAG-AL2-237"
    assert contact.unit_number == "237"

    _apply_evidence(contact, sales=sales, rentals={})
    # A later resale exists in the registry, so the uploaded buyer is a former owner.
    assert contact.ownership_status == "former_owner"
    assert contact.latest_sale_date == date(2024, 12, 25)


async def test_short_unit_resolves_when_location_separates_the_buildings(monkeypatch):
    """Aura A136 exists as TAG-AUR-3-A136 in Aura and as A136 in Elan; the building decides."""
    _fake_registry(
        monkeypatch,
        [
            _sale("TAG-AUR-3-A136", "Aura", "aura-a136", date(2022, 5, 1)),
            _sale("A136", "Elan", "elan-a136", date(2023, 5, 1)),
        ],
    )
    contact = _contact(project_name="Aura", building_name="Aura", unit_number="A136")
    sales: dict = {}

    await _apply_registry_unit_fallbacks([contact], sales)

    assert [row["sale_key"] for row in sales[contact.property_key]] == ["aura-a136"]
    assert contact.evidence["registry_unit_fallback"]["method"] == (
        "registry_short_unit_with_location"
    )


async def test_villa_number_shared_by_two_phases_stays_unresolved(monkeypatch):
    """ "Harmony 103" exists in Harmony 1 and Harmony 3; guessing would attach a wrong owner."""
    _fake_registry(
        monkeypatch,
        [
            _sale("TAG-HAR-1-103", "Harmony 1", "har1-103", date(2022, 5, 1)),
            _sale("TAG-HAR-3-103", "Harmony 3", "har3-103", date(2023, 5, 1)),
        ],
    )
    contact = _contact(project_name="Harmony", building_name="Harmony", unit_number="103")
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts["resolved"] == 0
    assert sales == {}
    assert contact.building_name == "Harmony"
    assert "registry_unit_fallback" not in contact.evidence


async def test_rows_already_matched_are_never_touched(monkeypatch):
    """Other imports must keep their match: a row with sales evidence is not re-queried."""
    calls = _fake_registry(monkeypatch, ALAYA_237)
    contact = _contact(building_name="Aspect Tower", project_name="", unit_number="602")
    existing = [{"sale_key": "existing", "transaction_date": date(2024, 1, 1)}]
    sales = {contact.property_key: existing}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts == {"pending": 0, "resolved": 0, "collisions": 0}
    assert calls == []
    assert sales == {contact.property_key: existing}
    assert contact.building_name == "Aspect Tower"


async def test_two_villas_sharing_a_short_unit_are_not_merged(monkeypatch):
    """TAG-AL2-237 and TAG-M1-237 both shorten to "alaya|237"; neither may borrow the other's
    sales history."""
    _fake_registry(
        monkeypatch,
        [*ALAYA_237, _sale("TAG-M1-237", "Alaya", "m1-237", date(2024, 3, 1), 7_000_000)],
    )
    first = _contact(evidence=_identified("al2-237-b"))
    second = _contact(source_row_number=3, evidence=_identified("m1-237"))
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([first, second], sales)

    assert counts["resolved"] == 0
    assert counts["collisions"] == 2
    assert sales == {}


async def test_ambiguous_row_with_the_same_key_blocks_the_resolution(monkeypatch):
    """An ALAYA-sheet row "Alaya 237" with no date cannot tell AL2 from M1. If another row
    resolved to "alaya|237", the ambiguous row would inherit that villa's sales."""
    _fake_registry(
        monkeypatch,
        [*ALAYA_237, _sale("TAG-M1-237", "Alaya", "m1-237", date(2024, 3, 1), 7_000_000)],
    )
    identified = _contact(evidence=_identified("al2-237-b"))
    ambiguous = _contact(source_sheet="ALAYA", source_row_number=3)
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([identified, ambiguous], sales)

    assert counts["resolved"] == 0
    assert sales == {}


async def test_two_rows_for_the_same_villa_both_resolve(monkeypatch):
    """Buyer and seller rows for one villa point at one registry property; that is not a
    collision."""
    _fake_registry(monkeypatch, ALAYA_237)
    buyer = _contact(evidence=_identified("al2-237-b"))
    seller = _contact(source_row_number=3, party_role="Seller", evidence=_identified("al2-237-b"))
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([buyer, seller], sales)

    assert counts["resolved"] == 2
    assert buyer.property_key == seller.property_key
    assert len(sales[buyer.property_key]) == 3


HARMONY_103 = [
    _sale("TAG-HAR-1-103", "Harmony 1", "har1-103", date(2022, 5, 1)),
    _sale("TAG-HAR-3-103", "Harmony 3", "har3-103", date(2023, 5, 1)),
]


def test_parser_keeps_the_original_villa_number_without_changing_the_unit():
    """The HARMONY sheet gives TAG-HAR-2-006; the stored unit and fingerprint stay as before
    (006), and only evidence gains the original text the cluster can be read from."""
    data = b"VILLA NUMBER,NAME,PHONE\nTAG-HAR-2-006,Example Owner,0501234567\n602,Other Owner,0507654321\n"
    profiles = _inspect_csv(data, "Harmony.csv")

    candidates, _ = _parse_csv(data, "Harmony.csv", profiles)

    tagged, plain = candidates
    assert tagged.unit_number == "006"
    assert tagged.evidence["source_unit_number"] == "TAG-HAR-2-006"
    assert plain.unit_number == "602"
    assert "source_unit_number" not in plain.evidence


async def test_original_villa_number_picks_the_cluster_the_short_number_cannot(monkeypatch):
    """ "Harmony 103" alone is ambiguous between Harmony 1 and 3; TAG-HAR-3-103 is not."""
    _fake_registry(monkeypatch, HARMONY_103)
    contact = _contact(
        project_name="Harmony",
        building_name="Harmony",
        unit_number="103",
        evidence={"source_unit_number": "TAG-HAR-3-103"},
    )
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts["resolved"] == 1
    assert contact.building_name == "Harmony 3"
    assert contact.unit_number == "103"
    assert [row["sale_key"] for row in sales[contact.property_key]] == ["har3-103"]
    assert contact.evidence["registry_unit_fallback"]["method"] == (
        "source_unit_exact_registry_unit"
    )


async def test_original_villa_number_with_contradictory_location_is_rejected(monkeypatch):
    """A villa id that exists only in another community must not be accepted on the id alone."""
    _fake_registry(monkeypatch, HARMONY_103)
    contact = _contact(
        area_name="Dubai Hills Estate",
        project_name="Maple",
        building_name="Maple",
        unit_number="103",
        evidence={"source_unit_number": "TAG-HAR-3-103"},
    )
    sales: dict = {}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts["resolved"] == 0
    assert sales == {}


async def test_key_already_used_by_the_same_villa_is_not_a_collision(monkeypatch):
    """A TAG OLD row matched TAG-AL2-237 earlier; the ALAYA-sheet row for the same villa may
    join it, because both read the same sales history."""
    _fake_registry(monkeypatch, ALAYA_237)
    matched_rows = [{**row, "_match_method": "unit_with_location_hierarchy"} for row in ALAYA_237]
    contact = _contact(
        source_sheet="ALAYA",
        building_name="Alaya Villas",
        evidence={"source_unit_number": "TAG-AL2-237"},
    )
    sales = {"alaya|237": matched_rows}

    counts = await _apply_registry_unit_fallbacks([contact], sales)

    assert counts["resolved"] == 1
    assert contact.property_key == "alaya|237"
    assert sales["alaya|237"] is matched_rows
