/**
 * CRM Beta API shapes. Field names follow docs/crm/data-model.md and reference/fixtures.json;
 * anything computed (chips, summaries, commission amounts, counts) is produced by the backend
 * so no business rule lives in Svelte. Money is whole AED; dates are YYYY-MM-DD (Dubai).
 */
import type { ClaimStatus, DropReason, ImportCounts, SheetCheck } from '../ownerImportReport'

/** `owner`: a Vitevue lease lead not yet classified as seller or landlord. */
export type LeadType = 'buyer' | 'seller' | 'tenant' | 'landlord' | 'owner'
export type DealType = 'sale' | 'rent'
/** `file`: an owner on the agency's own owner file ("Your owner file", 7 Oct; was `vitevue`). */
export type Source = 'inbound' | 'file' | 'own_contact' | 'agent_to_agent' | 'vitevue'
/**
 * Stages depend on the lead type (6 Oct): buyers and tenants go through Viewing, sellers and
 * landlords through Listed (proposal). New → Contacted → Qualified → Viewing | Listed → Offer → Won / Lost.
 */
export type LeadStage =
	| 'new'
	| 'contacted'
	| 'qualified'
	| 'viewing'
	| 'listed'
	| 'offer'
	| 'won'
	| 'lost'
export type ListingStatus = 'live' | 'pocket' | 'expired'
export type ListingScope = 'mine' | 'company'
/** Own contact · Agency file · DLD/Ejari event (7 Oct; Vitevue never supplies an owner). */
export type OwnerSource = 'agent_contact' | 'agency_import' | 'dld_event'
export type EventKind = 'viewing' | 'call' | 'follow_up' | 'deal_date' | 'deadline'
export type Origin = 'human' | 'rule' | 'model' | 'voice'
export type InvoiceStatus = 'not_invoiced' | 'due' | 'received' | 'overdue'
export type Approval = 'pending' | 'approved'
export type Tone = 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'pink' | 'dark' | 'outline'

/** A status chip decided by the backend: colour and words always travel together. */
export interface Chip {
	tone: Tone
	label: string
}

export interface Paged<T> {
	items: T[]
	total: number
	page: number
	page_size: number
}

/** Every write returns the record plus a token the toast's Undo sends back. */
export interface Written<T> {
	data: T
	undo_token: string | null
	event_id?: number | null
}

export interface Me {
	name: string
	initials: string
	role: 'agent'
	today: string
	areas: string[]
}

export interface NavCounts {
	new_leads: number
	/** Inbox items assigned to me and not yet a lead, or waiting with my request on them. */
	inbox: number
	open_deals: number
	events_today: number
	/** Inbox items waiting to be assigned (Manager). */
	waiting: number
	unread_notifications: number
}

/* ── leads ── */

export type LeadFieldKey =
	| 'wants'
	| 'budget_aed'
	| 'finance'
	| 'purpose'
	| 'timeline'
	| 'unit'
	/** A seller's or landlord's property, in the two keys the backend matches on. */
	| 'building'
	| 'unit_number'
	| 'target_price_aed'
	| 'occupancy'
	| 'mortgage'
	| 'cheques'
	| 'move_in'
	| 'expected_rent_aed'
	| 'available'
	| 'furnishing'
	/** Off-plan buyers (7 Oct, planned): areas, amount they can pay on booking, handover by. */
	| 'areas'
	| 'on_booking_aed'
	| 'handover_by'

export type LeadFields = Partial<Record<LeadFieldKey, string | number | null>>

export interface LeadRow {
	id: number
	name: string
	phone: string
	lead_type: LeadType
	deal_type: DealType
	source: Source
	source_detail: string
	received_label: string
	stage: LeadStage
	agent: string | null
	score: number
	area: string
	language: string
	next_action: string
	/** Buyer looking at off-plan projects (Matches › Off-plan buyers). */
	off_plan: boolean
	/**
	 * DLD Circular 02-2026: a lead from the owner file whose owner has no prior deal on record.
	 * No call, viewing, listing or stage move (except Lost) until a record is added.
	 */
	gated: boolean
	/** The owner record behind an owner-file lead (for Add record). */
	owner_id: string | null
	fields: LeadFields
	/** "Details" column: first line + sub line, per the lead type (business-rules.md). */
	summary: { line: string; sub: string }
	/** "What we know": the owner-match summary. */
	known: string
	/** Where it came from in a word or two ("Property Finder", "Lease ends in 41 days"). */
	source_short: string
	/** Buyer/tenant from a portal: the listing they enquired on. */
	listing: RecordRef | null
	/** Vitevue lease lead: the unit's lease behind it. */
	lease: VitevueLease | null
}

/**
 * The lease behind a Vitevue lease lead. A lead nobody has opened yet has id 0 and is created
 * (one per unit, shared by the agency) when an agent first opens it.
 */
export interface VitevueLease {
	/** Lease id: opens the lease with its property history. */
	id: string
	unit: string
	area: string
	property: string
	lease_start: string
	lease_end: string
	/** Negative once the lease has ended. */
	days_left: number
	vacant: boolean
	rent_aed: number
	last_purchase: { date: string; price_aed: number | null } | null
	end_chip: Chip
}

export interface OwnerMatchRecord {
	unit: string
	area: string
	status: string
	purchase: string
	owner_id: string | null
	owner_name: string | null
}

export interface RecordRef {
	id: string
	label: string
}

export interface StageDocument {
	type: string
	note: string | null
	file: { name: string; size_bytes: number } | null
}

export interface PursuedMatch {
	key: string
	title: string
	next: string
	at: string
}

export interface LeadDetail extends LeadRow {
	/** The contact's email ('' when unknown). */
	email: string
	/** "On your agency's file": what the phone matches on the agency's owner file. */
	owner_match: { summary: string; records: OwnerMatchRecord[] }
	/** Matches the agent pursued for this lead (Pursue in Matches). */
	pursued: PursuedMatch[]
	suggested_agent: { name: string; reason: string } | null
	/** The enquired-on listing in full (price, status, whose) for the "Enquired on" row. */
	listing_card: ListingCard | null
	/** Seller/landlord: the listing created from this lead. */
	own_listing: (RecordRef & { status: ListingStatus }) | null
	/** Deal made from this lead, or (seller/landlord) the deal on their listing. */
	deal: (RecordRef & { stage: string }) | null
	/** Documents each stage asks for (optional at the move). Stages that ask for none are left out. */
	stage_documents: { stage: LeadStage; docs: StageDocument[] }[]
	can_undo: boolean
}

export type BudgetBand = 's1' | 's2' | 's3' | 'r1' | 'r2'
export type PropertyKind = 'apartment' | 'villa'
/** Owners type filter: the kinds of property in owner files and DLD; "unspecified" = "Unit" / "Residential". */
export type OwnerPropertyKind =
	| 'apartment'
	| 'villa'
	| 'land'
	| 'hotel'
	| 'commercial'
	| 'building'
	| 'unspecified'

export interface LeadListParams {
	lead_type?: LeadType | ''
	/** Lost tab: only lost leads. Every other tab leaves lost leads out. */
	lost?: boolean
	source?: Source | ''
	stage?: LeadStage | ''
	area?: string
	q?: string
	beds?: string
	ptype?: PropertyKind | ''
	budget?: BudgetBand | ''
	/** Leads page: add Vitevue lease leads from My areas (not Home, counts or Matches). */
	vitevue?: boolean
}

export interface LeadListResponse {
	items: LeadRow[]
	total: number
	/** All and the type counts leave lost leads out; `lost` counts them. */
	counts: { all: number; lost: number } & Record<LeadType, number>
	new_count: number
	areas: string[]
	/** The Vitevue lease leads could not load; the agency's own leads are still listed. */
	vitevue_unavailable?: boolean
}

export interface LeadEvent {
	id: number
	at: string
	text: string
	origin: Origin
	revertible: boolean
}

export interface LeadCreate {
	name: string
	phone: string
	source: Source
	source_detail: string
	lead_type: LeadType
	area: string
	fields: LeadFields
}

export interface LeadPatch {
	stage?: LeadStage
	lead_type?: LeadType
	fields?: LeadFields
	next_action?: string
	language?: string
	/** Normalised to +971; a changed number re-runs the owner match. */
	phone?: string
	/** The contact's name and email, e.g. the owner found for a Vitevue lease lead. */
	name?: string
	email?: string
	/** Buyers only: matched to Launch desk projects (Matches › Off-plan buyers). */
	off_plan?: boolean
}

/** A stage move with what that stage needs (business-rules.md, Lead stages). */
export interface LeadStageMove {
	stage: LeadStage
	note?: string
	/** Contacted: how it went. */
	outcome?: string
	next_action?: string
	/** Qualified: the type's fields (the money field is required). */
	fields?: LeadFields
	/** Viewing: adds a calendar event. */
	viewing?: { date: string; time: string; listing_id: string | null }
	/** Offer: buyer/tenant amount + unit; seller/landlord amount received + from whom. */
	offer?: { amount_aed: number; listing_id?: string | null; from?: string }
	/** Lost: the reason. */
	lost_reason?: string
	/** Listed (seller/landlord): the pocket listing to create and link, unless one is linked. */
	listing?: ListingCreate
}

/** A colleague's lead in Matches: no name, phone or details, and it does not open. */
export interface AnonLead {
	lead_type: LeadType
	agent: string
	area: string
	price_label: string
	stage: LeadStage
}

export interface MatchListing {
	listing: ListingCard
	over_budget: Chip | null
}

export interface MatchPublic {
	listing: MarketListing
	over_budget: Chip | null
}

/* ── Matches (top-level section, 6 Oct) ── */

export type MatchTab = 'ready' | 'offplan' | 'sell'

/** One client in the Matches list (left column). */
export interface MatchClient {
	lead: LeadRow
	/** "3 bed villa, Dubai Hills · Sale · 2.8m" */
	line: string
	pursued: number
}

export interface MatchClients {
	ready: MatchClient[]
	offplan: MatchClient[]
	sell: MatchClient[]
}

/** What a match row is; it decides the buttons. */
export type MatchKind =
	| 'listing'
	| 'colleague_listing'
	| 'seller_lead'
	| 'owner'
	| 'public'
	| 'unit_event'
	| 'project'
	| 'allocation'
	| 'lead'
	| 'colleague_lead'
	| 'inbox'

export interface MatchRow {
	/** Stable key for Pursue, Shortlist and Watch. */
	key: string
	kind: MatchKind
	title: string
	sub: string
	/** Source line colour (company = own, owner file = vv, public = in). */
	line: 'own' | 'vv' | 'in' | 'a2a'
	over_budget: Chip | null
	/** Status chip (Form I first, Prior deal on file, Not a fit, Colleague…). */
	chip: Chip | null
	/** Projects that don't fit: why. */
	why: string[]
	/** Record behind the row (opens on click of its button). */
	ref: { kind: RefKind | 'inbox' | 'market'; id: string } | null
	/** Colleague's listing: "2 leads on it · Qualified, Viewing". */
	lead_stages: string | null
	/** Colleague whose listing/lead/allocation it is (Ask {colleague}). */
	colleague: string | null
	/** Tier 2: the owner on the file and whether a prior deal is on record. */
	owner: { id: string; name: string; prior_deal: boolean } | null
	/** Pursue shows (not for unit events, not-a-fit projects, or owners without a record). */
	pursuable: boolean
	pursued: PursuedMatch | null
	shortlisted: boolean
	watching: boolean
	renewal_planned: boolean
	/** After the client's move date (shown only with "Show N after"). */
	after_move: boolean
	/** Booking a viewing makes sense once pursued (listings, owners, public). */
	can_book: boolean
}

export interface MatchTier {
	id: string
	/** 1, 2, 3 — null for Unit events. */
	n: number | null
	title: string
	sub: string
	chip: Chip | null
	/** The owner-file gate on a seller/landlord with no prior deal (no Pursue here). */
	gate: { owner_id: string } | null
	groups: { label: string | null; rows: MatchRow[]; empty: string }[]
	/** Rows hidden because they come after the move date (tier 2 and unit events). */
	hidden_after: number
}

export interface LeadMatches {
	tab: MatchTab
	lead: LeadRow
	/** Brief: Wants · Budget · Buying to / Move-in · Move by (or the unit for a seller). */
	brief: { k: string; v: string }[]
	/** Ready: tier 2 and unit events show what frees up before this date. */
	move_by: string | null
	shortlist: number
	tiers: MatchTier[]
	/** Off-plan and seller-side matching are planned (status chip). */
	status: Chip | null
	own_listing: RecordRef | null
}

export interface MatchOptions {
	/** Up to 10% over budget (proposal). */
	over?: boolean
	/** Show the units that free up after the move date. */
	after?: boolean
}

/** Pursue (6 Oct, 10:39): "Match pursued" on both linked records + the lead's next action. */
export interface PursueResult {
	lead: LeadDetail
	pursued: PursuedMatch | null
}

export interface ConversationMessage {
	from: 'client' | 'agent' | 'system'
	who: string
	at: string
	text: string
}

/* ── deals ── */

export interface DealDocument {
	id: number
	type: string
	file: string
	size_bytes: number
	uploaded: string
}

export interface CommissionAmounts {
	gross: number
	cobroker: number
	referral: number
	net: number
	agent: number
	agency: number
	vat: number
}

export interface Commission {
	rate_pct: number
	cobroker_pct: number
	referral_pct: number
	agent_pct: number
	invoice_status: InvoiceStatus
	cobroker_name: string | null
	referral_name: string | null
	amounts: CommissionAmounts
}

export interface CommissionPatch {
	rate_pct?: number
	cobroker_pct?: number
	referral_pct?: number
	agent_pct?: number
	invoice_status?: InvoiceStatus
}

export interface Compliance {
	due_diligence: 'pending' | 'complete'
	screening: 'not_run' | 'running' | 'clear' | 'review'
	cash_aed: number
	goaml_required: boolean
}

export interface DealRow {
	id: string
	deal_type: DealType
	unit: string
	client: string
	counterparty: string
	value_aed: number
	agent: string
	lead_id: number | null
	listing_id: string | null
	due_date: string
	next_step: string
	approval: Approval
	dld_transaction_id: string | null
	stages: string[]
	stage_index: number
	stage: string
	next_stage: string | null
	closed: boolean
	docs_received: number
	docs_required: number
	missing: string[]
	due_chip: Chip
	commission: Commission
}

export interface DealForm {
	code: string
	title: string
	note: string
}

export interface ChecklistItem {
	key: string
	label: string
	/** Set when the item is a document slot (Upload / On file). */
	doc: string | null
	done: boolean
	file: { name: string; uploaded: string } | null
}

/** One stage of the conveyancing page: who is involved, the checklist, that stage's documents. */
export interface ChecklistStage {
	index: number
	name: string
	who: string
	state: 'done' | 'now' | 'next'
	chip: Chip
	items: ChecklistItem[]
}

export interface DealDetail extends DealRow {
	documents: { required: string[]; received: DealDocument[] }
	compliance: Compliance
	listing: RecordRef | null
	lead: RecordRef | null
	forms: DealForm[]
	checklist: ChecklistStage[]
}

export type DealTab = 'pipeline' | 'conveyancing' | 'missing_documents' | 'commission' | 'closed'

export interface DealListResponse {
	items: DealRow[]
	counts: { open: number; missing_documents: number; closed: number; sale: number; rent: number }
	open_value_aed: number
	totals: { gross: number; agent: number; agency: number }
}

/** Stage names per deal type. Agencies may rename them (open), so they come from the API. */
export type DealStages = Record<DealType, string[]>

export interface DealCreate {
	lead_id: number
	listing_id?: string | null
	value_aed?: number | null
	due_date?: string | null
}

export interface DocumentUploadResult {
	matched: string | null
	candidates: string[]
	file_name: string
	size_bytes: number
}

export interface FormPreview {
	code: string
	title: string
	deal_id: string
	fields: { label: string; value: string | null }[]
}

/* ── listings ── */

export interface ListingDocument {
	id: number
	type: string
	file: string
	size_bytes: number
	uploaded: string
}

export interface ListingCard {
	id: string
	unit: string
	area: string
	bedrooms: number
	size_sqft: number
	price_aed: number
	deal_type: DealType
	status: ListingStatus
	scope: ListingScope
	agent: string
	owner: string
	portals: string
	days_live: number
	enquiries: number
	permit: { number: string; expires: string } | null
	permit_chip: Chip
	form_a_expires: string | null
	form_a_chip: Chip | null
	comparables_note: string
	from_lead_id: number | null
}

export interface ListingDetail extends ListingCard {
	documents: { required: string[]; notes: Record<string, string>; received: ListingDocument[] }
	form_a_status: Chip
	/** 'expired' or 'none' → the permit form shows ("Renew permit" / "Add permit"). */
	permit_status: 'none' | 'valid' | 'expired'
	/** My listing: the open leads on it. A colleague's listing: empty (see lead_stages). */
	leads: LeadRow[]
	/** A colleague's listing: how many leads and the stage of each; no names, can't open. */
	lead_stages: { stage: LeadStage; count: number }[] | null
	deals: { id: string; client: string; stage: string }[]
	/** My buyers and tenants who fit. */
	matched: LeadRow[]
	from_lead: { id: number; name: string; lead_type: LeadType } | null
	can_publish: boolean
}

export interface ListingListResponse {
	items: ListingCard[]
	counts: { live: number; pocket: number; expired: number }
}

export interface ListingCreate {
	unit: string
	area: string
	deal_type: DealType
	price_aed: number
	bedrooms: number
	size_sqft: number
	status: 'live' | 'pocket'
	permit_number: string
	owner: string
}

export interface ListingCreateResult {
	listing: ListingCard
	/** Set when the requested status could not be kept (live without a permit → pocket). */
	warning: string | null
}

export interface PortfolioResponse {
	clients: {
		client: string
		transactions: {
			what: string
			kind: DealType
			unit: string
			value_aed: number
			date: string
			dld_or_ejari: string
			commission_aed: number
		}[]
	}[]
	totals: {
		sales: number
		sales_value_aed: number
		lets: number
		lets_value_aed: number
		commission_aed: number
		clients: number
	}
}

/**
 * One lease from the lease-expiry dataset (/api/v1/crm/dashboard), keyed by its lease id.
 * Owner contact is only what the agent typed in or an owner import matched; otherwise null.
 */
export interface Renewal {
	id: string
	/** "Tower · unit 1204" (unit only when the lease is tied to one exact unit). */
	unit: string
	area: string
	/** "Apartment · 1 Bed · 850 sqft" */
	property: string
	rent_aed: number
	lease_start: string
	lease_end: string
	/** Days to lease end; negative once it has ended. */
	days_left: number
	end_chip: Chip
	owner: RenewalOwner | null
}

export interface RenewalOwner {
	name: string | null
	phone: string | null
	email: string | null
	/** "Typed in" or "Owner import · {list}" */
	source: string
}

/**
 * Assets › Renewals filter: every lease, or one lease-end window. `vacant` (the default) is a
 * unit whose lease ended in the last 60 days with no later lease registered.
 */
export type RenewalWindow =
	| ''
	| 'vacant'
	| 'expired'
	| 'expiring_30'
	| 'expiring_60'
	| 'expiring_90'

export interface RenewalListParams {
	area: string
	window: RenewalWindow
	property_type: string
	/** Picked after an area. */
	project: string
	/** Picked after a project. */
	building: string
	limit: number
}

export interface RenewalListResponse {
	items: Renewal[]
	total: number
	/** Options for the filters, from the leases in the current filter (areas come from My areas' list). */
	property_types: string[]
	projects: string[]
	buildings: string[]
}

/** A dated entry in the property history: a registered lease or a sale. */
export interface RenewalHistoryEvent {
	id: string
	kind: 'lease' | 'sale'
	date: string
	end_date: string | null
	title: string
	detail: string
	amount_aed: number | null
	source: string
}

export interface RenewalDetail extends Renewal {
	reason: string
	action: string
	last_purchase: { date: string; price_aed: number | null } | null
	gross_yield_pct: number | null
	history: RenewalHistoryEvent[]
	/** How the history was matched to the unit. */
	match_explanation: string
}

export interface MarketListing {
	id: string
	unit: string
	area: string
	deal_type: DealType
	price_aed: number
	value_from_comparables_aed: number
	discount_pct: number
	discount_chip: Chip
	portal: string
	days_listed: number
	price_cuts: number
	watch: boolean
}

export interface MarketListingDetail extends MarketListing {
	matched: LeadRow[]
}

/* ── owners ── */

export type OwnerTab = 'mine' | 'all' | 'multi' | 'event' | 'sold' | 'update'

export interface OwnerListParams {
	tab: OwnerTab
	/** Owner name, unit number or building ("Oxford Residence"). */
	q?: string
	source?: OwnerSource | ''
	area?: string
	beds?: string
	ptype?: OwnerPropertyKind | ''
	/** 1, 2 or 3 (= 3 or more) */
	units?: string
	/** Where the page starts in the whole owner file; 0 when left out. */
	offset?: number
	/** Owners per page (50, 100, 200 or 500); 500 when left out. */
	limit?: number
}

export interface OwnerRow {
	id: string
	name: string
	sources: OwnerSource[]
	source_label: string
	/** Unit numbers, not sentences: "Marina Gate 2 #2304". */
	units: string[]
	next_event: string
	next_event_date: string
	/** "in 41 days · 9 Nov" */
	next_event_label: string
	last_contact: string
	tag: string
	market_update_due: boolean
	mine: boolean
	/** On the agency's own file (agency import), not only the agent's own contact. */
	on_file: boolean
	/** A deal, a listing with a signed Form A, or a past transaction with this owner is on file. */
	prior_deal: boolean
	/** The open lead for this owner. `id` is null when it is a colleague's (it does not open). */
	lead: { id: number | null; stage: LeadStage; agent: string } | null
}

export interface OwnerNote {
	id: number
	at: string
	text: string
	origin: Origin
}

/** Every unit an owner holds, the same fields for every owner. */
export interface OwnerUnit {
	key: string
	number: string
	building: string
	area: string
	type: string
	bedrooms: number | null
	size_sqft: number | null
	bought_aed: number | null
	bought_year: number | null
	/** Date of this owner's own deal on the unit (agency file, else the matched DLD sale). */
	owned_from: string | null
	/** Date the unit was sold on to someone else, if it was. */
	owned_to: string | null
	status: string
	status_chip: Chip
	rent: { aed: number; from: string; to: string } | null
	market: { sale: [number, number]; rent: [number, number] } | null
	rent_history: { year: number; aed: number }[]
	transactions: { when: string; what: string; aed: number | null }[]
	/** Where this unit came from: "Business Bay 2024 SVR · Sheet1 row 12". Empty when unknown. */
	source: string
	/** The owner file's record next to DLD's, and the match result. Owner-import units only. */
	check?: OwnerUnitCheck
}

/** What the owner file said about a unit, what DLD has for it, and how they were matched. */
export interface OwnerUnitCheck {
	/** `role` is the file's Buyer/Seller column; empty when the file had none. */
	imported: { date: string | null; aed: number | null; type: string; unit: string; role: string }
	/** `unit`, `building` and `area` are DLD's own names for the sale's property. */
	dld: {
		unit: string
		building: string
		area: string
		date: string | null
		aed: number | null
		type: string
		bedrooms: string
		size_sqft: number | null
		built_up_sqft: number | null
		aed_per_sqft: number | null
		market: string
	}
	result: { label: string; confidence: number; reason: string; chip: Chip }
	lease: { end: string; aed: number | null } | null
}

/** One line of the owner's record of relationship. Leads and call logs do not count as a transaction. */
export interface RelationRow {
	kind: 'Deal' | 'Listing' | 'Lead' | 'Sale' | 'Tenancy' | 'Listing agreement'
	/** Counts as a documented prior transaction (DLD Circular 02-2026). */
	transaction: boolean
	text: string
	date: string | null
	ref: { kind: RefKind; id: string } | null
	/** Added by hand with Add record (can be undone from the toast). */
	added: boolean
}

/** One uploaded file this owner came from, with how many of their units it gave. */
export interface OwnerImportSource {
	/** Null when the import is no longer in the import history. */
	importId: number | null
	name: string
	file: string | null
	/** Upload date, YYYY-MM-DD. */
	date: string | null
	units: number
}

export interface OwnerDetail extends OwnerRow {
	unit_rows: OwnerUnit[]
	/** The uploaded files this owner came from, oldest first. */
	imported_from: OwnerImportSource[]
	matched: LeadRow[]
	notes: OwnerNote[]
	/** Deals, listings and past transactions with this owner on the agency's file. */
	relationship: RelationRow[]
	/**
	 * Set when the owner has an actionable event and no open lead yet ("Create lead"). Null when
	 * the owner has no prior deal on record: the panel shows Add record instead.
	 */
	create_lead: { lead_type: LeadType; next_action: Record<LeadType, string> } | null
}

/** Add record: a documented prior transaction with an owner (7 Oct). */
export interface OwnerRecordCreate {
	kind: 'sale' | 'tenancy' | 'listing_agreement'
	/** YYYY-MM-DD, not after today. */
	date: string
	unit: string
	/** DLD or Ejari number, or the Form A number. Required. */
	reference: string
}

export interface OwnerListResponse {
	items: OwnerRow[]
	counts: Record<OwnerTab, number>
	total: number
	unit_total: number
	with_event_90: number
	/** Owners on the agency's own file. */
	on_file: number
	areas: string[]
	/**
	 * This page within the whole file (search included). The tab counts and the source, area,
	 * bedrooms and type filters only see the owners on this page.
	 */
	page: { offset: number; limit: number; total: number; loaded: number }
	/** Tabs whose counts cover only this page (the backend can't filter them yet). */
	pageOnlyTabs: OwnerTab[]
}

export interface OwnerLeadCreate {
	lead_type: LeadType
	unit_key: string
	next_action: string
}

export interface OwnerReport {
	owner: string
	period: string
	rows: { unit: string; line: string }[]
	next_event: string
}

export interface ImportRequest {
	id: string
	who: string
	kind: 'Owners' | 'Leads'
	file: string
	rows: number
	areas: string
	at: string
	status: string
	tone: Tone
}

/** The backend reads the CSV and picks the columns; the screen only shows the result. */
export interface ImportPreview {
	file: string
	rows: number
	/** The header used for each column, or null when none was found. */
	columns: Record<'name' | 'phone' | 'budget' | 'area', string | null>
	sample: { name: string; phone: string; budget: string; area: string }[]
	problem: string | null
}

/**
 * An owners file read in the browser before upload: the header row it found, the data rows
 * and a few sample rows. The backend maps the columns itself when the file is imported.
 */
export interface OwnerFilePreview {
	file: string
	size: number
	sheets: number
	rows: number
	headers: string[]
	sample: string[][]
	problem: string | null
}

/** One uploaded owners file in the import history (/contact-imports). */
export interface OwnerImport {
	id: number
	name: string
	file: string
	status: 'processing' | 'completed' | 'failed'
	/** Owner–property claims kept after duplicates were merged. */
	claims: number
	rentalLeads: number
	at: string
}

/** One sheet of an owner file: how its columns were mapped and what that means for its rows. */
export interface ImportSheet {
	name: string
	rows: number
	/** 'transaction_party' (a DLD export with dates) or 'contact_list'. */
	family: string
	source: 'heuristic' | 'llm' | 'heuristic_fallback'
	confidence: number
	fields: { field: string; column: string }[]
	warnings: string[]
	checks: SheetCheck[]
}

/** An owner file with its counts and sheets, for the import dashboard and report. */
export interface OwnerImportDetail {
	id: number
	name: string
	file: string
	status: 'processing' | 'completed' | 'failed'
	at: string
	createdAt: string
	/** The saved list holding its owner rows; null when none was saved. */
	listId: number | null
	counts: ImportCounts
	sheets: ImportSheet[]
	flagged: boolean
}

export interface OwnerImportRowsParams {
	status: ClaimStatus | null
	search: string
	offset: number
}

/** One kept row of an owner file (a claim), as checked against DLD. */
export interface OwnerImportRow {
	id: number
	/** Opens this owner in the Owner panel. */
	ownerKey: string
	name: string
	phone: string
	building: string
	area: string
	project: string
	propertyType: string
	propertyDetails: Record<string, string>
	latestSaleDate: string | null
	latestSalePrice: number | null
	unit: string
	date: string | null
	price: number | null
	status: ClaimStatus
	confidence: number
	reason: string
	sheet: string
	rowNumber: number
}

export interface OwnerPropertyParams {
	search: string
	offset: number
}

/** One kept row of a property: who, their role, the sale and its DLD status. */
export interface OwnerPropertyRecord {
	name: string
	procedure: string
	identifiers: Record<string, string>
	propertyDetails: Record<string, string>
	matchReason: string
	role: string
	date: string | null
	price: number | null
	status: ClaimStatus
	sheet: string
	rowNumber: number
}

/** One distinct property (building + unit) of an owner file, with its kept rows. */
export interface OwnerProperty {
	key: string
	area: string
	project: string
	building: string
	unit: string
	type: string
	rows: number
	buyers: number
	sellers: number
	/** Name on the strongest verified/probable owner row; null when there is none. */
	currentOwner: string | null
	latestDate: string | null
	latestPrice: number | null
	records: OwnerPropertyRecord[]
}

export interface OwnerProperties {
	rows: OwnerProperty[]
	total: number
	offset: number
	limit: number
}

export interface OwnerDroppedRowsParams {
	reason: DropReason | null
	search: string
	offset: number
}

/** A row an owner file did not keep: the file's own values and why it was dropped. */
export interface OwnerDroppedRow {
	sheet: string
	rowNumber: number
	reason: DropReason
	name: string
	building: string
	unit: string
	date: string
	price: string
	role: string
	procedure: string
}

export interface OwnerDroppedRows {
	/** False for imports made before the dropped-rows log existed. */
	available: boolean
	rows: OwnerDroppedRow[]
	byReason: Partial<Record<DropReason, number>>
	total: number
	offset: number
	limit: number
}

export interface OwnerImportRows {
	rows: OwnerImportRow[]
	total: number
	offset: number
	limit: number
}

/** What the backend did with an owners file: its counts, or the earlier import of the same file. */
export interface OwnerImportResult {
	id: number
	name: string
	status: 'processing' | 'completed' | 'failed'
	/** The same file was uploaded before; the backend returned that import. */
	alreadyImported: boolean
	createdAt: string
	summary: {
		totalRows: number
		candidateRows: number
		/** Distinct units, villas or plots; null for imports made before it was counted. */
		distinctProperties: number | null
		duplicatesCollapsed: number
		validContacts: number
		verified: number
		probable: number
		unitLinked: number
		former: number
		conflicting: number
		notMatched: number
		rentalOpportunities: number
		contactReady: number
	}
}

/* ── calendar ── */

export interface CalendarEvent {
	id: number
	date: string
	time: string | null
	kind: EventKind
	title: string
	detail: string
	agent: string
	lead_id: number | null
	deal_id: string | null
	listing_id: string | null
	owner_id: string | null
	/** Lead events: "Buyer · Property Finder". */
	context: string | null
	/** The record it belongs to, for "Open record" in the event detail. */
	record: { kind: RefKind; id: string; label: string } | null
	/** Viewing outcome; "No outcome logged" for a past viewing without one. */
	outcome: string | null
}

export interface EventCreate {
	title: string
	date: string
	time: string | null
	kind: EventKind
}

export interface OverdueItem {
	text: string
	detail: string
	lead_id: number
}

export interface CalendarSide {
	overdue: OverdueItem[]
	deadlines: { event: CalendarEvent; chip: Chip }[]
}

/* ── home ── */

export interface Gap {
	text: string
	detail: string
	ref_type: 'lead' | 'deal'
	ref_id: string
}

export interface HomeResponse {
	date: string
	facts: { events_today: number; conveyancing: number; permits_under_14: number }
	/** 1 Oct (proposal): Viewings this week · Expiring in 14 days · Market · {area} · Commission. */
	counters: {
		viewings_week: { count: number; next: string | null }
		expiring_14: { count: number; permits: number; form_a: number }
		market: { area: string; psf_aed: number; rent_median_aed: number; yield_pct: number } | null
		commission: { received_aed: number; due_aed: number }
		new_leads: number
		overdue: number
		conveyancing: { count: number; value_aed: number }
	}
	week: { date: string; kinds: EventKind[] }[]
	day: CalendarEvent[]
	conveyancing: DealRow[]
	new_leads: LeadRow[]
	gaps: Gap[]
	/** To do › Owner file: owner-file leads at New or Contacted. */
	file_leads: LeadRow[]
}

/* ── feed ── */

/**
 * Leads feed strip (7 Oct): "Lease ends in 90 days · {areas} · N units · N on your agency's file ·
 * N entered the inbox today". Only owners on the agency's own file enter the inbox.
 */
export interface FeedSummary {
	areas: string[]
	/** Lease-end window: leases ending in the next `forward_days` or ended in the last `lookback_days`. */
	forward_days: number
	lookback_days: number
	/** Units in the areas inside the window (DLD/Ejari). */
	window_total: number
	/** Of the loaded units, those with a known owner (typed in or matched by an owner import). */
	on_file: number
	/** Units whose lease end entered the forward window today. */
	entered_today: number
}

export interface FeedArea {
	area: string
	/** Units with a lease ending in the next 90 days (DLD/Ejari), for My areas. */
	owners_in_90_day_window: number
}

export interface FeedPreferences {
	areas: string[]
	forward_days: number
	lookback_days: number
}

/* ── inbox and the handout setting (6 Oct, 04:25–06:10) ── */

/** Manager assigns · First to ask gets it · Agents ask, manager confirms. Default not decided. */
export type HandoutMode = 'manager' | 'request' | 'both'
export type InboxFilter = 'all' | 'mine' | 'waiting' | 'dismissed'

export interface InboxItem {
	id: string
	name: string
	/** Bayut, Instagram, Email, Your owner file… */
	source: string
	/** Source colour line. */
	line: 'in' | 'vv' | 'own' | 'a2a'
	/** "Today · 10:02" */
	at_label: string
	/** "13 min ago" */
	ago: string
	text: string
	/** "Phone matches an owner on your agency's file" or "Not on your agency's file". */
	owner_check: string
	priority: string
	/** The chip says it: Waiting for a manager · You asked · Assigned · Yours, asked first · Not a real enquiry */
	state: 'waiting' | 'requested' | 'assigned' | 'dismissed'
	status_chip: Chip
	agent: string
	suggested: string
	reason: string
	/** Who asked for it, and when. */
	requests: { agent: string; at_label: string }[]
	/** "Tenant · Dubai Marina · studio · no budget" */
	want_line: string
	lead_id: number | null
	conversation: ConversationMessage[]
	/** What I can do here, decided by the backend from the handout mode. */
	can: { ask: boolean; withdraw: boolean; release: boolean; dismiss: boolean; restore: boolean }
	/** "Asked 10:05 · the manager confirms" when I asked. */
	my_request: string | null
}

export interface InboxResponse {
	items: InboxItem[]
	counts: Record<InboxFilter, number>
	today: number
	waiting: number
	handout: HandoutMode
	handout_label: string
	/** The Sources card (stands in for the 1 Oct Connections page). */
	sources: Connection[]
}

export interface HandoutSettings {
	mode: HandoutMode
	options: { id: HandoutMode; label: string; line: string }[]
	/** Changes log, newest first; Restore puts a previous mode back. */
	log: {
		id: number
		at: string
		who: string
		from: HandoutMode
		to: HandoutMode
		from_label: string
		to_label: string
	}[]
	/** Lead stages per type (read only; renaming is open). */
	stages: { types: string; stages: string[]; proposal: boolean }[]
}

/* ── search, notifications, connections ── */

export interface SearchResults {
	leads: LeadRow[]
	deals: DealRow[]
	listings: ListingCard[]
	owners: OwnerRow[]
	/** Unit numbers and buildings from owner data; each opens its owner. */
	units: { owner_id: string; owner_name: string; unit: string; area: string }[]
}

export type RefKind = 'lead' | 'deal' | 'listing' | 'owner'

export interface Notification {
	id: number
	text: string
	detail: string
	unread: boolean
	ref: { kind: RefKind; id: string } | null
}

export interface Connection {
	id: string
	name: string
	detail: string
	status: string
	tone: Tone
	kind: 'source' | 'import'
}
