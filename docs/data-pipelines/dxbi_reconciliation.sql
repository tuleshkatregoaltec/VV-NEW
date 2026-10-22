-- DXBI reconciliation queries behind DXBI_RECONCILIATION.md.
-- Read-only. Run against the warehouse the report describes, e.g.
--   cd holocron && set -a && . ./.env.local && set +a
--   clickhouse-client (or any HTTP client) with each statement below.
-- Numbers in the report come from the handoff snapshot (source-Mac warehouse, 2026-09-21).

-- Q1 DXBI object inventory
SELECT t.name, t.engine, t.total_rows, t.total_bytes, c.n_columns
FROM system.tables t
LEFT JOIN (SELECT table, count() AS n_columns FROM system.columns WHERE database = currentDatabase() GROUP BY table) c ON c.table = t.name
WHERE t.database = currentDatabase() AND t.name LIKE 'dxbi%'
ORDER BY t.name;

-- Q2 Event coverage of the evidence tables
SELECT 'rental_events' AS t, count() AS rows, min(lease_start) AS min_event, max(lease_start) AS max_event, max(loaded_at) AS loaded FROM dxbi_rental_events
UNION ALL SELECT 'rental_events_v2_clean', count(), min(lease_start), max(lease_start), max(loaded_at) FROM dxbi_rental_events_v2_clean
UNION ALL SELECT 'rental_events_v2', count(), min(lease_start), max(lease_start), max(loaded_at) FROM dxbi_rental_events_v2
UNION ALL SELECT 'rental_events_legacy', count(), min(lease_start), max(lease_start), max(loaded_at) FROM dxbi_rental_events_legacy
UNION ALL SELECT 'sales_unit_events', count(), min(transaction_date), max(transaction_date), max(loaded_at) FROM dxbi_sales_unit_events FINAL;

-- Q3 Rental loader ledger (raw -> unique -> loaded, quarantine, out of scope)
SELECT run_id, status, raw_rows, unique_source_rows, normalized_rows, quarantined_rows, profile_overlap_rows,
       out_of_scope_rows, loaded_contracts, expected_partitions, completed_partitions, failed_partitions, finished_at
FROM dxbi_rental_run_reconciliation FINAL ORDER BY started_at;

-- Q4 Active rental table vs v2_clean: shared source rows (lease_start <= 2026-09-15)
WITH a AS (SELECT DISTINCT raw_row_fingerprint AS f FROM dxbi_rental_events WHERE lease_start <= '2026-09-15'),
     c AS (SELECT DISTINCT raw_row_fingerprint AS f FROM dxbi_rental_events_v2_clean WHERE lease_start <= '2026-09-15')
SELECT (SELECT count() FROM a) AS active, (SELECT count() FROM c) AS v2_clean,
       (SELECT count() FROM a WHERE f IN (SELECT f FROM c)) AS both,
       (SELECT count() FROM c WHERE f NOT IN (SELECT f FROM a)) AS only_v2_clean,
       (SELECT count() FROM a WHERE f NOT IN (SELECT f FROM c)) AS only_active;

-- Q5 Rentals per month: DLD Ejari contracts (contract_number; Pulse rows only carry it) vs DXBI tables
WITH dld AS (SELECT toStartOfMonth(toDate(start_date)) AS m, uniqExact(contract_number) AS dld_contracts
             FROM dld_od_rents_bronze WHERE start_date >= '2025-01-01' AND start_date < '2026-10-01' GROUP BY m),
     act AS (SELECT toStartOfMonth(lease_start) AS m, uniqExact(tuple(lease_start, lease_end, building_name, unit_number, annual_rent_aed, size_sqft)) AS dxbi_active FROM dxbi_rental_events GROUP BY m),
     cln AS (SELECT toStartOfMonth(lease_start) AS m, uniqExact(tuple(lease_start, lease_end, building_name, unit_number, annual_rent_aed, size_sqft)) AS dxbi_v2_clean FROM dxbi_rental_events_v2_clean GROUP BY m)
SELECT dld.m, dld_contracts, dxbi_active, round(dxbi_active / dld_contracts * 100, 1) AS active_pct,
       dxbi_v2_clean, round(dxbi_v2_clean / dld_contracts * 100, 1) AS clean_pct
FROM dld LEFT JOIN act USING m LEFT JOIN cln USING m ORDER BY dld.m;

-- Q6 DLD rent rows per start month by source (open-data rows have no contract_number)
SELECT toStartOfMonth(toDate(start_date)) AS m, source_system, count() AS rows,
       countIf(contract_number = '') AS empty_contract_number
FROM dld_od_rents_bronze WHERE start_date >= '2025-09-01' AND start_date < '2026-10-01'
GROUP BY m, source_system ORDER BY m, source_system;

-- Q7 Sales per year: DLD Sales vs DXBI unit-bearing sales
WITH dld AS (SELECT toYear(instance_date) AS y, uniqExact(transaction_number) AS dld_sales FROM dld_od_transactions_bronze WHERE group_en = 'Sales' GROUP BY y),
     dx AS (SELECT toYear(transaction_date) AS y, count() AS dxbi_sales FROM dxbi_sales_unit_events FINAL GROUP BY y)
SELECT y, dld_sales, dxbi_sales, round(dxbi_sales / dld_sales * 100, 1) AS dxbi_pct
FROM dld LEFT JOIN dx USING y WHERE y >= 2014 ORDER BY y;

-- Q8 Record-level sales match 2025: DXBI (date +/- 3 days, exact price) found in DLD Sales
WITH dld AS (SELECT DISTINCT toDate(instance_date) AS d, toUInt64(trans_value) AS p FROM dld_od_transactions_bronze
             WHERE group_en = 'Sales' AND instance_date >= '2024-12-25' AND instance_date < '2026-01-05'),
     dx AS (SELECT DISTINCT transaction_date AS d, sale_amount_aed AS p FROM dxbi_sales_unit_events FINAL
            WHERE transaction_date >= '2025-01-01' AND transaction_date < '2026-01-01'),
     dx_exp AS (SELECT d, p, d + toIntervalDay(k) AS dd FROM dx ARRAY JOIN [-3,-2,-1,0,1,2,3] AS k)
SELECT (SELECT count() FROM dx) AS dxbi_pairs,
       (SELECT uniqExact(d, p) FROM dx_exp WHERE (dd, p) IN (SELECT d, p FROM dld)) AS matched,
       round(matched / dxbi_pairs * 100, 1) AS match_pct;

-- Q9 Record-level rental match 2025-06: DXBI (lease start, contract amount) found in DLD (start, contract amount)
WITH dld AS (SELECT DISTINCT toDate(start_date) AS d, toUInt64(contract_amount) AS p FROM dld_od_rents_bronze WHERE start_date >= '2025-06-01' AND start_date < '2025-07-01'),
     dx AS (SELECT DISTINCT lease_start AS d, contract_amount_aed AS p FROM dxbi_rental_events WHERE lease_start >= '2025-06-01' AND lease_start < '2025-07-01')
SELECT (SELECT count() FROM dx) AS dxbi, (SELECT count() FROM dx WHERE (d, p) IN (SELECT d, p FROM dld)) AS matched,
       round(matched / dxbi * 100, 1) AS match_pct;

-- Q10 CRM chain: rental events -> unit candidates -> sale matches
SELECT (SELECT count() FROM dxbi_rental_events) AS rental_events,
       (SELECT countIf(unit_number != '') FROM dxbi_rental_events) AS events_with_unit,
       (SELECT count() FROM dxbi_rental_unit_candidates FINAL) AS unit_candidates,
       (SELECT count() FROM dxbi_rental_sale_matches FINAL) AS sale_matches;
SELECT match_status, count() AS candidates, sum(matching_sales) AS sales_rows FROM dxbi_rental_sale_matches FINAL GROUP BY match_status;

-- Q11 Geography crosswalk coverage by source (rows represented)
SELECT l.source_system, l.status, count() AS signatures, sum(s.row_count) AS rows_represented
FROM geo_entity_links AS l FINAL
LEFT JOIN (SELECT source_system, source_key, any(row_count) AS row_count FROM geo_source_signatures FINAL GROUP BY source_system, source_key) AS s
  ON s.source_system = l.source_system AND s.source_key = l.source_key
GROUP BY l.source_system, l.status ORDER BY l.source_system, rows_represented DESC;

-- Q12 Building identity in DXBI rentals
SELECT property_type, count() AS rows, countIf(building_name = '') AS no_building, round(no_building / rows * 100, 1) AS pct
FROM dxbi_rental_events GROUP BY property_type ORDER BY rows DESC;

-- Q13 Label drift between DLD sources
SELECT source_system, group_en, count() AS rows FROM dld_od_transactions_bronze GROUP BY 1, 2 ORDER BY 1, 3 DESC;
SELECT source_system, version_en, count() AS rows FROM dld_od_rents_bronze GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- Q14 Open-data identity columns
SELECT count() AS od_rent_rows, countIf(contract_number != '') AS with_contract_number FROM dld_od_rents_bronze WHERE source_system = 'dld_open_data';
SELECT count() AS od_txn_rows, countIf(building_name_en != '') AS with_building FROM dld_od_transactions_bronze WHERE source_system = 'dld_open_data';

-- Q15 Map layer vs evidence building coverage
SELECT (SELECT uniqExact(JSONExtractString(payload_json, 'Building', 'location')) FROM dxbi_heatmap_buildings_bronze) AS map_buildings,
       (SELECT uniqExact(building_name) FROM dxbi_rental_events WHERE building_name != '') AS rental_buildings,
       (SELECT uniqExact(building_name) FROM dxbi_sales_unit_events FINAL WHERE building_name != '') AS sales_buildings,
       (SELECT uniqExact(building_number) FROM dld_od_buildings_bronze) AS dld_buildings;
