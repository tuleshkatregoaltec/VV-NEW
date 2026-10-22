import { describe, expect, it } from 'vitest'

import type { OwnerUnitCheck } from '$lib/crm/api/types'
import { matchLines } from '$lib/crm/ownerMatch'

/* The owner panel's Matching lines say, field by field, whether the owner file agrees with DLD's
   latest sale, by the import's own rules: same sale within 2 days, price within 2%. */
const check = (over: {
	imported?: Partial<OwnerUnitCheck['imported']>
	dld?: Partial<OwnerUnitCheck['dld']>
}): OwnerUnitCheck => ({
	imported: {
		date: '2024-02-27',
		aed: 1_100_000,
		type: 'Unit',
		unit: 'G01',
		role: 'Buyer',
		...over.imported
	},
	dld: {
		unit: 'G01',
		building: 'Al Ghozlan 4',
		area: 'Greens',
		date: '2024-02-27',
		aed: 1_100_000,
		type: 'Apartment',
		bedrooms: '1 Bed',
		size_sqft: 1042,
		built_up_sqft: null,
		aed_per_sqft: 1056,
		market: 'Ready',
		...over.dld
	},
	result: {
		label: 'Verified owner',
		confidence: 98,
		reason: '',
		chip: { tone: 'green', label: 'Verified owner' }
	},
	lease: null
})
const byLabel = (c: OwnerUnitCheck) => Object.fromEntries(matchLines(c).map((m) => [m.label, m]))

describe('matchLines', () => {
	it('marks a verified owner: same unit, same sale, price within 2%', () => {
		const m = byLabel(check({ imported: { aed: 1_110_000 } }))
		expect(m.Unit.result).toBe('match')
		expect(m['Sale date']).toMatchObject({ result: 'match', note: 'Same sale (within 2 days)' })
		expect(m.Price).toMatchObject({ result: 'match', note: 'Within 2%' })
		expect(m.Role.note).toBe('Buyer in the file: checked against DLD sales')
	})

	it('explains a former owner: DLD has a later sale and the price is not compared', () => {
		const m = byLabel(
			check({
				imported: {
					date: '2024-03-25',
					aed: 2_060_000,
					type: 'Land',
					unit: 'JVT03B1TH066',
					role: 'Seller'
				},
				dld: { unit: 'jvt03b1th066', date: '2025-05-27', aed: 3_175_000, type: 'Villa' }
			})
		)
		expect(m.Unit.result).toBe('match')
		expect(m['Sale date']).toMatchObject({ result: 'differs' })
		expect(m['Sale date'].note).toContain('later sale, 428 days after')
		expect(m.Price.note).toBe('Not compared: not the same sale')
		expect(m['Property type']).toMatchObject({
			result: 'info',
			note: 'Different · not used for matching'
		})
		expect(m.Role.note).toBe('Seller in the file: a former owner')
	})

	it('flags a price that differs on the same sale', () => {
		const m = byLabel(check({ imported: { aed: 1_300_000 } }))
		expect(m.Price).toMatchObject({ result: 'differs', note: 'Differs by 18.2%' })
	})

	it('says when there is nothing to compare', () => {
		const m = byLabel(
			check({ imported: { role: '' }, dld: { unit: '', date: null, aed: null, type: '' } })
		)
		expect(m.Unit.note).toBe('No DLD sale found for this unit')
		expect(m['Sale date'].result).toBe('none')
		expect(m.Price.result).toBe('none')
		expect(m.Role.note).toBe('No role in the file')
	})
})
