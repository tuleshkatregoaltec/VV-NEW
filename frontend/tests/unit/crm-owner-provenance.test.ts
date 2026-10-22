import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import { getOwner } from '$lib/crm/api/backend'

/* The owner panel says which uploaded file each owner and unit came from (sheet and row). */
const unit = (number: string, lists: string[], rows: string[]) => ({
	property_key: `polaris|${number}`,
	building_name: 'POLARIS',
	project_name: 'POLARIS',
	area_name: 'Business Bay',
	unit_number: number,
	property_type: 'Unit',
	ownership_status: 'verified_current_owner',
	confidence_score: 98,
	match_reason: '',
	source_lists: lists,
	source_rows: rows
})

function serve(properties: ReturnType<typeof unit>[]) {
	vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
		if (url.startsWith('/api/v1/crm/owner-workspaces')) return [] as never
		if (url.startsWith('/api/v1/crm/contact-imports'))
			return [
				{
					id: 2,
					name: 'Business Bay 2024 SVR',
					original_filename: 'Business Bay 2024 SVR.xlsx',
					status: 'completed',
					summary: {},
					created_at: '2026-07-29T17:30:49Z'
				},
				{
					id: 8,
					name: 'Dubai Hills Elite',
					original_filename: 'Dubai Hills Elite.xlsx',
					status: 'completed',
					summary: {},
					created_at: '2026-08-20T12:41:58Z'
				}
			] as never
		if (url.startsWith('/api/v1/crm/owners/'))
			return {
				owner_key: 'fahed',
				contact_name: 'FAHED HUSSEIN HAJ HUSSEIN',
				highest_ownership_status: 'verified_current_owner',
				properties
			} as never
		throw new Error(`unexpected GET ${url}`)
	})
}

afterEach(() => vi.restoreAllMocks())

describe('owner provenance', () => {
	it('lists each import the owner came from, with its file, date and unit count', async () => {
		serve([
			unit('805', ['Business Bay 2024 SVR'], ['Sheet1:12']),
			unit('2023', ['Business Bay 2024 SVR'], ['Sheet1:40']),
			unit('9', ['Dubai Hills Elite'], ['Old:7'])
		])
		const o = await getOwner('fahed')
		expect(o.imported_from).toEqual([
			{
				importId: 2,
				name: 'Business Bay 2024 SVR',
				file: 'Business Bay 2024 SVR.xlsx',
				date: '2026-07-29',
				units: 2
			},
			{
				importId: 8,
				name: 'Dubai Hills Elite',
				file: 'Dubai Hills Elite.xlsx',
				date: '2026-08-20',
				units: 1
			}
		])
	})

	it('names the file, sheet and row on each unit', async () => {
		serve([unit('805', ['Business Bay 2024 SVR'], ['Sheet1:12', 'Sheet1:90'])])
		const o = await getOwner('fahed')
		expect(o.unit_rows[0].source).toBe('Business Bay 2024 SVR · Sheet1 row 12, Sheet1 row 90')
	})

	it('keeps an import whose file is no longer in the import history, without file or date', async () => {
		serve([unit('1', ['Old list'], ['Sheet1:3'])])
		const o = await getOwner('fahed')
		expect(o.imported_from).toEqual([
			{ importId: null, name: 'Old list', file: null, date: null, units: 1 }
		])
	})
})
