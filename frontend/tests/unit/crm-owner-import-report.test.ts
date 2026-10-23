import { describe, expect, it } from 'vitest'
import {
	clampOffset,
	failureReasons,
	importCounts,
	isFlagged,
	mappingChecks
} from '$lib/crm/ownerImportReport'

/* Real summaries and sheet mappings from imports #14 (Westwood) and #15 (JVT JAN 2025). */
const JVT_SUMMARY = {
	total_rows: 5764,
	candidate_rows: 2141,
	duplicate_rows_collapsed: 45,
	valid_contacts: 1615,
	verified_current_owners: 466,
	probable_current_owners: 6,
	unit_linked_unverified: 0,
	former_owners: 1489,
	conflicting_claims: 0,
	insufficient_property_data: 180,
	registry_not_observed: 0,
	unmatched: 0,
	rental_opportunities: 0,
	contact_ready: 0
}
const JVT_OLD = {
	sheet_family: 'transaction_party',
	field_map: {
		master_project: 'Master Project',
		unit_number: 'UnitNumber',
		transaction_date: 'Regis',
		transaction_price_aed: 'ProcedureValue',
		party_role: 'ProcedurePartyTypeNameEn',
		procedure_type: 'ProcedureNameEn',
		contact_name: 'NameEn',
		phone_primary: 'Mobile'
	}
}
const JVT_APR = {
	sheet_family: 'transaction_party',
	field_map: {
		master_project: 'Master Project',
		project_name: 'Project',
		unit_number: 'UnitNumber',
		transaction_date: 'Date',
		contact_name: 'Owner Name',
		phone_alternate: 'Mobile 2',
		building_name: 'BuildingName 2',
		phone_primary: 'Phone 1'
	}
}
const WESTWOOD = {
	sheet_family: 'contact_list',
	field_map: {
		unit_number: 'UNIT NUMBER',
		contact_name: 'OWNER NAME',
		phone_primary: 'MOBILE',
		phone_alternate: 'SECONDARY MOBILE',
		email_primary: 'EMAIL'
	}
}

describe('importCounts', () => {
	it('derives the funnel a manager reads for JVT', () => {
		const c = importCounts(JVT_SUMMARY)
		expect(c.rows).toBe(5764)
		expect(c.kept).toBe(2141)
		expect(c.merged).toBe(45)
		expect(c.dropped).toBe(3578)
		expect(c.matched).toBe(1961)
		expect(c.failed).toBe(180)
		expect(c.matchRate).toBeCloseTo(0.916, 3)
		expect(c.byStatus.former_owner).toBe(1489)
	})

	it('reads an import still processing (empty summary) as zeros, not NaN', () => {
		const c = importCounts({})
		expect([c.rows, c.kept, c.merged, c.dropped, c.matched, c.failed, c.readyToContact]).toEqual([
			0, 0, 0, 0, 0, 0, 0
		])
		expect(c.matchRate).toBeNull()
	})

	it('keeps source identifiers, derived groups and plot-only evidence separate', () => {
		const c = importCounts({
			distinct_properties: 7586,
			source_property_counts: { distinct_identifiers: 3470, source_property_references: 5000 },
			rows_with_sales_history: 100,
			plot_only_rows: 200
		})
		expect(c.properties).toBe(7586)
		expect(c.sourceProperties?.distinct_identifiers).toBe(3470)
		expect(c.salesHistoryRows).toBe(100)
		expect(c.plotOnlyRows).toBe(200)
		expect(importCounts({}).sourceProperties).toBeNull()
	})

	it('shows no match rate when nothing was kept', () => {
		expect(importCounts({ total_rows: 2203, candidate_rows: 0 }).matchRate).toBeNull()
	})

	it('never reports a negative dropped count', () => {
		expect(
			importCounts({ total_rows: 10, candidate_rows: 8, duplicate_rows_collapsed: 5 }).dropped
		).toBe(0)
	})
})

describe('mappingChecks', () => {
	it('passes a complete DLD transaction sheet', () => {
		expect(mappingChecks(JVT_OLD)).toEqual([])
	})

	it('warns that missing roles require review without dropping records', () => {
		const checks = mappingChecks(JVT_APR)
		expect(checks.map((c) => c.code)).toEqual(['no_role', 'no_price'])
		expect(checks[0].level).toBe('warning')
		expect(checks[0].text).toBe(
			'No party-role column: source records are retained for ownership review.'
		)
	})

	it('catches a contact list that cannot be tied to a property', () => {
		const checks = mappingChecks(WESTWOOD)
		expect(checks.map((c) => c.code)).toEqual(['no_property'])
		expect(checks[0].text).toBe(
			"No building or project and no date + price: rows can't be tied to a property."
		)
	})

	it('accepts master_project as the property, as the backend falls back to it', () => {
		const sheet = {
			sheet_family: 'contact_list',
			field_map: {
				contact_name: 'Name',
				master_project: 'Community',
				unit_number: 'Unit',
				phone_primary: 'Mobile'
			}
		}
		expect(mappingChecks(sheet).map((c) => c.code)).toEqual([])
	})

	it('accepts a project name the backend infers from the file name', () => {
		const sheet = {
			...WESTWOOD,
			warnings: ['Property name will be inferred as Elan.']
		}
		expect(mappingChecks(sheet).map((c) => c.code)).toEqual([])
	})

	it('catches a sheet with a building but no unit column and no date + price', () => {
		const sheet = {
			sheet_family: 'contact_list',
			field_map: { contact_name: 'Name', building_name: 'Building', phone_primary: 'Mobile' }
		}
		const checks = mappingChecks(sheet)
		expect(checks.map((c) => c.code)).toEqual(['no_unit'])
		expect(checks[0].level).toBe('error')
		expect(checks[0].text).toBe(
			"No unit column and no date + price: rows can't be matched to a unit."
		)
	})

	it('does not ask for a unit column when date + price can recover the property', () => {
		const sheet = { ...JVT_OLD, field_map: { ...JVT_OLD.field_map, unit_number: '' } }
		expect(mappingChecks(sheet).map((c) => c.code)).toEqual([])
	})

	it('flags a sheet with no name and no contact columns', () => {
		const codes = mappingChecks({ sheet_family: 'contact_list', field_map: {} }).map((c) => c.code)
		expect(codes).toContain('no_name')
		expect(codes).toContain('no_contact')
	})
})

describe('isFlagged', () => {
	it('flags an import with any error-level sheet check', () => {
		expect(isFlagged('completed', [JVT_OLD, WESTWOOD])).toBe(true)
	})

	it('does not flag a clean completed import', () => {
		expect(isFlagged('completed', [JVT_OLD])).toBe(false)
	})

	it('flags a failed import', () => {
		expect(isFlagged('failed', [JVT_OLD])).toBe(true)
	})
})

describe('clampOffset', () => {
	it('moves to the last page when the rows shrank under the current page', () => {
		expect(clampOffset(200, 150, 100)).toBe(100)
	})

	it('keeps a page that still has rows', () => {
		expect(clampOffset(100, 150, 100)).toBe(100)
	})

	it('goes to the first page when no rows are left', () => {
		expect(clampOffset(300, 0, 100)).toBe(0)
	})
})

describe('failureReasons', () => {
	it('explains why rows failed to match, biggest reason first, with a fix for each', () => {
		const c = importCounts({
			candidate_rows: 281,
			unmatched: 104,
			insufficient_property_data: 1,
			registry_not_observed: 0,
			former_owners: 176
		})
		const reasons = failureReasons(c)
		expect(reasons.map((r) => [r.status, r.count])).toEqual([
			['unmatched', 104],
			['insufficient_property_data', 1]
		])
		expect(reasons[0].label).toBe('Needs review')
		expect(reasons[0].fix).toBeTruthy()
	})

	it('is empty when nothing failed', () => {
		expect(
			failureReasons(importCounts({ candidate_rows: 10, verified_current_owners: 10 }))
		).toEqual([])
	})
})

describe('mortgage category', () => {
	// Damac Hills 2 (import 113): mortgages move out of "unverified" and "needs review" into their
	// own tile, so those tiles show only the rows that still need work. Totals stay the same.
	const DH2 = { candidate_rows: 31393, unit_linked_unverified: 3752, unmatched: 2381 }
	const MORTGAGES = { unit_linked_unverified: 2371, unmatched: 973 }

	it('reports mortgages as their own category', () => {
		const c = importCounts(DH2, MORTGAGES)
		expect(c.byStatus.mortgage).toBe(3344)
		expect(c.byStatus.unit_linked_unverified).toBe(1381)
		expect(c.byStatus.unmatched).toBe(1408)
	})

	it('keeps the matched and failed totals unchanged', () => {
		const before = importCounts(DH2)
		const after = importCounts(DH2, MORTGAGES)
		expect(after.matched).toBe(before.matched)
		expect(after.failed).toBe(before.failed)
	})

	it('shows no mortgages for an import without the breakdown', () => {
		expect(importCounts(DH2).byStatus.mortgage).toBe(0)
	})
})
