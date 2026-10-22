import { describe, expect, it } from 'vitest'

import { insufficientReason } from '$lib/crm/ownerImportReport'

/* "Insufficient property data" rows say what was missing, so the manager knows what to add to the
   file: the unit, the building / project, or both — and whether a sale date and price were there. */
describe('insufficientReason', () => {
	const row = (over: Partial<Parameters<typeof insufficientReason>[0]>) => ({
		building: 'Jumeirah Village Triangle',
		unit: '',
		date: '2024-03-21',
		price: 3_700_000,
		...over
	})

	it('names a missing unit and says the sale did not identify the property', () => {
		expect(insufficientReason(row({}))).toBe(
			'No unit number in the file. Its sale date and price did not match one DLD sale in this location.'
		)
	})

	it('names a missing building / project', () => {
		expect(insufficientReason(row({ building: '', unit: '1203' }))).toBe(
			'No building or project in the file. Its sale date and price did not match one DLD sale in this location.'
		)
	})

	it('names both when both are missing', () => {
		expect(insufficientReason(row({ building: '' }))).toBe(
			'No unit number and no building or project in the file. Its sale date and price did not match one DLD sale in this location.'
		)
	})

	it('says when there was no sale date and price to look up', () => {
		expect(insufficientReason(row({ date: null, price: null }))).toBe(
			'No unit number in the file, and no sale date and price to look the property up in DLD.'
		)
	})

	it('treats the text "null" as missing', () => {
		expect(insufficientReason(row({ unit: 'null' }))).toMatch(/^No unit number in the file/)
	})
})
