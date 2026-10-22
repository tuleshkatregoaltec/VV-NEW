import { describe, expect, it } from 'vitest'

import type { LeadType } from '$lib/crm/api/types'
import { LEAD_TYPES } from '$lib/crm/config'

// The backend only matches a lead once its brief is complete: buyers and tenants need an area and
// a budget, sellers and landlords a building and a unit number. The New Lead form asks for exactly
// those, so no lead is created that can never be matched.
const required = (t: LeadType) => LEAD_TYPES[t].fields.filter((f) => f.required).map((f) => f.key)

describe('New Lead form: required fields per lead type', () => {
	it('buyers need an area and a budget', () => {
		expect(LEAD_TYPES.buyer.areaRequired).toBe(true)
		expect(required('buyer')).toEqual(['budget_aed'])
	})

	it('tenants need an area and a yearly budget', () => {
		expect(LEAD_TYPES.tenant.areaRequired).toBe(true)
		expect(required('tenant')).toEqual(['budget_aed'])
	})

	it('sellers and landlords need the building and the unit number, as two separate values', () => {
		for (const t of ['seller', 'landlord'] as const) {
			expect(LEAD_TYPES[t].areaRequired ?? false).toBe(false)
			expect(required(t)).toEqual(['building', 'unit_number'])
			// The old single free-text "Unit" was saved where the backend never looks.
			expect(LEAD_TYPES[t].fields.map((f) => f.key)).not.toContain('unit')
		}
	})
})
