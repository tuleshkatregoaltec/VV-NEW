/**
 * CRM API. Every screen reads and writes through these functions only, and every value shown
 * comes from the Vitevue backend (./backend): leads, owners, imports, portal listings, the
 * agent's name, and what is built from them (Search, Matches, Home's new leads, the counts).
 *
 * Lease renewals come from the lease-expiry dataset (/api/v1/crm/renewals). Leads and its feed
 * strip also hold the Vitevue lease leads for the agent's areas (/renewal-leads).
 *
 * Areas with no backend endpoint yet (deals, calendar, the inbox's handout, the agency's
 * listings, portfolio, notifications, settings) read as empty, and their writes fail with a 501
 * so no screen reports a change that was not stored.
 */
import { addDays } from '../format'
import * as backend from './backend'
import type {
	CalendarEvent,
	CalendarSide,
	CommissionAmounts,
	CommissionPatch,
	Connection,
	ConversationMessage,
	DealCreate,
	DealDetail,
	DealListResponse,
	DealStages,
	DealTab,
	DealType,
	DocumentUploadResult,
	EventCreate,
	FeedArea,
	FeedPreferences,
	FeedSummary,
	FormPreview,
	HandoutMode,
	HandoutSettings,
	HomeResponse,
	ImportPreview,
	ImportRequest,
	InboxFilter,
	InboxItem,
	InboxResponse,
	LeadCreate,
	LeadDetail,
	LeadEvent,
	LeadListParams,
	LeadListResponse,
	LeadMatches,
	LeadPatch,
	LeadStageMove,
	ListingCreate,
	ListingCreateResult,
	ListingDetail,
	ListingListResponse,
	ListingScope,
	ListingStatus,
	MarketListing,
	MarketListingDetail,
	MatchClients,
	MatchOptions,
	Me,
	NavCounts,
	Notification,
	OwnerDetail,
	OwnerDroppedRows,
	OwnerDroppedRowsParams,
	OwnerFilePreview,
	OwnerImport,
	OwnerImportDetail,
	OwnerImportResult,
	OwnerImportRows,
	OwnerImportRowsParams,
	OwnerLeadCreate,
	OwnerListParams,
	OwnerListResponse,
	OwnerProperties,
	OwnerPropertyParams,
	OwnerRecordCreate,
	OwnerReport,
	OwnerRow,
	PortfolioResponse,
	PursueResult,
	RenewalDetail,
	RenewalListParams,
	RenewalListResponse,
	SearchResults,
	Written
} from './types'

type Ok = { ok: boolean }

const notOnBackend = backend.notOnBackend

/** Conveyancing steps of a Dubai sale and a let: the process, not stored records. */
const DEAL_STAGES: DealStages = {
	sale: [
		'Offer accepted',
		'Form F signed',
		'Mortgage / bank',
		'NOC',
		'Transfer booked',
		'Transferred'
	],
	rent: ['Contract', 'Deposit and cheques', 'Ejari', 'Keys']
}

/** Monday to Sunday of the week holding `day`. */
function weekOf(day: string): string[] {
	const dow = (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7
	const monday = addDays(day, -dow)
	return Array.from({ length: 7 }, (_, i) => addDays(monday, i))
}

/** Home: new leads are real; events, deals and commission have no backend yet and read as none. */
async function home(day: string): Promise<HomeResponse> {
	const date = day || backend.localToday()
	const leads = await backend.listLeads({ stage: 'new' })
	const fresh = [...leads.items].sort((a, b) => b.score - a.score)
	return {
		date,
		facts: { events_today: 0, conveyancing: 0, permits_under_14: 0 },
		counters: {
			viewings_week: { count: 0, next: null },
			expiring_14: { count: 0, permits: 0, form_a: 0 },
			market: null,
			commission: { received_aed: 0, due_aed: 0 },
			new_leads: fresh.length,
			overdue: 0,
			conveyancing: { count: 0, value_aed: 0 }
		},
		week: weekOf(date).map((d) => ({ date: d, kinds: [] })),
		day: [],
		conveyancing: [],
		new_leads: fresh,
		gaps: [],
		file_leads: []
	}
}

async function counts(): Promise<NavCounts> {
	const leads = await backend.listLeads({})
	return {
		new_leads: leads.new_count,
		inbox: 0,
		open_deals: 0,
		events_today: 0,
		waiting: 0,
		unread_notifications: 0
	}
}

export const crm = {
	me: (): Promise<Me> => backend.me(),
	counts,
	home,
	undo: (_token: string) => notOnBackend<Ok>('Undo'),

	/** Backend: /api/v1/crm/inbound/enquiries (a lead is an enquiry with a known intent). */
	leads: {
		list: (p: LeadListParams): Promise<LeadListResponse> => backend.listLeads(p),
		get: (id: number): Promise<LeadDetail> => backend.getLead(id),
		create: (body: LeadCreate) => backend.createLead(body),
		/** Open a Vitevue lease as the agency's shared lead for its unit (created the first time). */
		openVitevue: (leaseId: string) => backend.openVitevueLead(leaseId),
		patch: (id: number, body: LeadPatch) => backend.patchLead(id, body),
		/** Move to a stage with what that stage needs (status + a note or contact activity). */
		moveStage: (id: number, body: LeadStageMove) => backend.moveLeadStage(id, body),
		uploadDocument: (_id: number, _file: File, _docType: string) =>
			notOnBackend<Written<LeadDetail>>('Lead documents'),
		events: (id: number): Promise<LeadEvent[]> => backend.leadEvents(id),
		undoLast: (_id: number, _eventId?: number) =>
			notOnBackend<{ data: LeadDetail; undone: string }>('Undo'),
		addNote: (id: number, text: string) => backend.addLeadNote(id, text),
		logCall: (id: number, outcome?: string) => backend.logLeadCall(id, outcome),
		bookViewing: (
			_id: number,
			_body: { date: string; time: string; listing_id: string | null; note: string }
		) => notOnBackend<Written<CalendarEvent>>('Booking a viewing'),
		/** The backend has no over-budget or after-move options; both are ignored. */
		matches: (id: number, _o: MatchOptions = {}): Promise<LeadMatches> => backend.leadMatches(id),
		/** Pursue a match (again to stop): "Match pursued" on both records + the next action. */
		pursue: (_id: number, _key: string) => notOnBackend<Written<PursueResult>>('Pursue'),
		shortlist: (id: number, key: string) => backend.shortlistMatch(id, key),
		renewalCall: (_id: number, _key: string) =>
			notOnBackend<Written<CalendarEvent>>('Planning a renewal call'),
		/** Ask a colleague about their listing, lead or allocation (logged; nothing goes to a client). */
		ask: (_id: number, _key: string) =>
			notOnBackend<Written<{ asked: string }>>('Asking a colleague'),
		conversation: async (_id: number): Promise<ConversationMessage[]> => [],
		createListing: (_id: number, _body: ListingCreate) =>
			notOnBackend<Written<ListingCreateResult>>('A listing from a lead')
	},

	/** Backend: /api/v1/crm/area-preferences, /renewal-areas and /renewal-leads (My areas). */
	feed: {
		summary: (): Promise<FeedSummary> => backend.feedSummary(),
		areas: (): Promise<FeedArea[]> => backend.feedAreas(),
		preferences: (): Promise<FeedPreferences> => backend.getFeedPreferences(),
		setPreferences: (p: FeedPreferences): Promise<Written<FeedPreferences>> =>
			backend.setFeedPreferences(p)
	},

	inbox: {
		/** Backend: /api/v1/crm/inbound/enquiries. Read only until the handout system exists. */
		list: (filter: InboxFilter): Promise<InboxResponse> => backend.listInbox(filter),
		ask: (_id: string) => notOnBackend<Written<InboxItem>>('Asking for an enquiry'),
		withdraw: (_id: string) => notOnBackend<Written<InboxItem>>('Withdrawing a request'),
		release: (_id: string) => notOnBackend<Written<InboxItem>>('Releasing an enquiry'),
		dismiss: (_id: string) => notOnBackend<Written<InboxItem>>('Dismissing an enquiry'),
		restore: (_id: string) => notOnBackend<Written<InboxItem>>('Restoring an enquiry')
	},

	settings: {
		handout: () => notOnBackend<HandoutSettings>('The lead handout setting'),
		setHandout: (_mode: HandoutMode) =>
			notOnBackend<Written<HandoutSettings>>('The lead handout setting')
	},

	matches: {
		clients: (): Promise<MatchClients> => backend.matchClients(),
		/** Watch a unit whose owner is not on your file (unit events). */
		watchUnit: (_key: string) => notOnBackend<Written<{ on: boolean }>>('Watching a unit')
	},

	deals: {
		stages: async (): Promise<DealStages> => DEAL_STAGES,
		list: async (_tab: DealTab, _dealType?: DealType | ''): Promise<DealListResponse> => ({
			items: [],
			counts: { open: 0, missing_documents: 0, closed: 0, sale: 0, rent: 0 },
			open_value_aed: 0,
			totals: { gross: 0, agent: 0, agency: 0 }
		}),
		get: (_id: string) => notOnBackend<DealDetail>('Deals'),
		check: (_id: string, _key: string, _done: boolean) =>
			notOnBackend<Written<DealDetail>>('The deal checklist'),
		commissionPreview: (_id: string, _body: CommissionPatch) =>
			notOnBackend<CommissionAmounts>('Commission'),
		create: (_body: DealCreate) => notOnBackend<Written<DealDetail>>('Creating a deal'),
		move: (_id: string, _direction: 'next' | 'back') =>
			notOnBackend<Written<DealDetail>>('Moving a deal'),
		patch: (_id: string, _body: { dld_transaction_id?: string }) =>
			notOnBackend<Written<DealDetail>>('Editing a deal'),
		upload: (_id: string, _file: File, _docType?: string | null) =>
			notOnBackend<Written<DocumentUploadResult>>('Deal documents'),
		removeDocument: (_id: string, _docId: number) =>
			notOnBackend<Written<{ removed: string }>>('Deal documents'),
		requestDocuments: (_id: string, _docTypes: string[]) =>
			notOnBackend<{ requested: number }>('Requesting documents'),
		commission: (_id: string, _body: CommissionPatch) =>
			notOnBackend<Written<DealDetail>>('Commission'),
		invoice: (_id: string) =>
			notOnBackend<Written<DealDetail> & { invoice: { amount_aed: number; vat_aed: number } }>(
				'Invoicing'
			),
		form: (_id: string, _code: string) => notOnBackend<FormPreview>('Deal forms'),
		compliance: (_id: string, _body: { due_diligence: 'complete' | 'pending' }) =>
			notOnBackend<Written<DealDetail>>('Compliance'),
		screening: (_id: string) => notOnBackend<{ status: string }>('Screening'),
		goaml: (_id: string) => notOnBackend<{ status: string }>('goAML')
	},

	/** The agency's own listings have no backend yet (portal listings are under `market`). */
	listings: {
		list: async (_scope: ListingScope, _status?: ListingStatus): Promise<ListingListResponse> => ({
			items: [],
			counts: { live: 0, pocket: 0, expired: 0 }
		}),
		get: (_id: string) => notOnBackend<ListingDetail>('Listings'),
		create: (_body: ListingCreate) =>
			notOnBackend<Written<ListingCreateResult>>('Creating a listing'),
		addPermit: (_id: string, _body: { number: string; expires: string }) =>
			notOnBackend<Written<ListingDetail>>('Permits'),
		publish: (_id: string) => notOnBackend<Written<ListingDetail>>('Publishing a listing'),
		unpublish: (_id: string) => notOnBackend<Written<ListingDetail>>('Unpublishing a listing'),
		relist: (_id: string) =>
			notOnBackend<Written<ListingDetail> & { message: string | null }>('Relisting'),
		upload: (_id: string, _file: File, _docType: string) =>
			notOnBackend<Written<ListingDetail>>('Listing documents'),
		send: (_id: string, _leadIds: number[]) =>
			notOnBackend<Written<{ sent: number }>>('Sending a listing'),
		sendBrochure: (_id: string) => notOnBackend<{ sent: boolean }>('Sending a brochure')
	},

	portfolio: {
		get: async (): Promise<PortfolioResponse> => ({
			clients: [],
			totals: {
				sales: 0,
				sales_value_aed: 0,
				lets: 0,
				lets_value_aed: 0,
				commission_aed: 0,
				clients: 0
			}
		}),
		checkIn: (_client: string) => notOnBackend<{ sent: boolean }>('Client check-ins')
	},

	/** Backend: /api/v1/crm/dashboard (every lease), /leads/{id} (history), /workspaces/{id} (owner). */
	renewals: {
		list: (p: RenewalListParams, signal?: AbortSignal): Promise<RenewalListResponse> =>
			backend.listRenewals(p, signal),
		get: (id: string): Promise<RenewalDetail> => backend.getRenewal(id),
		saveOwner: (id: string, owner: { name: string; phone: string; email: string }) =>
			backend.saveRenewalOwner(id, owner),
		/** Starting a renewal opens a rent deal; deals are not stored yet. */
		start: (_id: string) => notOnBackend<Written<RenewalDetail>>('Starting a renewal')
	},

	/** Backend: /api/v1/listings (Property Finder listings with a comparable-sales estimate). */
	market: {
		list: (): Promise<MarketListing[]> => backend.listMarket(),
		get: (id: string): Promise<MarketListingDetail> => backend.getMarket(id),
		watch: (_id: string, _watch: boolean) => notOnBackend<Written<MarketListing>>('The watchlist'),
		send: (_id: string, _leadIds: number[]) =>
			notOnBackend<Written<{ sent: number }>>('Sending a portal listing')
	},

	/** Backend: /api/v1/crm/owner-registry and /owner-workspaces (the agency's imported owner file). */
	owners: {
		list: (p: OwnerListParams): Promise<OwnerListResponse> => backend.listOwners(p),
		get: (id: string): Promise<OwnerDetail> => backend.getOwner(id),
		createLead: (_id: string, _body: OwnerLeadCreate) =>
			notOnBackend<Written<LeadDetail>>('A lead from an owner'),
		create: (_body: { name: string; units: string[] }) =>
			notOnBackend<Written<OwnerRow>>('Adding an owner by hand'),
		addNote: (id: string, text: string) => backend.addOwnerNote(id, text),
		/** Add record: a documented prior transaction (sale, tenancy, listing agreement). */
		addRecord: (_id: string, _body: OwnerRecordCreate) =>
			notOnBackend<Written<OwnerRow>>('Adding a prior deal record'),
		report: (_id: string) => notOnBackend<OwnerReport>('The owner report'),
		sendReport: (_id: string) => notOnBackend<Written<OwnerRow>>('Sending the owner report'),
		sendMarketUpdate: (_id: string) => notOnBackend<Written<OwnerRow>>('Sending a market update')
	},

	/** Backend: /api/v1/crm/inbound/imports (leads) and /contact-imports (owners). */
	imports: {
		list: (): Promise<ImportRequest[]> => backend.listImports(),
		/** The backend reads a leads file and picks the columns. */
		preview: (file: File): Promise<ImportPreview> => backend.previewImport(file),
		/** The import endpoints take no areas, so `areas` is not sent. */
		create: (file: File, _areas: string) => backend.createImport(file),
		/** An owners file is read in the browser first; nothing is sent until it is imported. */
		previewOwners: (file: File): Promise<OwnerFilePreview> => backend.previewOwnerFile(file),
		/** Upload, match against DLD and save an owners file (/contact-imports). */
		importOwners: (file: File, name: string): Promise<Written<OwnerImportResult>> =>
			backend.importOwnerFile(file, name),
		/** Owner files uploaded by the organization, newest first. */
		ownerHistory: (): Promise<OwnerImport[]> => backend.listOwnerImports(),
		/** Owner files with counts, sheet mappings and checks (dashboard and report). */
		ownerDetails: (): Promise<OwnerImportDetail[]> => backend.listOwnerImportDetails(),
		/** One page of an owner file's kept rows. */
		ownerRows: (listId: number, p: OwnerImportRowsParams): Promise<OwnerImportRows> =>
			backend.listOwnerImportRows(listId, p),
		/** One page of the rows an owner file did not keep, with the reason. */
		ownerDropped: (importId: number, p: OwnerDroppedRowsParams): Promise<OwnerDroppedRows> =>
			backend.listOwnerImportDropped(importId, p),
		/** One page of an owner file's distinct properties, with their rows. */
		ownerProperties: (importId: number, p: OwnerPropertyParams): Promise<OwnerProperties> =>
			backend.listOwnerImportProperties(importId, p),
		/** Rename an owner file and its saved list. */
		renameOwners: (id: number, name: string): Promise<Written<null>> =>
			backend.renameOwnerImport(id, name),
		/** Delete an owner file with its saved list and claims. No undo. */
		removeOwners: (id: number): Promise<Written<null>> => backend.deleteOwnerImport(id)
	},

	events: {
		list: async (_from: string, _to: string): Promise<CalendarEvent[]> => [],
		get: (_id: number) => notOnBackend<CalendarEvent>('Calendar events'),
		create: (_body: EventCreate) => notOnBackend<Written<CalendarEvent>>('Calendar events'),
		done: (_id: number) => notOnBackend<Written<CalendarEvent>>('Calendar events'),
		/** Reschedule: a lead's event is logged on the lead and becomes its next action. */
		reschedule: (_id: number, _date: string, _time: string | null) =>
			notOnBackend<Written<CalendarEvent>>('Calendar events'),
		remove: (_id: number) => notOnBackend<Written<{ deleted: string }>>('Calendar events'),
		side: async (): Promise<CalendarSide> => ({ overdue: [], deadlines: [] })
	},

	search: (q: string): Promise<SearchResults> => backend.search(q),

	notifications: {
		list: async (): Promise<Notification[]> => [],
		read: async (_ids?: number[]): Promise<Written<{ unread: number }>> => ({
			data: { unread: 0 },
			undo_token: null
		})
	},

	connections: async (): Promise<Connection[]> => []
}
