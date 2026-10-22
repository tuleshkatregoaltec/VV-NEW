# Uniform market layer: what changed

Generated 2026-10-06 17:43 UTC by `holocron/scripts/report_uniform_layer_changes.py` on the local warehouse
copy (handoff snapshot). Before = frozen copy of the release built with the code on `VV` before
this change (`uniform_baseline_sale_events`, `uniform_baseline_rental_events`); after = the published uniform release
(sales `4cd2ea3a9088`, rentals
`9b7aaabe3002`). These are local pre-production numbers; re-run
the script on staging/production after deployment. Aggregates only.

## Rules (decided 2026-10-06)

- **DXBI is the primary source** from 2022-01-01 (sales) and 2025-01-01 (rentals).
  On a matched pair DXBI's value wins whenever DXBI has one; DLD fills empty or generic values
  and registry-only fields (procedure, registration type, parties, Arabic names).
- **DLD fills the gaps**: records only DLD holds are still served. Before those dates DLD is the
  row source and DXBI only fills DLD's empty values.
- **No double counting**: an unmatched DXBI record replaces its probable DLD duplicate one for
  one; a DXBI record whose DLD counterpart already stands for another DXBI record stays hidden.
- **Hidden source label** (`match_status`, `primary_source`, `filled_from_dld`,
  `dld_disagrees`): kept for audit, never displayed in the product.
- **One vocabulary**: property type (DXBI's words), bedrooms (`Studio`, `N B/R`), location
  (DXBI's community names, one spelling), size in m², rentals dated by contract start.

## Totals

| Dataset | Served before | Served after | Change | DXBI-primary rows in its period |
|---|---:|---:|---:|---:|
| Sales | 1,411,430 | 1,411,430 | 0 (+0.00%) | 685,106 of 765,500 (89.5%) |
| Rentals | 10,334,201 | 10,334,201 | 0 (+0.00%) | 1,375,610 of 1,897,699 (72.5%) |

Served record counts are unchanged by design: every swap is one DXBI record for one DLD
record. What changes is which source supplies each record and its values.

### Sales by year (Sales group only, by served date)

| Year | Records before | Records after | Change | Change % | Value before (AED) | Value after (AED) | Value change % |
|---|---|---|---|---|---|---|---|
| 2018 | 33,469 | 33,469 | 0 | +0.00% | 76,328,787,809 | 76,328,787,809 | +0.00% |
| 2019 | 38,338 | 38,338 | 0 | +0.00% | 77,996,405,969 | 77,996,405,969 | +0.00% |
| 2020 | 34,660 | 34,660 | 0 | +0.00% | 71,494,280,603 | 71,494,280,603 | +0.00% |
| 2021 | 61,268 | 61,261 | -7 | -0.01% | 150,899,472,500 | 150,892,777,500 | -0.00% |
| 2022 | 97,723 | 97,796 | 73 | +0.07% | 265,802,025,882 | 266,040,767,328 | +0.09% |
| 2023 | 135,853 | 135,787 | -66 | -0.05% | 415,209,651,347 | 414,977,604,901 | -0.06% |
| 2024 | 185,441 | 185,591 | 150 | +0.08% | 530,778,781,243 | 531,067,148,993 | +0.05% |
| 2025 | 222,281 | 222,131 | -150 | -0.07% | 699,922,733,858 | 699,636,777,609 | -0.04% |
| 2026 | 124,195 | 124,195 | 0 | +0.00% | 379,144,236,136 | 379,141,824,635 | -0.00% |

### Rentals by year (by contract start, annual rent)

| Year | Records before | Records after | Change | Change % | Value before (AED) | Value after (AED) | Value change % |
|---|---|---|---|---|---|---|---|
| 2021 | 769,364 | 769,364 | 0 | +0.00% | 210,451,447,553 | 54,460,734,905 | -74.12% |
| 2022 | 895,096 | 895,096 | 0 | +0.00% | 211,371,239,401 | 60,921,675,455 | -71.18% |
| 2023 | 998,946 | 998,946 | 0 | +0.00% | 343,246,339,519 | 77,068,961,541 | -77.55% |
| 2024 | 1,147,786 | 1,147,786 | 0 | +0.00% | 361,981,473,416 | 86,279,311,885 | -76.16% |
| 2025 | 1,224,285 | 1,224,285 | 0 | +0.00% | 487,876,895,089 | 106,447,097,290 | -78.18% |
| 2026 | 672,978 | 672,978 | 0 | +0.00% | 110,304,799,184 | 74,520,825,325 | -32.44% |

Record counts do not move. Sales value moves only where DXBI's price or date replaces DLD's.
Rental value falls because a multi-unit contract's rent was counted once per unit (see "Data
errors found"): each row now carries its unit's share, and DXBI's per-unit rent where it
holds the unit.

## Which source supplies each record (hidden label)

### Sales, DXBI period (from 2022-01-01)

| Hidden label | Served | Rows | Share of served | Meaning |
|---|---|---|---|---|
| matched | yes | 621,587 | 64.9% | DXBI and DLD records of the same event, one row |
| dld_only | yes | 273,063 | 28.5% | only DLD has it (DLD fills the gap) |
| dxbi_preferred | yes | 39,424 | 4.1% | DXBI record served; its probable DLD duplicate hidden |
| dxbi_only | yes | 24,095 | 2.5% | only DXBI has it |
| dld_probable_duplicate | no | 39,417 | – | hidden: DLD copy of a served DXBI record |
| dxbi_probable_duplicate | no | 21,436 | – | hidden: DXBI copy of a served record |

### Rentals, DXBI period (from 2025-01-01)

| Hidden label | Served | Rows | Share of served | Meaning |
|---|---|---|---|---|
| matched | yes | 1,133,227 | 59.7% | DXBI and DLD records of the same event, one row |
| dld_only | yes | 522,089 | 27.5% | only DLD has it (DLD fills the gap) |
| dxbi_preferred | yes | 225,263 | 11.9% | DXBI record served; its probable DLD duplicate hidden |
| dxbi_only | yes | 17,120 | 0.9% | only DXBI has it |
| dxbi_probable_duplicate | no | 256,864 | – | hidden: DXBI copy of a served record |
| dld_probable_duplicate | no | 225,263 | – | hidden: DLD copy of a served DXBI record |

## Values DXBI replaced and gaps DLD filled (matched records, DXBI period)

### Sales

Matched rows in the DXBI period: **621,587**.

| Column | DXBI value replaced DLD's | % of matched | DLD filled empty DXBI | % of matched |
|---|---|---|---|---|
| instance_date | 134,847 | 21.7% | 0 | 0.0% |
| is_offplan_en | 64,141 | 10.3% | 404 | 0.1% |
| property_type | 66,770 | 10.7% | 207 | 0.0% |
| rooms_en | 1,849 | 0.3% | 2,663 | 0.4% |
| size_sqm | 9,742 | 1.6% | 35 | 0.0% |

### Rentals

Matched rows in the DXBI period: **1,133,227**.

| Column | DXBI value replaced DLD's | % of matched | DLD filled empty DXBI | % of matched |
|---|---|---|---|---|
| annual_amount | 154,922 | 13.7% | 10 | 0.0% |
| contract_end_date | 6,334 | 0.6% | 0 | 0.0% |
| property_type | 16,625 | 1.5% | 333,883 | 29.5% |
| rooms_en | 0 | 0.0% | 9,528 | 0.8% |
| size_sqm | 64,666 | 5.7% | 3,330 | 0.3% |

### Sales dates

DXBI dates a share of matched sales one business day before DLD registers them (a Monday in
DLD is the previous Friday in DXBI). The served date is DXBI's; DLD's registration date is kept
in the hidden `dld_instance_date` column, and release validation checks completeness per day
on it.

| DXBI date minus DLD date (days) | Matched sales | Share |
|---|---|---|
| 0 | 486,740 | 78.3% |
| -1 | 105,709 | 17.0% |
| -3 | 27,167 | 4.4% |
| 1 | 1,429 | 0.2% |
| 3 | 303 | 0.0% |

## Vocabulary shown on Transactions and Analytics

### Sales property type

| Before (DLD words) | Records | After (uniform list) | Records |
|---|---|---|---|
| Unit | 1,092,246 | Apartment | 994,107 |
| Villa | 179,712 | Villa | 237,001 |
| Land | 114,435 | Plot | 67,793 |
| Building | 25,021 | Office | 58,918 |
| (blank) | 16 | Hotel Apartment | 23,349 |
|  |  | Shop | 14,824 |
|  |  | Building | 14,000 |
|  |  | Warehouse | 868 |
|  |  | Townhouse | 471 |
|  |  | Other | 76 |
|  |  | (blank) | 23 |

### Rental property type

| Before (DLD words) | Records | After (uniform list) | Records |
|---|---|---|---|
| Flat | 5,026,388 | Apartment | 5,591,528 |
| Office | 1,391,173 | Office | 1,415,136 |
| Labor Camps | 1,102,981 | Labour Accommodation | 1,119,878 |
| Shop | 866,579 | Shop | 941,385 |
| Unit | 625,658 | Villa | 673,763 |
| Villa | 623,191 | Warehouse | 254,754 |
| Warehouse | 226,933 | Hotel Apartment | 98,897 |
| Studio | 78,984 | Commercial | 91,967 |
| Hotel | 75,459 | (blank) | 72,311 |
| (blank) | 68,089 | Other | 50,689 |
| Complex Villas | 57,338 | Townhouse | 14,924 |
| Showroom | 34,907 | Plot | 5,226 |
| Virtual Unit | 30,778 | Building | 3,743 |
| Hotel apartments | 19,460 |  |  |
| Portacabin | 11,451 |  |  |

### Locations and bedrooms

| Measure | Before | After |
|---|---:|---:|
| Distinct sales location names | 403 | 645 |
| Distinct rental location names | 257 | 399 |
| Sales with a bedroom label | 88.8% | 85.1% |
| Rentals with a bedroom label | 0.3% | 17.8% |

Before, the location list mixed DLD's official districts and open-data labels (`DUBAI MARINA`,
`Marsa Dubai`); after, it uses DXBI's community names with one spelling per place (`Damac
Hills 2`/`DAMAC HILLS 2`, `Dubailand`/`Dubai Land` merged), DLD names only where DXBI has none.

## Data errors found while building the layer

| Finding | Evidence | Handling |
|---|---|---|
| DLD repeats a multi-unit rental contract's whole rent on every unit line | Matched tier-2 rentals (same lease and floor area): 95,542 rows, 80,160 multi-unit; DLD rent on them AED 125,511,946,813 vs DXBI's per-unit AED 6,505,997,456 | Each DLD row now carries its unit's share (contract rent / units; decided 2026-10-06), and DXBI's own per-unit rent wins where DXBI holds the unit. This is most of the rental value change above: the old totals counted each contract's rent once per unit. |
| Off-plan villas registered by DLD against the plot ("Land") | 46,319 matched sales typed Land by DLD and Villa by DXBI (39,109 of them "Delayed Sell") | DXBI's Villa wins; sales typed Plot fall and Villa rises accordingly. |
| DXBI dates 21% of matched sales one business day before DLD registration | table above | DXBI's date served; DLD's kept hidden for validation. |
| DLD's bedroom column holds type words ('Office', 'Shop', 'NA', 'PENTHOUSE') | sales bedroom coverage before counts them | Treated as unknown bedrooms (coverage drops from the "before" figure for that reason). |
| DLD rentals carry no bedroom count; DXBI-only rentals lost theirs | rentals with a bedroom label: 0.3% before | DXBI's bedrooms used; DXBI-only rows fixed. |
| Property types in different words per source; DLD hides the type in a sub-type ("Unit" / "Labor Camps") | type tables above | One list in DXBI's words; generic or blank values filled from the matched record. |
| One place spelled several ways ('DAMAC HILLS 2' / 'Damac Hills 2', 'Dubai Land' / 'Dubailand', all-capitals DLD labels) | 7 colliding names before | One spelling per place (DXBI's), all-capitals DLD labels title-cased. |
| Analytics read DLD `actual_area` as sales size (0 on 91% of sales) and DLD "Unit" (offices, shops) as residential | backend queries | Pages read the uniform size and type columns. |
| Transactions rentals "last N days" included leases starting in the future | 164 of 34,170 JVC residential leases (365 days) | Window bounded at today, as Analytics already was; the two pages now agree. |
| Summary medians were sampled (changed between refreshes) | 70.79 vs 70.61 m² on identical requests | Exact medians on the summaries. |

### DLD-only multi-unit rentals (rent now split per unit)

| Year | Multi-unit DLD rows | All DLD-only rows | Share of DLD-only annual value |
|---|---|---|---|
| 2021 | 134,077 | 769,364 | 10.9% |
| 2022 | 153,671 | 895,096 | 10.6% |
| 2023 | 180,991 | 998,946 | 10.0% |
| 2024 | 210,644 | 1,147,786 | 10.5% |
| 2025 | 115,253 | 302,728 | 29.1% |
| 2026 | 13,477 | 213,521 | 6.3% |

Before the split, these lines carried most of DLD's annual rent value although they are a
fifth of the rows. Splitting keeps every contract's total (its rows add up to its rent) and
gives each unit a realistic rent.
