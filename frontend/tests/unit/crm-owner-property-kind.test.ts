import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import { listOwners } from '$lib/crm/api/backend'
import { OWNER_PTYPE_OPTIONS, ownerPropertyKind } from '$lib/crm/config'

/* The Owners type filter offers every kind of property found in the owner files and DLD, and does
   not file a generic "Unit" or "Residential" row as an apartment. */
describe('ownerPropertyKind', () => {
	it.each([
		['Flat', 'apartment'],
		['Residential Flats', 'apartment'],
		['Apartment', 'apartment'],
		['Villa', 'villa'],
		['Townhouse', 'villa'],
		['Land', 'land'],
		['Plot', 'land'],
		['Hotel Apartment', 'hotel'],
		['Hotel Rooms', 'hotel'],
		['Commercial', 'commercial'],
		['Shop', 'commercial'],
		['Office', 'commercial'],
		['Show Rooms', 'commercial'],
		['Building', 'building'],
		['Unit', 'unspecified'],
		['Residential', 'unspecified'],
		['', 'unspecified'],
		['null', 'unspecified']
	])('reads %s as %s', (type, kind) => {
		expect(ownerPropertyKind(type)).toBe(kind)
	})

	it('offers apartment, villa, land, building and "Not specified" (no hotel or commercial option)', () => {
		expect(OWNER_PTYPE_OPTIONS.map((o) => o.id)).toEqual([
			'apartment',
			'villa',
			'land',
			'building',
			'unspecified'
		])
	})
})

describe('owner type filter', () => {
	const property = (type: string, registry?: string) => ({
		property_key: type,
		building_name: 'B',
		project_name: 'P',
		area_name: 'A',
		unit_number: '1',
		property_type: type,
		registry_property_type: registry ?? null,
		ownership_status: 'verified_current_owner',
		confidence_score: 98,
		match_reason: ''
	})
	const owner = (key: string, ...props: ReturnType<typeof property>[]) => ({
		owner_key: key,
		contact_name: key,
		highest_ownership_status: 'verified_current_owner',
		properties: props
	})

	afterEach(() => vi.restoreAllMocks())

	function serve() {
		vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
			if (url.includes('/owner-workspaces')) return [] as never
			return {
				owners: [
					owner('flat', property('Flat')),
					owner('unit', property('Unit')),
					owner('land', property('Land')),
					owner('dld-villa', property('Unit', 'Villa'))
				],
				total: 4,
				limit: 500,
				offset: 0,
				summary: {}
			} as never
		})
	}

	it('keeps a generic "Unit" out of Apartment', async () => {
		serve()
		const res = await listOwners({ tab: 'all', ptype: 'apartment' })
		expect(res.items.map((o) => o.name)).toEqual(['flat'])
	})

	it('shows generic rows under "Not specified"', async () => {
		serve()
		const res = await listOwners({ tab: 'all', ptype: 'unspecified' })
		expect(res.items.map((o) => o.name)).toEqual(['unit'])
	})

	it("prefers DLD's type when the owner panel has it", async () => {
		serve()
		const res = await listOwners({ tab: 'all', ptype: 'villa' })
		expect(res.items.map((o) => o.name)).toEqual(['dld-villa'])
	})

	it('finds land', async () => {
		serve()
		const res = await listOwners({ tab: 'all', ptype: 'land' })
		expect(res.items.map((o) => o.name)).toEqual(['land'])
	})
})
