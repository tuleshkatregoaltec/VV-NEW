import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import { listOwners } from '$lib/crm/api/backend'

/* The Owners page pages through the whole agency file (73k owners), 500 at a time. */
const owner = (key: string) => ({
	owner_key: key,
	contact_name: key.toUpperCase(),
	highest_ownership_status: 'verified_current_owner',
	properties: []
})

function mockBackend(total: number, owners = [owner('a'), owner('b')]) {
	return vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
		if (url.includes('/owner-workspaces')) return [] as never
		return { owners, total, limit: 500, offset: 500, summary: {} } as never
	})
}

afterEach(() => vi.restoreAllMocks())

describe('listOwners paging', () => {
	it('asks the backend for the requested page', async () => {
		const get = mockBackend(73224)
		await listOwners({ tab: 'all', offset: 500 })
		const registryUrl = get.mock.calls.map(([u]) => u).find((u) => u.includes('/owner-registry'))
		expect(registryUrl).toContain('offset=500')
		expect(registryUrl).toContain('limit=500')
	})

	it('reports where the page sits in the whole file', async () => {
		mockBackend(73224)
		const res = await listOwners({ tab: 'all', offset: 500 })
		expect(res.page).toEqual({ offset: 500, limit: 500, total: 73224, loaded: 2 })
	})

	it('counts All as the whole file when no page-only filter is set', async () => {
		mockBackend(73224)
		const res = await listOwners({ tab: 'all', offset: 0 })
		expect(res.counts.all).toBe(73224)
	})

	it('counts All on this page when a page-only filter is set', async () => {
		mockBackend(73224)
		const res = await listOwners({ tab: 'all', offset: 0, area: 'JVC' })
		expect(res.counts.all).toBe(0)
	})

	it('starts at the first page when no offset is given', async () => {
		const get = mockBackend(10)
		await listOwners({ tab: 'all' })
		const registryUrl = get.mock.calls.map(([u]) => u).find((u) => u.includes('/owner-registry'))
		expect(registryUrl).toContain('offset=0')
	})
})

/* Every tab the backend can filter (All, Just sold, 3+ units, Next event) counts the whole file. */
function mockFile({ all = 73224, sold = 900, multi = 1187, event = 9086, workspaces = 3 } = {}) {
	return vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
		if (url.includes('/owner-workspaces'))
			return Array.from({ length: workspaces }, (_, i) => ({ owner_key: `w${i}` })) as never
		const total = url.includes('scope=prior_owners')
			? sold
			: url.includes('min_properties=3')
				? multi
				: url.includes('next_event_days=90')
					? event
					: all
		return { owners: [owner('a'), owner('b')], total, limit: 500, offset: 0, summary: {} } as never
	})
}
const registryUrls = (get: ReturnType<typeof mockFile>) =>
	get.mock.calls.map(([u]) => u).filter((u) => u.includes('/owner-registry'))

describe('listOwners tab counts across the whole file', () => {
	it('lists Just sold from every former owner, paged', async () => {
		const get = mockFile()
		const res = await listOwners({ tab: 'sold', offset: 500 })
		const main = registryUrls(get).find((u) => u.includes('offset=500'))
		expect(main).toContain('scope=prior_owners')
		expect(res.page.total).toBe(900)
		expect(res.items).toHaveLength(2)
	})

	it('lists 3+ units from every owner with at least 3 units, paged', async () => {
		const get = mockFile()
		const res = await listOwners({ tab: 'multi', offset: 500 })
		const main = registryUrls(get).find((u) => u.includes('offset=500'))
		expect(main).toContain('min_properties=3')
		expect(res.page.total).toBe(1187)
		expect(res.items).toHaveLength(2)
	})

	it('lists Next event from every owner whose lease ends within 90 days, paged', async () => {
		const get = mockFile()
		const res = await listOwners({ tab: 'event', offset: 500 })
		const main = registryUrls(get).find((u) => u.includes('offset=500'))
		expect(main).toContain('next_event_days=90')
		expect(res.page.total).toBe(9086)
	})

	it('counts every backend tab over the whole file on any tab', async () => {
		mockFile()
		for (const tab of ['all', 'sold', 'multi', 'event'] as const) {
			const res = await listOwners({ tab, offset: 0 })
			expect(res.counts).toMatchObject({ all: 73224, sold: 900, multi: 1187, event: 9086 })
		}
	})

	it('counts My contacts from the whole workspace list', async () => {
		mockFile({ workspaces: 3 })
		const res = await listOwners({ tab: 'all', offset: 0 })
		expect(res.counts.mine).toBe(3)
	})

	it('no longer marks any tab as counted on this page only', async () => {
		mockFile()
		const res = await listOwners({ tab: 'all', offset: 0 })
		expect(res.pageOnlyTabs).toEqual([])
	})
})

describe('listOwners page size', () => {
	it('asks the backend for the chosen number of owners per page', async () => {
		const get = mockFile()
		const res = await listOwners({ tab: 'all', offset: 100, limit: 50 })
		const main = registryUrls(get).find((u) => u.includes('offset=100'))
		expect(main).toContain('limit=50')
		expect(res.page.limit).toBe(50)
	})

	it('keeps 500 per page when no size is given', async () => {
		const get = mockFile()
		const res = await listOwners({ tab: 'all', offset: 0 })
		expect(registryUrls(get)[0]).toContain('limit=500')
		expect(res.page.limit).toBe(500)
	})
})
