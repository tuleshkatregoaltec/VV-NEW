"""Source procedure semantics, independent of whether a row is retained."""

from __future__ import annotations

import re

IMPORT_RESOLVER_VERSION = "owner-context-8"


def event_family(procedure: str) -> str:
    label = " ".join(re.sub(r"[-_]", " ", procedure.casefold()).split())
    # Composite procedures take precedence over incidental 'sell' words.
    if "mortgage" in label or "finance" in label:
        return "financing"
    if "lease to own" in label:
        return "lease_to_own"
    if "grant" in label or "gift" in label:
        return "gift"
    if "heir" in label or "inherit" in label:
        return "inheritance"
    if "ownership transfer" in label:
        return "ownership_transfer"
    if "annexation" in label or "cut off" in label:
        return "property_change"
    if re.search(r"\b(sale|sell)\b", label):
        return "sale"
    return "unknown" if label else "contact_snapshot"


# Report-only category: stored statuses are unchanged; mortgages are shown apart from them.
MORTGAGE_REPORT_STATUSES = ("unit_linked_unverified", "unmatched")


def is_mortgage_review(claim) -> bool:
    """An unverified or needs-review row whose event is a mortgage or other financing."""
    if claim.ownership_status not in MORTGAGE_REPORT_STATUSES:
        return False
    family = (claim.evidence or {}).get("event_family") or event_family(claim.procedure_type or "")
    return family == "financing"


def matches_report_status(claim, status: str) -> bool:
    """Whether a row belongs to a report category ("mortgage" or a stored status)."""
    if status == "mortgage":
        return is_mortgage_review(claim)
    return claim.ownership_status == status and not is_mortgage_review(claim)


def has_sale_evidence(candidate) -> bool:
    """Only explicit sales may use sale date/amount as acquisition evidence.

    Older imports did not store a family. Preserve their explicit Buyer/Seller
    blank-procedure interpretation; newly parsed rows always carry a family.
    """
    family = candidate.evidence.get("event_family")
    if family is None:
        family = event_family(candidate.procedure_type)
        if not candidate.procedure_type and candidate.party_role in {"Buyer", "Seller"}:
            family = "sale"
    return family == "sale" and candidate.party_role in {"Buyer", "Seller"}


def is_owner_list_entry(candidate) -> bool:
    """An owner-list row: no role or procedure, but a registration date.

    It may count as an acquisition only once a DLD sale confirms that date.
    """
    family = candidate.evidence.get("event_family")
    if family is None:
        family = event_family(candidate.procedure_type)
    return (
        family == "contact_snapshot"
        and not candidate.party_role
        and candidate.transaction_date is not None
    )
