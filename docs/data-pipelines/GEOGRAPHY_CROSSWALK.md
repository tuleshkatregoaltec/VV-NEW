# Canonical market geography

**Status:** code ready (draft PR), measured on a local copy of the warehouse on 2026-10-03.
Not staging accepted.

## Problem

DLD sales arrive in two vocabularies. Dubai Pulse history (to 2025-12-31) names the official
district (`area_en = Marsa Dubai`, with an `area_id`) and the master project (`Dubai Marina`).
Open data (from 2026-01-01) has `area_id = 0` and an area label that is sometimes the district
(`Madinat Al Mataar`) and sometimes the community (`DUBAI MARINA`, `JUMEIRAH VILLAGE CIRCLE`),
usually without a master project. Area lists, rankings and metrics grouped by the raw column
split one place in two, and each consumer kept its own alias list.

## Model

Every served sale and rental row (`market_sale_events`, `market_rental_events`, and so
`ch_transactions` / `ch_rent_contracts`) carries:

| Column | Meaning |
|---|---|
| `district_id`, `district_name` | Official DLD district. `district_id` is DLD's `area_id`, the stable identifier. |
| `community_key`, `community_name` | DLD master project the row belongs to, when evidence says so. The key is the lower-case, single-spaced name. |
| `geo_resolution` | `community`, `district` or `unresolved`. |
| `geo_method` | `pulse_master`, `project_evidence`, `district_name`, `label_evidence` or `unresolved`. |

Raw names (`area_name_en`, `master_project_en`, `project_name_en`) are unchanged.

The evidence is `market_geo_crosswalk`, rebuilt from bronze with every sales release and
promoted with it; rentals read the served crosswalk, so both datasets place rows the same way.

| Kind | Rule | Rows (local copy) |
|---|---|---|
| `district` | Each area name DLD tags with an `area_id` (Pulse sales and Ejari) | 263 |
| `community` | Each Pulse master project; its district when ≥ 80% of its rows sit in one | 177 |
| `project` | A Pulse project whose rows sit in one (district, master) ≥ 80% of ≥ 3 times | 2,647 |
| `label` | An open-data label that is not a district: the community of the same name, else the (district, community) of its project-located rows ≥ 80% of ≥ 20, else the district alone if that reaches 80% | 82 |

A row takes the first rule that applies: its own Pulse master project, then project evidence,
then its own district name, then label evidence. A row DLD locates only by a wide district keeps
that district and no community. Marsa Dubai also contains JBR, Dubai Harbour and Bluewaters, so a
lease registered only in Marsa Dubai is never counted as Dubai Marina. Community answers exclude
such rows and state how many there are.

## Measured result (served sales since 2023)

| Source | pulse_master | project_evidence | district_name | label_evidence | unresolved |
|---|---|---|---|---|---|
| Dubai Pulse | 85.6% | 0.2% | 14.1% | — | 0 |
| DLD open data | — | 59.9% | 25.5% | 13.4% | 1.2% |
| DXBI only | — | 3.9% | 84.5% | 1.4% | 10.2% |

DLD rent rows since 2024 all have a district; 26% (open data) and 35% (Pulse) also have a
community. Served 2026 sales go from 320 raw area labels to 181 districts. The open-data
`DUBAI MARINA` label (3,005 sales in 2026) now sits in district Marsa Dubai and community
Dubai Marina with the Pulse rows. Remaining unresolved open-data labels are new projects with no
Pulse history (`HORIZON`, `VILLANOVA`, `MIRA`). DXBI-only rows with DXBI district spellings
(`Me'Aisem 1`) stay unresolved. Both are disclosed, not guessed.

Blocking release checks: one crosswalk entry per key, one label vocabulary, open-data sales
unresolved ≤ 3% (1.2% measured), DLD rent rows unresolved ≤ 1% (0% measured).

## Consumers

- `app.market.reference.community_location` with one alias table (`COMMUNITY_KEYS`) and
  `app.market.scope.LOCATION_PREDICATE`. These are used by the shared metrics (API and chat) and,
  through `PROJECT_LOCATION_PREDICATE`, by delivery.
- `group_by=area` is the official district and `group_by=community` the community.
- Area rankings and the area list (`ch_area_fact`) are keyed by the official district.

Not yet migrated: `analytics/chart_queries.py` area resolution, transaction facets and the
project/radar queries still read raw `area_name_en`.
