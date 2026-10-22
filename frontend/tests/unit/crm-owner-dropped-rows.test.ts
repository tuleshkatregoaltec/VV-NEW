import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import { listOwnerImportDropped } from '$lib/crm/api/backend'
import { DROP_REASON_INFO, dropDetail, importCounts } from '$lib/crm/ownerImportReport'

/* Clicking "Dropped" on an import report lists the rows the import did not keep and why, so an
   agent can see whether a file lost owners it should have kept. */
afterEach(() => vi.restoreAllMocks())

describe('dropped rows of an owner file', () => {
	it('read the reason, the file values and the source row from the log', async () => {
		const get = vi.spyOn(client, 'get').mockResolvedValue({
			available: true,
			total: 1,
			offset: 0,
			limit: 100,
			by_reason: { not_buyer_seller: 410, no_name: 120 },
			rows: [
				{
					sheet: 'Old',
					row: 57,
					reason: 'not_buyer_seller',
					name: 'Example Bank',
					building: 'Panorama',
					unit: 'T2-203',
					date: '2024-01-12',
					price: '1400000',
					role: 'Mortgagee',
					procedure: 'Mortgage Registration'
				}
			]
		})

		const page = await listOwnerImportDropped(56, {
			reason: 'not_buyer_seller',
			search: ' bank ',
			offset: 0
		})

		const url = String(get.mock.calls[0][0])
		expect(url).toContain('/contact-imports/56/dropped-rows')
		expect(url).toContain('reason=not_buyer_seller')
		expect(url).toContain('search=bank')
		expect(page.available).toBe(true)
		expect(page.byReason.not_buyer_seller).toBe(410)
		expect(page.rows[0]).toMatchObject({
			sheet: 'Old',
			rowNumber: 57,
			reason: 'not_buyer_seller',
			role: 'Mortgagee',
			procedure: 'Mortgage Registration'
		})
	})

	it('say when an import was made before the log existed', async () => {
		vi.spyOn(client, 'get').mockResolvedValue({
			available: false,
			total: 0,
			offset: 0,
			limit: 100,
			by_reason: {},
			rows: []
		})

		const page = await listOwnerImportDropped(12, { reason: null, search: '', offset: 0 })

		expect(page.available).toBe(false)
		expect(page.rows).toEqual([])
	})

	it('count dropped rows per reason on the report', () => {
		const counts = importCounts({
			total_rows: 928,
			candidate_rows: 318,
			duplicate_rows_collapsed: 12,
			dropped_by_reason: { no_name: 120, not_buyer_seller: 410, not_a_sale: 60, no_unit: 8 },
			dropped_log: true
		})

		expect(counts.dropped).toBe(598)
		expect(Object.values(counts.droppedByReason).reduce((a, b) => a + b, 0)).toBe(598)
		expect(counts.droppedLog).toBe(true)
		expect(Object.keys(DROP_REASON_INFO)).toEqual([
			'no_name',
			'not_buyer_seller',
			'not_a_sale',
			'no_unit'
		])
	})

	it('explain a mortgage row in full: dropped before DLD matching, with its procedure', () => {
		// Greens "Old" row 55: ALISTAIR JAMES BUSH, AL GHOZLAN 3 · 719, Seller, Mortgage Registration.
		const d = dropDetail({
			reason: 'not_a_sale',
			role: 'Seller',
			procedure: 'Mortgage Registration',
			date: '2022-12-23',
			price: '1140000'
		})

		expect(d.stage).toBe('before_match')
		expect(d.what).toContain('“Mortgage Registration”')
		expect(d.rule).toContain('“sale”, “sell” or “delayed sell”')
	})

	it('tell a sheet without a role column apart from a bank row', () => {
		const empty = dropDetail({
			reason: 'not_buyer_seller',
			role: '',
			procedure: '',
			date: '',
			price: ''
		})
		const bank = dropDetail({
			reason: 'not_buyer_seller',
			role: 'Mortgagee',
			procedure: '',
			date: '',
			price: ''
		})

		expect(empty.what).toContain('no Buyer/Seller column')
		expect(empty.fix).toContain('map its role column')
		expect(bank.what).toContain('“Mortgagee”')
	})

	it('place a missing unit after DLD matching', () => {
		const d = dropDetail({
			reason: 'no_unit',
			role: 'Buyer',
			procedure: 'Sell',
			date: '2024-02-01',
			price: '900000'
		})

		expect(d.stage).toBe('after_match')
		expect(d.what).toContain('2024-02-01, 900000')
	})
})
