import { afterEach, describe, expect, it, vi } from 'vitest'

import { client } from '$lib/api/client'
import type {
	AuthMeResponse,
	CrmLead,
	CrmOwnerProfile,
	CrmRenewalLeadsResponse,
	InboundEnquiryResponse
} from '$lib/api/generated/hey-api/types.gen'
import * as backend from '$lib/crm/api/backend'

const ME: AuthMeResponse = {
	id: 'u-1',
	email: 'sara@agency.ae',
	first_name: 'Sara',
	last_name: 'Ali',
	organization_id: 'org-1',
	organization_name: 'Agency',
	role: 'member',
	is_owner: false,
	subscription_status: 'active'
} as AuthMeResponse

function enquiry(overrides: Partial<InboundEnquiryResponse> = {}): InboundEnquiryResponse {
	return {
		id: 7,
		contact: {
			id: 1,
			full_name: 'Omar Khan',
			phone: '+971500000000',
			consent_status: 'provided',
			do_not_contact: false,
			registry_profiles: []
		},
		record_kind: 'opportunity',
		intent: 'buy',
		status: 'new',
		assigned_user_id: 'u-1',
		source: 'Bayut',
		enquiry_date: new Date().toISOString(),
		criteria: {
			bedrooms_min: 2,
			property_type: 'Apartment',
			locations: ['JVC'],
			budget_max_aed: 1_500_000
		},
		strict_fields: [],
		observed_data: {},
		inferred_data: {},
		confirmed_data: {},
		qualification: {},
		extraction_confidence: 1,
		review_reasons: [],
		sla_state: 'on_time',
		qualification_complete: false,
		tags: [],
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
		...overrides
	} as InboundEnquiryResponse
}

/** Route client.get by path so each test states only the backend data it cares about. */
function serve(routes: Record<string, unknown>) {
	return vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
		const path = url.split('?')[0]
		if (path === '/api/v1/auth/me') return ME
		if (path in routes) return routes[path]
		throw new Error(`unexpected GET ${url}`)
	})
}

afterEach(() => vi.restoreAllMocks())

describe('CRM leads from /api/v1/crm/inbound', () => {
	it('keeps unqualified contacts (intent unknown) out of Leads: they are Inbox work, not a typed lead', async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': {
				enquiries: [
					enquiry({ id: 1 }),
					enquiry({ id: 2, intent: 'unknown', record_kind: 'unqualified_contact' })
				],
				total: 2,
				summary: {}
			}
		})
		const res = await backend.listLeads({})
		expect(res.items.map((l) => l.id)).toEqual([1])
		expect(res.counts.all).toBe(1)
	})

	it('maps backend statuses onto the stage bar of each lead type', async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': {
				enquiries: [
					enquiry({ id: 1, intent: 'sell', status: 'signed' }),
					enquiry({ id: 2, intent: 'buy', status: 'attempted' }),
					enquiry({ id: 3, intent: 'rent', status: 'disqualified' })
				],
				total: 3,
				summary: {}
			}
		})
		const res = await backend.listLeads({})
		// A signed seller instruction is "Listed"; an attempt counts as Contacted.
		expect(res.items.map((l) => [l.id, l.stage])).toEqual([
			[1, 'listed'],
			[2, 'contacted']
		])
		// Disqualified leads only show under Lost.
		expect(res.counts.lost).toBe(1)
		expect((await backend.listLeads({ lost: true })).items.map((l) => l.id)).toEqual([3])
	})

	it('reads criteria into the buyer brief the Leads table shows', async () => {
		serve({ '/api/v1/crm/inbound/enquiries/7': enquiry() })
		const lead = await backend.getLead(7)
		expect(lead.fields.wants).toBe('2 bed Apartment in JVC')
		expect(lead.fields.budget_aed).toBe(1_500_000)
		expect(lead.area).toBe('JVC')
		expect(lead.agent).toBe('Sara Ali')
	})

	it('does not offer Undo or a linked deal on a backend lead: neither is recorded yet', async () => {
		serve({ '/api/v1/crm/inbound/enquiries/7': enquiry() })
		const lead = await backend.getLead(7)
		expect(lead.can_undo).toBe(false)
		expect(lead.deal).toBeNull()
	})

	it('refuses the Offer stage instead of reporting a move the backend cannot store', async () => {
		serve({ '/api/v1/crm/inbound/enquiries/7': enquiry() })
		const patch = vi.spyOn(client, 'patch')
		await expect(backend.moveLeadStage(7, { stage: 'offer' })).rejects.toMatchObject({
			status: 501
		})
		expect(patch).not.toHaveBeenCalled()
	})

	it('writes edited fields back under the criteria keys the backend matcher reads', async () => {
		serve({ '/api/v1/crm/inbound/enquiries/7': enquiry() })
		const patch = vi.spyOn(client, 'patch').mockResolvedValue(enquiry())
		await backend.patchLead(7, { fields: { budget_aed: 2_000_000, areas: 'JVC, Dubai Marina' } })
		expect(patch).toHaveBeenCalledWith('/api/v1/crm/inbound/enquiries/7', {
			criteria: expect.objectContaining({
				budget_max_aed: 2_000_000,
				locations: ['JVC', 'Dubai Marina']
			})
		})
	})
})

describe('New Lead form saves every field of every lead type (POST /api/v1/crm/inbound/enquiries)', () => {
	// What the agent typed must reach the backend under the keys it stores and matches on, and
	// come back unchanged when the lead is opened; a lost field is a lost part of the brief.
	const cases = [
		{
			type: 'buyer',
			intent: 'buy',
			fields: {
				wants: '2 bed apartment',
				budget_aed: 1_500_000,
				finance: 'Mortgage',
				purpose: 'Invest',
				timeline: '3 months'
			},
			criteria: {
				wants: '2 bed apartment',
				budget_max_aed: 1_500_000,
				financing: 'Mortgage',
				purpose: 'Invest',
				timeline: '3 months'
			}
		},
		{
			type: 'seller',
			intent: 'sell',
			fields: {
				building: 'Bloom Towers A',
				unit_number: '1204',
				target_price_aed: 2_100_000,
				occupancy: 'Vacant',
				mortgage: 'None',
				timeline: 'This month'
			},
			criteria: {
				building: 'Bloom Towers A',
				unit_number: '1204',
				expected_price_aed: 2_100_000,
				occupancy: 'Vacant',
				mortgage: 'None',
				timeline: 'This month'
			}
		},
		{
			type: 'tenant',
			intent: 'rent',
			fields: { wants: 'Studio', budget_aed: 65_000, cheques: '4', move_in: 'December' },
			criteria: { wants: 'Studio', budget_max_aed: 65_000, cheques: '4', move_in: 'December' }
		},
		{
			type: 'landlord',
			intent: 'let',
			fields: {
				building: 'Marina Heights',
				unit_number: '3301',
				expected_rent_aed: 120_000,
				available: 'January',
				furnishing: 'Furnished'
			},
			criteria: {
				building: 'Marina Heights',
				unit_number: '3301',
				expected_rent_aed: 120_000,
				available: 'January',
				furnishing: 'Furnished'
			}
		}
	] as const

	for (const c of cases) {
		it(`${c.type}: sends every field and reads it back`, async () => {
			serve({})
			const post = vi
				.spyOn(client, 'post')
				.mockImplementation(async (_url: string, body: unknown) => {
					// The backend stores criteria as given (a JSON column), so echo it back.
					const b = body as {
						intent: string
						full_name: string
						criteria: Record<string, unknown>
					}
					return enquiry({
						intent: b.intent as InboundEnquiryResponse['intent'],
						criteria: b.criteria,
						contact: { ...enquiry().contact, full_name: b.full_name }
					})
				})

			const res = await backend.createLead({
				name: 'Test Lead',
				phone: '050 123 4567',
				source: 'inbound',
				source_detail: 'Property Finder',
				lead_type: c.type,
				area: 'JVC, Dubai Marina',
				fields: { ...c.fields }
			})

			expect(post).toHaveBeenCalledWith('/api/v1/crm/inbound/enquiries', {
				intent: c.intent,
				record_kind: 'opportunity',
				full_name: 'Test Lead',
				phone: '050 123 4567',
				source: 'Property Finder',
				criteria: { ...c.criteria, locations: ['JVC', 'Dubai Marina'] }
			})
			expect(res.data.name).toBe('Test Lead')
			expect(res.data.fields).toMatchObject({ ...c.fields, areas: 'JVC, Dubai Marina' })
			// The Leads table headline shows the first area; the full list stays in the brief.
			expect(res.data.area).toBe('JVC')
		})
	}
})

describe('CRM owners from /api/v1/crm/owner-registry', () => {
	const owner = (key: string, units: number, leaseInDays: number | null): CrmOwnerProfile => {
		const end =
			leaseInDays === null
				? null
				: new Date(Date.now() + leaseInDays * 864e5).toISOString().slice(0, 10)
		return {
			owner_key: key,
			contact_name: `Owner ${key}`,
			highest_ownership_status: 'verified_current_owner',
			highest_confidence_score: 90,
			property_count: units,
			rental_linked_count: 0,
			contact_ready_count: units,
			annual_rent_aed: 0,
			next_lease_end: end,
			properties: Array.from({ length: units }, (_, i) => ({
				property_key: `${key}-${i}`,
				area_name: 'JVC',
				project_name: 'Bloom',
				building_name: 'Bloom Towers',
				unit_number: String(100 + i),
				property_type: 'Apartment',
				ownership_status: 'verified_current_owner',
				confidence_score: 90,
				match_reason: ''
			}))
		} as CrmOwnerProfile
	}

	it('counts the tabs the agent filters by (3+ units, a lease ending within 90 days) on the backend, for the whole file', async () => {
		const page = {
			owners: [owner('a', 3, 30), owner('b', 1, 200), owner('c', 1, null)],
			limit: 500,
			offset: 0,
			summary: {}
		}
		vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
			if (url.startsWith('/api/v1/crm/owner-workspaces')) return [] as never
			if (!url.startsWith('/api/v1/crm/owner-registry')) throw new Error(`unexpected GET ${url}`)
			// The backend applies the tab filters: one owner has 3+ units, one a lease within 90 days.
			const total = url.includes('min_properties=3') || url.includes('next_event_days=90') ? 1 : 3
			return { ...page, total } as never
		})
		const res = await backend.listOwners({ tab: 'all' })
		expect(res.counts).toMatchObject({ all: 3, multi: 1, event: 1, mine: 0 })
		expect(res.items[0].units).toEqual([
			'Bloom Towers #100',
			'Bloom Towers #101',
			'Bloom Towers #102'
		])
	})

	it('keeps the DLD 02-2026 gate on: no owner has a prior deal on record yet', async () => {
		serve({
			'/api/v1/crm/owners/a': owner('a', 1, 30),
			'/api/v1/crm/owner-workspaces': [],
			'/api/v1/crm/contact-imports': []
		})
		const o = await backend.getOwner('a')
		expect(o.prior_deal).toBe(false)
		expect(o.create_lead).toBeNull()
	})
})

describe('CRM market listings from /api/v1/listings', () => {
	const card = (id: string, price: number, estimate: number | null) => ({
		listing_id: id,
		listing_mode: 'sale',
		title: 'Listing',
		tower_name: 'Marina Gate 1',
		bedrooms: '2',
		area_name: 'Dubai Marina',
		price_value: price,
		market_estimate_aed: estimate,
		listing_age_days: 12
	})

	it('computes the discount from price vs matching sales and drops listings with no estimate', async () => {
		serve({
			'/api/v1/listings': {
				listings: [
					card('a', 900_000, 1_000_000),
					card('b', 1_100_000, 1_000_000),
					card('c', 1, null)
				]
			}
		})
		const list = await backend.listMarket()
		// Sale and rent are both fetched from the same stub, so each listing appears twice.
		const a = list.find((m) => m.id === 'a')
		const b = list.find((m) => m.id === 'b')
		expect(a).toMatchObject({
			discount_pct: 10,
			discount_chip: { tone: 'green', label: '10% under' }
		})
		expect(b).toMatchObject({
			discount_pct: -10,
			discount_chip: { tone: 'amber', label: '10% over' }
		})
		expect(list.some((m) => m.id === 'c')).toBe(false)
		expect(list[0].id).toBe('a')
	})
})

function lease(overrides: Partial<CrmLead> = {}): CrmLead {
	return {
		lead_id: 'lease-1',
		unit_candidate_key: 'unit-1',
		building_name: 'Bloom Towers',
		project_name: '',
		area_name: 'Jumeirah Village Circle',
		property_type: 'Apartment',
		property_subtype: 'Apartment',
		bedrooms: '1 Bed',
		size_sqft: 750,
		annual_rent_aed: 85_000,
		unit_number: null,
		lease_start: '2025-12-08',
		lease_end: '2026-12-07',
		days_to_expiry: 60,
		status: 'expiring_90',
		priority: 'medium',
		score: 66,
		contract_state: 'New',
		match_confidence: 'low',
		lead_reason: 'Lease expires in 60 days; begin a renewal-status conversation.',
		recommended_action: 'Resolve the exact unit, then contact the owner.',
		sale_conversation: false,
		...overrides
	} as CrmLead
}

function renewalLeads(items: CrmRenewalLeadsResponse['items']): CrmRenewalLeadsResponse {
	return {
		items,
		total: items.length,
		limit: 500,
		offset: 0,
		entered_today: 1,
		preferences: { areas: ['Jumeirah Village Circle'], forward_days: 60, lookback_days: 30 },
		as_of: '2026-10-08'
	}
}

const NO_ENQUIRIES = { enquiries: [], total: 0, summary: {} }

/** A stored Vitevue lease lead as the backend returns it once an agent has opened the unit. */
function openedLease(id: number): InboundEnquiryResponse {
	return enquiry({
		id,
		intent: 'unknown',
		record_kind: 'unqualified_contact',
		source: 'Vitevue lease lead',
		contact: { ...enquiry().contact, full_name: '', phone: null },
		criteria: {
			building: 'Bloom Towers',
			unit_number: '216',
			locations: ['Jumeirah Village Circle']
		},
		observed_data: {
			vitevue_lease: {
				lease_id: 'lease-1',
				lease_start: '2025-12-08',
				lease_end: '2026-12-07',
				annual_rent_aed: 85_000,
				vacant: false
			}
		}
	})
}

describe('Vitevue lease leads in Leads (/api/v1/crm/renewal-leads)', () => {
	it('lists unopened units as undecided Vitevue leads with a blank owner, never a guessed one', async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': NO_ENQUIRIES,
			'/api/v1/crm/renewal-leads': renewalLeads([
				{ lead: lease({ unit_number: '216' }), owner: null }
			])
		})
		const { crm } = await import('$lib/crm/api')
		const [row] = (await crm.leads.list({ vitevue: true })).items
		expect(row).toMatchObject({
			id: 0,
			lead_type: 'owner',
			source: 'vitevue',
			name: 'Bloom Towers · unit 216',
			known: 'Owner not identified yet'
		})
	})

	it('ranks vacant units first, then the leases that end soonest', async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': NO_ENQUIRIES,
			'/api/v1/crm/renewal-leads': renewalLeads([
				{ lead: lease({ lead_id: 'ends-60', days_to_expiry: 60 }), owner: null },
				{
					lead: lease({
						lead_id: 'vacant',
						days_to_expiry: -5,
						lease_evidence_state: 'unlet_signal'
					}),
					owner: null
				},
				{ lead: lease({ lead_id: 'ends-3', days_to_expiry: 3 }), owner: null },
				{
					lead: lease({
						lead_id: 'ended-unconfirmed',
						days_to_expiry: -5,
						lease_evidence_state: 'unit_unresolved'
					}),
					owner: null
				}
			])
		})
		const { crm } = await import('$lib/crm/api')
		const { items } = await crm.leads.list({ vitevue: true })
		expect(items.map((r) => r.lease?.id)).toEqual([
			'vacant',
			'ended-unconfirmed',
			'ends-3',
			'ends-60'
		])
	})

	it('shows a unit once: an opened unit is its stored lead, not a second row', async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': { enquiries: [openedLease(7)], total: 1, summary: {} },
			'/api/v1/crm/renewal-leads': renewalLeads([
				{ lead: lease({ unit_number: '216' }), owner: null, lead_record_id: 7 }
			])
		})
		const { crm } = await import('$lib/crm/api')
		const { items } = await crm.leads.list({ vitevue: true })
		expect(items).toHaveLength(1)
		expect(items[0]).toMatchObject({ id: 7, lead_type: 'owner', name: 'Bloom Towers · unit 216' })
	})

	it('keeps undecided Vitevue leads out of Home, counts and Matches', async () => {
		const get = serve({
			'/api/v1/crm/inbound/enquiries': { enquiries: [openedLease(7)], total: 1, summary: {} }
		})
		const { crm } = await import('$lib/crm/api')
		expect((await crm.leads.list({})).items).toEqual([])
		expect(get.mock.calls.some(([u]) => String(u).includes('renewal-leads'))).toBe(false)
	})

	it("still lists the agency's own leads when the lease data cannot load, and says so", async () => {
		vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
			if (url.startsWith('/api/v1/auth/me')) return ME
			if (url.startsWith('/api/v1/crm/inbound/enquiries'))
				return { enquiries: [enquiry({ id: 1 })], total: 1, summary: {} }
			throw new Error('ClickHouse timed out')
		})
		const { crm } = await import('$lib/crm/api')
		const res = await crm.leads.list({ vitevue: true })
		expect(res.items.map((r) => r.id)).toEqual([1])
		expect(res.vitevue_unavailable).toBe(true)
	})

	it('opens a unit as the shared lead for it', async () => {
		serve({})
		const post = vi.spyOn(client, 'post').mockResolvedValue(openedLease(7))
		const { crm } = await import('$lib/crm/api')
		const res = await crm.leads.openVitevue('lease-1')
		expect(post).toHaveBeenCalledWith('/api/v1/crm/renewal-leads/lease-1/lead')
		expect(res.data.id).toBe(7)
	})

	it("saves the owner the agent finds as the lead's contact name, phone and email", async () => {
		serve({ '/api/v1/crm/inbound/enquiries/7': openedLease(7) })
		const patch = vi.spyOn(client, 'patch').mockResolvedValue(openedLease(7))
		const { crm } = await import('$lib/crm/api')
		await crm.leads.patch(7, { name: 'Huda Saeed', phone: '0501234567', email: 'huda@example.com' })
		expect(patch.mock.calls[0][1]).toMatchObject({
			full_name: 'Huda Saeed',
			phone: '0501234567',
			email: 'huda@example.com'
		})
	})

	it('reports the saved areas and windows in the Leads feed strip', async () => {
		serve({ '/api/v1/crm/renewal-leads': renewalLeads([{ lead: lease(), owner: null }]) })
		const { crm } = await import('$lib/crm/api')
		expect(await crm.feed.summary()).toEqual({
			areas: ['Jumeirah Village Circle'],
			forward_days: 60,
			lookback_days: 30,
			window_total: 1,
			on_file: 0,
			entered_today: 1
		})
	})

	it('shares one lease request between the Leads list and its feed strip', async () => {
		const get = serve({
			'/api/v1/crm/inbound/enquiries': NO_ENQUIRIES,
			'/api/v1/crm/renewal-leads': renewalLeads([{ lead: lease(), owner: null }])
		})
		const { crm } = await import('$lib/crm/api')
		await Promise.all([crm.leads.list({ vitevue: true }), crm.feed.summary()])
		const calls = get.mock.calls.filter(([u]) => String(u).startsWith('/api/v1/crm/renewal-leads'))
		expect(calls).toHaveLength(1)
	})
})

describe('Inbox from /api/v1/crm/inbound/enquiries', () => {
	/** Answer each Inbox query (workspace=inbox, workspace=unqualified, status=disqualified). */
	function serveInbox(lists: {
		inbox?: InboundEnquiryResponse[]
		unqualified?: InboundEnquiryResponse[]
		disqualified?: InboundEnquiryResponse[]
	}) {
		return vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
			if (url.startsWith('/api/v1/auth/me')) return ME
			const pick = url.includes('workspace=inbox')
				? lists.inbox
				: url.includes('workspace=unqualified')
					? lists.unqualified
					: url.includes('status=disqualified')
						? lists.disqualified
						: undefined
			if (pick === undefined) throw new Error(`unexpected GET ${url}`)
			return { enquiries: pick, total: pick.length, summary: {} }
		})
	}

	it('holds enquiries waiting for a first reply and unqualified contacts, not Vitevue lease leads', async () => {
		serveInbox({
			inbox: [enquiry({ id: 1 })],
			unqualified: [
				enquiry({ id: 2, intent: 'unknown', record_kind: 'unqualified_contact' }),
				openedLease(3)
			],
			disqualified: [enquiry({ id: 4, status: 'disqualified' })]
		})
		const { crm } = await import('$lib/crm/api')
		const res = await crm.inbox.list('all')
		expect(res.items.map((x) => x.id).sort()).toEqual(['1', '2'])
		expect(res.counts.dismissed).toBe(1)
		expect((await crm.inbox.list('dismissed')).items.map((x) => x.id)).toEqual(['4'])
	})

	it('opens only a typed enquiry as a lead: an unqualified contact has no lead type yet', async () => {
		serveInbox({
			inbox: [enquiry({ id: 1 })],
			unqualified: [enquiry({ id: 2, intent: 'unknown', record_kind: 'unqualified_contact' })],
			disqualified: []
		})
		const { crm } = await import('$lib/crm/api')
		const byId = Object.fromEntries((await crm.inbox.list('all')).items.map((x) => [x.id, x]))
		expect(byId['1'].lead_id).toBe(1)
		expect(byId['2'].lead_id).toBeNull()
	})

	it('offers no Ask, Release or Dismiss: the handout system is not on the backend yet', async () => {
		serveInbox({
			inbox: [enquiry({ id: 1, assigned_user_id: null })],
			unqualified: [],
			disqualified: []
		})
		const { crm } = await import('$lib/crm/api')
		const [item] = (await crm.inbox.list('waiting')).items
		expect(item.state).toBe('waiting')
		expect(Object.values(item.can).some(Boolean)).toBe(false)
	})
})

describe('Assets › Renewals (/api/v1/crm/renewals)', () => {
	const params = {
		area: '',
		window: '' as const,
		property_type: '',
		project: '',
		building: '',
		limit: 50
	}
	const renewalsResponse = { leads: [lease()], total: 1, property_types: ['Apartment'] }

	it('opens on vacant units: the vacant window is the default the tab asks for', async () => {
		const get = serve({ '/api/v1/crm/renewals': renewalsResponse })
		const { crm } = await import('$lib/crm/api')
		const { ui } = await import('$lib/crm/stores/ui.svelte')
		await crm.renewals.list({ ...params, window: ui.renewalWindow })
		const url = String(get.mock.calls[0][0])
		expect(ui.renewalWindow).toBe('vacant')
		expect(url).toContain('window=vacant')
	})

	it("asks for every area unless one is picked: the full dataset, not the agent's areas", async () => {
		const get = serve({ '/api/v1/crm/renewals': renewalsResponse })
		const { crm } = await import('$lib/crm/api')
		const res = await crm.renewals.list(params)
		expect(String(get.mock.calls[0][0])).not.toContain('area=')
		expect(res.items[0]).toMatchObject({ id: 'lease-1', unit: 'Bloom Towers', owner: null })
		expect(res.property_types).toEqual(['Apartment'])
	})

	it('passes the abort signal so a filter change drops the previous request', async () => {
		const get = serve({ '/api/v1/crm/renewals': renewalsResponse })
		const { crm } = await import('$lib/crm/api')
		const controller = new AbortController()
		await crm.renewals.list(params, controller.signal)
		expect(get.mock.calls[0][1]).toMatchObject({ signal: controller.signal })
	})
})

describe('CRM areas with no backend yet', () => {
	it('read as empty instead of showing records nobody stored', async () => {
		const { crm } = await import('$lib/crm/api')
		const get = vi.spyOn(client, 'get')
		expect((await crm.deals.list('pipeline')).items).toEqual([])
		expect(await crm.events.list('2026-10-01', '2026-10-31')).toEqual([])
		expect((await crm.listings.list('mine')).items).toEqual([])
		expect(await crm.notifications.list()).toEqual([])
		expect(get).not.toHaveBeenCalled()
	})

	it('refuse writes with a 501 so no screen reports a save that did not happen', async () => {
		const { crm } = await import('$lib/crm/api')
		await expect(
			crm.deals.create({
				lead_id: 1,
				listing_id: null,
				value_aed: 1,
				due_date: '2026-11-01'
			} as never)
		).rejects.toMatchObject({ status: 501 })
		await expect(
			crm.events.create({ title: 'Call', date: '2026-10-08', time: null, kind: 'call' })
		).rejects.toMatchObject({ status: 501 })
	})

	it("fill Home's new leads and the sidebar count from real leads", async () => {
		serve({
			'/api/v1/crm/inbound/enquiries': {
				enquiries: [enquiry({ id: 1 }), enquiry({ id: 2, status: 'contacted' })],
				total: 2,
				summary: {}
			}
		})
		const { crm } = await import('$lib/crm/api')
		const home = await crm.home('2026-10-07')
		expect(home.new_leads.map((l) => l.id)).toEqual([1])
		expect(home.week.map((d) => d.date)).toEqual([
			'2026-10-05',
			'2026-10-06',
			'2026-10-07',
			'2026-10-08',
			'2026-10-09',
			'2026-10-10',
			'2026-10-11'
		])
		expect((await crm.counts()).new_leads).toBe(1)
	})
})
