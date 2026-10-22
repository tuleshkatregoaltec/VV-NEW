import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import { getOwner, listOwnerImportRows } from '$lib/crm/api/backend'

/* A row on an import report opens its owner, and each of the owner's units shows what the file
   said next to what DLD has, with the match result, as the old CRM's owner profile did. */
afterEach(() => vi.restoreAllMocks())

describe('import report rows', () => {
	it('carry the owner key the Owner panel opens with', async () => {
		vi.spyOn(client, 'get').mockResolvedValue({
			list: { id: 5 },
			contacts: [
				{
					id: 1,
					owner_key: 'abc123',
					contact_name: 'WALDEMAR ROTFUSS',
					phone_primary: '+971581465291',
					area_name: 'Greens',
					project_name: 'Greens',
					building_name: 'Al Ghozlan 4',
					unit_number: 'G01',
					property_type: 'Unit',
					transaction_date: '2024-02-27',
					transaction_price_aed: 1_100_000,
					ownership_status: 'verified_current_owner',
					confidence_score: 98,
					match_reason: 'Date and price match the latest sale.',
					source_sheet: 'Old',
					source_row_number: 12
				}
			],
			total: 1,
			limit: 100,
			offset: 0
		} as never)
		const page = await listOwnerImportRows(5, { status: null, search: '', offset: 0 })
		expect(page.rows[0]).toMatchObject({ ownerKey: 'abc123', unit: 'G01' })
	})
})

describe('owner unit check', () => {
	function serve(property: Record<string, unknown>) {
		vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
			if (url.startsWith('/api/v1/crm/owner-workspaces')) return [] as never
			if (url.startsWith('/api/v1/crm/contact-imports')) return [] as never
			if (url.startsWith('/api/v1/crm/owners/'))
				return {
					owner_key: 'abc123',
					contact_name: 'WALDEMAR ROTFUSS',
					highest_ownership_status: 'verified_current_owner',
					properties: [
						{
							property_key: 'alghozlan4|g01',
							building_name: 'Al Ghozlan 4',
							project_name: 'Greens',
							area_name: 'Greens',
							unit_number: 'G01',
							property_type: 'Unit',
							source_lists: ['Greens JAN 2025'],
							source_rows: ['Old:12'],
							...property
						}
					]
				} as never
			throw new Error(`unexpected GET ${url}`)
		})
	}

	it('shows the file record, the DLD record, the result and the lease', async () => {
		serve({
			ownership_status: 'verified_current_owner',
			confidence_score: 98,
			match_reason: 'Acquisition date and price match the latest sale.',
			imported_transaction_date: '2024-02-27',
			imported_transaction_price_aed: 1_100_000,
			imported_property_type: 'Unit',
			registry_sale_date: '2024-02-27',
			registry_sale_price_aed: 1_100_000,
			registry_property_type: 'Apartment',
			registry_bedrooms: '1 Bed',
			registry_size_sqft: 1042,
			registry_price_per_sqft_aed: 1056,
			registry_market_status: 'Ready',
			registry_unit_number: 'G01',
			registry_building_name: 'Al Ghozlan 4',
			registry_area_name: 'Greens',
			imported_party_role: 'Buyer',
			lease_end: '2026-09-22',
			annual_rent_aed: 120_000
		})
		const o = await getOwner('abc123')
		expect(o.unit_rows[0].check).toEqual({
			imported: { date: '2024-02-27', aed: 1_100_000, type: 'Unit', unit: 'G01', role: 'Buyer' },
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
				market: 'Ready'
			},
			result: {
				label: 'Verified owner',
				confidence: 98,
				reason: 'Acquisition date and price match the latest sale.',
				chip: { tone: 'green', label: 'Verified owner' }
			},
			lease: { end: '2026-09-22', aed: 120_000 }
		})
	})

	it('uses the import report wording and leaves missing DLD values empty', async () => {
		serve({
			ownership_status: 'former_owner',
			confidence_score: 100,
			match_reason: 'The uploaded record identifies this contact as the seller.'
		})
		const c = (await getOwner('abc123')).unit_rows[0].check
		expect(c?.result.label).toBe('Former owner')
		expect(c?.dld).toMatchObject({
			unit: '',
			building: '',
			date: null,
			aed: null,
			type: '',
			bedrooms: ''
		})
		expect(c?.imported.role).toBe('')
		expect(c?.lease).toBeNull()
	})
})
