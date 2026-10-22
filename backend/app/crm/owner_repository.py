from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm.import_models import (
    CrmContactList,
    CrmPropertyContactClaim,
)

_OWNER_REGISTRY_BASE_SQL = """
WITH base AS (
    SELECT
        c.id,
        c.organization_id,
        c.import_id,
        c.list_id,
        c.source_sheet,
        c.source_row_number,
        c.owner_key,
        c.property_key,
        c.contact_name,
        c.phone_primary,
        c.phone_alternate,
        c.email_primary,
        c.area_name,
        c.project_name,
        c.building_name,
        c.unit_number,
        c.property_type,
        c.party_role,
        c.transaction_date,
        c.transaction_price_aed,
        c.ownership_status,
        c.confidence_score,
        c.match_reason,
        c.lead_id,
        c.unit_candidate_key,
        c.latest_sale_date,
        c.latest_sale_price_aed,
        c.later_sale_date,
        c.lease_end,
        c.annual_rent_aed,
        c.owner_key AS owner_key_sql,
        c.property_key AS property_key_sql,
        CASE c.ownership_status
            WHEN 'former_owner' THEN 6
            WHEN 'verified_current_owner' THEN 5
            WHEN 'probable_current_owner' THEN 4
            WHEN 'unit_linked_unverified' THEN 3
            WHEN 'conflicting_claim' THEN 2
            ELSE 1
        END AS status_rank
    FROM crm_property_contact_claims c
    WHERE c.organization_id = :organization_id
      AND (
        CAST(:owner_list_id AS INTEGER) IS NULL
        OR c.list_id = CAST(:owner_list_id AS INTEGER)
      )
),
ranked AS (
    SELECT DISTINCT ON (owner_key_sql, property_key_sql)
        *
    FROM base
    ORDER BY
        owner_key_sql,
        property_key_sql,
        (status_rank >= 4) DESC,
        CASE WHEN status_rank >= 4 THEN transaction_date END DESC NULLS LAST,
        status_rank DESC,
        confidence_score DESC,
        transaction_date DESC NULLS LAST
),
properties AS (
    SELECT * FROM ranked
)
"""


def _owner_scope_clause(scope: str, alias: str = "p") -> str:
    ready = (
        f"{alias}.lead_id IS NOT NULL AND "
        f"{alias}.ownership_status IN "
        "('verified_current_owner', 'probable_current_owner')"
    )
    if scope == "rental_ready":
        return ready
    if scope == "prior_owners":
        return f"{alias}.ownership_status = 'former_owner'"
    if scope == "registry":
        return f"NOT ({ready}) AND {alias}.ownership_status <> 'former_owner'"
    return "TRUE"


def _owner_filter_sql(
    *,
    scope: str,
    status: str | None,
    search: str | None,
    lease_window: str | None = None,
    min_rent_aed: int | None = None,
    max_rent_aed: int | None = None,
    min_value_aed: int | None = None,
    max_value_aed: int | None = None,
    alias: str = "p",
) -> tuple[str, dict[str, Any]]:
    clauses = [_owner_scope_clause(scope, alias)]
    parameters: dict[str, Any] = {}
    if status:
        clauses.append(f"{alias}.ownership_status = :owner_status")
        parameters["owner_status"] = status
    if search:
        clauses.append(
            "lower(concat_ws(' ', "
            f"{alias}.contact_name, {alias}.phone_primary, "
            f"{alias}.phone_alternate, {alias}.email_primary, "
            f"{alias}.building_name, {alias}.project_name, "
            f"{alias}.area_name, {alias}.unit_number"
            ")) LIKE :owner_search"
        )
        parameters["owner_search"] = f"%{search.casefold().strip()}%"
    if lease_window == "expired":
        clauses.append(f"{alias}.lease_end < CURRENT_DATE")
    elif lease_window in {"expiring_30", "expiring_60", "expiring_90"}:
        lower_days, upper_days = {
            "expiring_30": (0, 30),
            "expiring_60": (30, 60),
            "expiring_90": (60, 90),
        }[lease_window]
        clauses.append(
            f"{alias}.lease_end > CURRENT_DATE + make_interval(days => :lease_lower_days)"
        )
        clauses.append(
            f"{alias}.lease_end <= CURRENT_DATE + make_interval(days => :lease_upper_days)"
        )
        parameters["lease_lower_days"] = lower_days
        parameters["lease_upper_days"] = upper_days
    if min_rent_aed is not None:
        clauses.append(f"{alias}.annual_rent_aed >= :owner_min_rent")
        parameters["owner_min_rent"] = min_rent_aed
    if max_rent_aed is not None:
        clauses.append(f"{alias}.annual_rent_aed <= :owner_max_rent")
        parameters["owner_max_rent"] = max_rent_aed
    if min_value_aed is not None:
        clauses.append(f"{alias}.latest_sale_price_aed >= :owner_min_value")
        parameters["owner_min_value"] = min_value_aed
    if max_value_aed is not None:
        clauses.append(f"{alias}.latest_sale_price_aed <= :owner_max_value")
        parameters["owner_max_value"] = max_value_aed
    return " AND ".join(f"({clause})" for clause in clauses), parameters


async def _owner_page_keys_sql(
    db: AsyncSession,
    *,
    organization_id: str,
    list_id: int | None,
    scope: str,
    status: str | None,
    search: str | None,
    lease_window: str | None,
    portfolio_only: bool,
    min_rent_aed: int | None,
    max_rent_aed: int | None,
    min_value_aed: int | None,
    max_value_aed: int | None,
    limit: int,
    offset: int,
    min_properties: int | None = None,
    next_event_days: int | None = None,
) -> tuple[list[str], int]:
    where_sql, parameters = _owner_filter_sql(
        scope=scope,
        status=status,
        search=search,
        lease_window=lease_window,
        min_rent_aed=min_rent_aed,
        max_rent_aed=max_rent_aed,
        min_value_aed=min_value_aed,
        max_value_aed=max_value_aed,
    )
    statement = text(
        _OWNER_REGISTRY_BASE_SQL
        + f"""
        , scoped_properties AS (
            SELECT p.* FROM properties p WHERE {where_sql}
        ),
        profile_rollup AS (
            SELECT
                owner_key_sql,
                count(*) AS property_count,
                count(*) FILTER (WHERE lead_id IS NOT NULL)
                    AS rental_linked_count,
                count(*) FILTER (
                    WHERE lead_id IS NOT NULL
                    AND ownership_status IN (
                        'verified_current_owner',
                        'probable_current_owner'
                    )
                ) AS contact_ready_count,
                max(confidence_score) AS highest_confidence_score,
                min(lease_end) FILTER (WHERE lease_end IS NOT NULL)
                    AS next_lease_end
            FROM scoped_properties
            GROUP BY owner_key_sql
        )
        SELECT
            owner_key_sql,
            count(*) OVER () AS total_profiles
        FROM profile_rollup
        WHERE (NOT :portfolio_only OR property_count > 1)
          AND (CAST(:min_properties AS integer) IS NULL
               OR property_count >= CAST(:min_properties AS integer))
          AND (CAST(:next_event_days AS integer) IS NULL
               OR (next_lease_end >= CURRENT_DATE
                   AND next_lease_end <= CURRENT_DATE
                       + make_interval(days => CAST(:next_event_days AS integer))))
        ORDER BY
            contact_ready_count DESC,
            property_count DESC,
            highest_confidence_score DESC,
            next_lease_end ASC NULLS LAST,
            owner_key_sql
        LIMIT :owner_limit OFFSET :owner_offset
        """
    )
    parameters.update(
        {
            "organization_id": organization_id,
            "owner_list_id": list_id,
            "portfolio_only": portfolio_only,
            "min_properties": min_properties,
            "next_event_days": next_event_days,
            "owner_limit": limit,
            "owner_offset": offset,
        }
    )
    rows = (await db.exec(statement, params=parameters)).mappings().all()
    return (
        [str(row["owner_key_sql"]) for row in rows],
        int(rows[0]["total_profiles"]) if rows else 0,
    )


async def _owner_properties_sql(
    db: AsyncSession,
    *,
    organization_id: str,
    list_id: int | None,
    owner_keys: list[str],
    scope: str,
    status: str | None = None,
    search: str | None = None,
    lease_window: str | None = None,
    min_rent_aed: int | None = None,
    max_rent_aed: int | None = None,
    min_value_aed: int | None = None,
    max_value_aed: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not owner_keys:
        return {}
    where_sql, parameters = _owner_filter_sql(
        scope=scope,
        status=status,
        search=search,
        lease_window=lease_window,
        min_rent_aed=min_rent_aed,
        max_rent_aed=max_rent_aed,
        min_value_aed=min_value_aed,
        max_value_aed=max_value_aed,
    )
    statement = text(
        _OWNER_REGISTRY_BASE_SQL
        + f"""
        , limited_properties AS (
            SELECT DISTINCT ON (owner_key_sql, property_key_sql)
                *
            FROM base
            WHERE owner_key_sql = ANY(:owner_keys)
            ORDER BY
                owner_key_sql,
                property_key_sql,
                (status_rank >= 4) DESC,
                CASE WHEN status_rank >= 4 THEN transaction_date END DESC NULLS LAST,
                status_rank DESC,
                confidence_score DESC,
                transaction_date DESC NULLS LAST
        ),
        limited_source_aggregation AS (
            SELECT
                c.owner_key AS owner_key_sql,
                c.property_key AS property_key_sql,
                count(*) AS duplicate_count,
                array_agg(DISTINCT l.name ORDER BY l.name)
                    AS source_lists,
                array_agg(
                    DISTINCT c.source_sheet || ':' || c.source_row_number::text
                    ORDER BY c.source_sheet || ':' || c.source_row_number::text
                ) AS source_rows
            FROM crm_property_contact_claims c
            INNER JOIN crm_contact_lists l ON l.id = c.list_id
            WHERE c.organization_id = :organization_id
              AND (
                CAST(:owner_list_id AS INTEGER) IS NULL
                OR c.list_id = CAST(:owner_list_id AS INTEGER)
              )
              AND c.owner_key = ANY(:owner_keys)
            GROUP BY c.owner_key, c.property_key
        ),
        scoped_properties AS (
            SELECT p.* FROM limited_properties p WHERE {where_sql}
        )
        SELECT
            p.*,
            a.duplicate_count,
            a.source_lists,
            a.source_rows
        FROM scoped_properties p
        INNER JOIN limited_source_aggregation a
            ON a.owner_key_sql = p.owner_key_sql
            AND a.property_key_sql = p.property_key_sql
        WHERE p.owner_key_sql = ANY(:owner_keys)
        ORDER BY
            p.owner_key_sql,
            p.status_rank DESC,
            p.confidence_score DESC,
            p.lease_end ASC NULLS LAST
        """
    )
    parameters.update(
        {
            "organization_id": organization_id,
            "owner_list_id": list_id,
            "owner_keys": owner_keys,
        }
    )
    rows = (await db.exec(statement, params=parameters)).mappings().all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["owner_key_sql"]), []).append(dict(row))
    return grouped


async def find_contact_list_claims(db: AsyncSession, list_id, organization_id, search):
    statement = select(CrmPropertyContactClaim).where(
        CrmPropertyContactClaim.organization_id == organization_id,
        CrmPropertyContactClaim.list_id == list_id,
    )
    if search:
        term = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                CrmPropertyContactClaim.contact_name.ilike(term),
                CrmPropertyContactClaim.building_name.ilike(term),
                CrmPropertyContactClaim.project_name.ilike(term),
                CrmPropertyContactClaim.unit_number.ilike(term),
            )
        )
    all_result = await db.exec(statement)
    return (all_result).all()


async def fetch_owner_registry_totals(db: AsyncSession, list_id, organization_id):
    statement = text(
        _OWNER_REGISTRY_BASE_SQL
        + """
        , profile_rollup AS (
            SELECT
                owner_key_sql,
                count(*) AS property_count,
                count(*) FILTER (
                    WHERE lead_id IS NOT NULL
                    AND ownership_status IN (
                        'verified_current_owner',
                        'probable_current_owner'
                    )
                ) AS rental_ready_count,
                count(*) FILTER (
                    WHERE NOT (
                        lead_id IS NOT NULL
                        AND ownership_status IN (
                            'verified_current_owner',
                            'probable_current_owner'
                        )
                    )
                    AND ownership_status <> 'former_owner'
                ) AS registry_count,
                count(*) FILTER (
                    WHERE ownership_status = 'former_owner'
                ) AS prior_count,
                count(*) FILTER (
                    WHERE ownership_status IN (
                        'unit_linked_unverified',
                        'conflicting_claim',
                        'insufficient_property_data',
                        'registry_not_observed',
                        'unmatched'
                    )
                ) AS review_count
            FROM properties
            GROUP BY owner_key_sql
        )
        SELECT
            count(*) FILTER (WHERE rental_ready_count > 0)
                AS rental_owner_profiles,
            count(*) FILTER (WHERE registry_count > 0)
                AS registry_owner_profiles,
            count(*) FILTER (WHERE prior_count > 0)
                AS prior_owner_profiles,
            count(*) FILTER (WHERE review_count > 0)
                AS needs_review_profiles,
            count(*) FILTER (WHERE property_count > 1)
                AS multi_property_owners
        FROM profile_rollup
        """
    )
    row = (
        (
            await db.exec(
                statement,
                params={"organization_id": organization_id, "owner_list_id": list_id},
            )
        )
        .mappings()
        .one()
    )
    return row


async def list_contact_lists(db: AsyncSession, organization_id):
    return (
        await db.exec(
            select(CrmContactList)
            .where(CrmContactList.organization_id == organization_id)
            .order_by(CrmContactList.updated_at.desc())
        )
    ).all()


async def list_claims_for_lists(db: AsyncSession, ids, organization_id):
    return (
        await db.exec(
            select(CrmPropertyContactClaim).where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.list_id.in_(ids),  # type: ignore[union-attr]
            )
        )
    ).all()


async def get_contact_list(db: AsyncSession, list_id, organization_id):
    return (
        await db.exec(
            select(CrmContactList).where(
                CrmContactList.id == list_id,
                CrmContactList.organization_id == organization_id,
            )
        )
    ).first()


async def list_claims_for_list(db: AsyncSession, list_id, organization_id):
    return (
        await db.exec(
            select(CrmPropertyContactClaim).where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.list_id == list_id,
            )
        )
    ).all()


async def list_lead_claims_with_lists(db: AsyncSession, lead_id, organization_id):
    return (
        await db.exec(
            select(CrmPropertyContactClaim, CrmContactList)
            .join(CrmContactList, CrmContactList.id == CrmPropertyContactClaim.list_id)
            .where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.lead_id == lead_id,
            )
            .order_by(CrmPropertyContactClaim.confidence_score.desc())
        )
    ).all()


async def count_owner_properties(db: AsyncSession, organization_id, owner_keys):
    return (
        await db.exec(
            select(
                CrmPropertyContactClaim.owner_key,
                func.count(func.distinct(CrmPropertyContactClaim.property_key)),
            )
            .where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.owner_key.in_(owner_keys),
            )
            .group_by(CrmPropertyContactClaim.owner_key)
        )
    ).all()


async def list_claims_with_lists(db: AsyncSession, organization_id):
    return (
        await db.exec(
            select(CrmPropertyContactClaim, CrmContactList)
            .join(CrmContactList, CrmContactList.id == CrmPropertyContactClaim.list_id)
            .where(CrmPropertyContactClaim.organization_id == organization_id)
        )
    ).all()
