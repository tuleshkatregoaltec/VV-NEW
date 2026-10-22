/**
 * Labels, colours and form layouts for the enums in docs/crm/data-model.md. Words match the
 * docs exactly (Buyer, Seller, Tenant, Landlord; stage names from business-rules.md).
 */
import type {
	BudgetBand,
	DealType,
	EventKind,
	InvoiceStatus,
	LeadFieldKey,
	LeadStage,
	LeadType,
	Origin,
	OwnerPropertyKind,
	OwnerSource,
	PropertyKind,
	Source,
	Tone
} from './api/types'

export interface FieldDef {
	key: LeadFieldKey
	label: string
	money?: boolean
	options?: string[]
	/** Needed by the New Lead form: without it the backend can never match the lead. */
	required?: boolean
}

export const LEAD_TYPE_ORDER: LeadType[] = ['buyer', 'seller', 'tenant', 'landlord']

export const LEAD_TYPES: Record<
	LeadType,
	{
		label: string
		plural: string
		deal: DealType
		tone: Tone
		fields: FieldDef[]
		/** Buyers and tenants are matched by area, so the New Lead form asks for one. */
		areaRequired?: boolean
	}
> = {
	buyer: {
		label: 'Buyer',
		plural: 'Buyers',
		deal: 'sale',
		tone: 'blue',
		areaRequired: true,
		fields: [
			{ key: 'wants', label: 'Wants' },
			{ key: 'budget_aed', label: 'Budget', money: true, required: true },
			{ key: 'finance', label: 'Finance' },
			{ key: 'purpose', label: 'Buying to', options: ['Live in', 'Invest'] },
			{ key: 'timeline', label: 'Timeline' }
		]
	},
	seller: {
		label: 'Seller',
		plural: 'Sellers',
		deal: 'sale',
		tone: 'amber',
		fields: [
			{ key: 'building', label: 'Building', required: true },
			{ key: 'unit_number', label: 'Unit number', required: true },
			{ key: 'target_price_aed', label: 'Target price', money: true },
			{ key: 'occupancy', label: 'Occupancy' },
			{ key: 'mortgage', label: 'Mortgage' },
			{ key: 'timeline', label: 'Timeline' }
		]
	},
	tenant: {
		label: 'Tenant',
		plural: 'Tenants',
		deal: 'rent',
		tone: 'green',
		areaRequired: true,
		fields: [
			{ key: 'wants', label: 'Wants' },
			{ key: 'budget_aed', label: 'Budget a year', money: true, required: true },
			{ key: 'cheques', label: 'Cheques' },
			{ key: 'move_in', label: 'Move-in' }
		]
	},
	landlord: {
		label: 'Landlord',
		plural: 'Landlords',
		deal: 'rent',
		tone: 'neutral',
		fields: [
			{ key: 'building', label: 'Building', required: true },
			{ key: 'unit_number', label: 'Unit number', required: true },
			{ key: 'expected_rent_aed', label: 'Expected rent a year', money: true },
			{ key: 'available', label: 'Available' },
			{ key: 'furnishing', label: 'Furnishing' }
		]
	},
	/** A Vitevue lease lead before the agent classifies it as a seller or a landlord. */
	owner: {
		label: 'Seller or landlord',
		plural: 'Undecided',
		deal: 'sale',
		tone: 'outline',
		fields: [
			{ key: 'unit', label: 'Unit' },
			{ key: 'timeline', label: 'Timeline' }
		]
	}
}

export const SOURCES: Record<Source, { label: string; color: string }> = {
	inbound: { label: 'Inbound', color: 'var(--l-in)' },
	file: { label: 'Your owner file', color: 'var(--l-vv)' },
	own_contact: { label: 'Own contact', color: 'var(--l-own)' },
	agent_to_agent: { label: 'Agent to agent', color: 'var(--l-a2a)' },
	vitevue: { label: 'Vitevue lease lead', color: 'var(--l-vv)' }
}

/** Where a hand-entered lead came from; each maps to one source bucket. */
export const SOURCE_OPTIONS: { label: string; source: Source }[] = [
	{ label: 'Property Finder', source: 'inbound' },
	{ label: 'Bayut', source: 'inbound' },
	{ label: 'Meta ad', source: 'inbound' },
	{ label: 'Google ad', source: 'inbound' },
	{ label: 'WhatsApp', source: 'inbound' },
	{ label: 'Instagram', source: 'inbound' },
	{ label: 'Website form', source: 'inbound' },
	{ label: 'Own contact', source: 'own_contact' },
	{ label: 'Referral', source: 'own_contact' },
	{ label: 'Agent for a client', source: 'agent_to_agent' }
]

export const STAGES: { id: LeadStage; label: string; tone: Tone }[] = [
	{ id: 'new', label: 'New', tone: 'blue' },
	{ id: 'contacted', label: 'Contacted', tone: 'neutral' },
	{ id: 'qualified', label: 'Qualified', tone: 'green' },
	{ id: 'viewing', label: 'Viewing', tone: 'green' },
	{ id: 'listed', label: 'Listed', tone: 'green' },
	{ id: 'offer', label: 'Offer', tone: 'amber' },
	{ id: 'won', label: 'Won', tone: 'green' },
	{ id: 'lost', label: 'Lost', tone: 'red' }
]
export const stageOf = (id: LeadStage) => STAGES.find((s) => s.id === id) ?? STAGES[0]

/**
 * Stages by lead type (6 Oct): buyers and tenants go through Viewing; sellers and landlords
 * through Listed (proposal — names not set on the call). The backend refuses the others.
 */
export type LeadGroup = 'bt' | 'sl'
export const LEAD_GROUPS: Record<
	LeadGroup,
	{ label: string; types: LeadType[]; proposal: boolean }
> = {
	bt: { label: 'Buyers and tenants', types: ['buyer', 'tenant'], proposal: false },
	sl: { label: 'Sellers and landlords', types: ['seller', 'landlord', 'owner'], proposal: true }
}
export const groupOf = (t: LeadType): LeadGroup =>
	t === 'seller' || t === 'landlord' || t === 'owner' ? 'sl' : 'bt'
export const stagesFor = (t: LeadType) =>
	STAGES.filter((s) => s.id !== (groupOf(t) === 'sl' ? 'viewing' : 'listed'))

/** DLD Circular 02-2026: the line shown wherever an owner without a prior deal has an action. */
export const GATE_LINE = 'Add a prior deal record before contacting'

/** Off-plan buyer fields (7 Oct, planned). */
export const OFF_PLAN_FIELDS: FieldDef[] = [
	{ key: 'areas', label: 'Areas' },
	{ key: 'on_booking_aed', label: 'Can pay now', money: true },
	{ key: 'handover_by', label: 'Handover by' }
]

/* Deal stage names come from the API (GET /deal-stages): agencies may rename them (open). */

/** Stage form choices (business-rules.md, Lead stages). The backend checks them. */
export const CONTACT_OUTCOMES = [
	'Spoke to them',
	'No answer',
	'Asked to call back',
	'Replied on WhatsApp'
]
export const LOST_REASONS = [
	'Bought or rented elsewhere',
	'Not ready yet',
	'Budget does not fit',
	'No reply after 3 tries',
	'Not a real enquiry'
]

/** "All filters" choices (Leads and Owners). */
export const BEDS_OPTIONS = [
	{ id: '0', label: 'Studio' },
	{ id: '1', label: '1 bed' },
	{ id: '2', label: '2 beds' },
	{ id: '3', label: '3 beds' },
	{ id: '4', label: '4+ beds' }
]
export const PTYPE_OPTIONS: { id: PropertyKind; label: string }[] = [
	{ id: 'apartment', label: 'Apartment' },
	{ id: 'villa', label: 'Villa / townhouse' }
]
/**
 * Owners type filter: the main kinds in the owner files and DLD, plus "Not specified". Hotel and
 * commercial properties are recognised but have no option of their own (they show under Any type).
 */
export const OWNER_PTYPE_OPTIONS: { id: OwnerPropertyKind; label: string }[] = [
	{ id: 'apartment', label: 'Apartment' },
	{ id: 'villa', label: 'Villa / townhouse' },
	{ id: 'land', label: 'Land / plot' },
	{ id: 'building', label: 'Whole building' },
	{ id: 'unspecified', label: 'Not specified (“Unit”)' }
]

/**
 * The kind of property from a file's or DLD's type. Generic types ("Unit", "Residential", empty)
 * are "unspecified" rather than guessed as apartments.
 */
export function ownerPropertyKind(type: unknown): OwnerPropertyKind {
	const t = String(type ?? '')
		.trim()
		.toLowerCase()
	if (/villa|townhouse/.test(t)) return 'villa'
	if (/hotel/.test(t)) return 'hotel'
	if (/\bland\b|plot/.test(t)) return 'land'
	if (/flat|apartment/.test(t)) return 'apartment'
	if (/commercial|shop|office|show ?room|workshop|warehouse|clinic|retail|gym/.test(t))
		return 'commercial'
	if (/building/.test(t)) return 'building'
	return 'unspecified'
}

export const BUDGET_OPTIONS: { id: BudgetBand; label: string }[] = [
	{ id: 's1', label: 'Sale under 1m' },
	{ id: 's2', label: 'Sale 1–3m' },
	{ id: 's3', label: 'Sale over 3m' },
	{ id: 'r1', label: 'Rent under 100k' },
	{ id: 'r2', label: 'Rent 100k+' }
]
export const UNITS_OPTIONS = [
	{ id: '1', label: '1 unit' },
	{ id: '2', label: '2 units' },
	{ id: '3', label: '3 or more' }
]

export const EVENT_KINDS: Record<EventKind, { label: string; color: string }> = {
	viewing: { label: 'Viewing', color: 'var(--e-view)' },
	call: { label: 'Call', color: 'var(--e-call)' },
	follow_up: { label: 'Follow-up', color: 'var(--e-fu)' },
	deal_date: { label: 'Deal date', color: 'var(--e-deal)' },
	deadline: { label: 'Deadline', color: 'var(--e-dead)' }
}
export const EVENT_KIND_ORDER: EventKind[] = [
	'viewing',
	'call',
	'follow_up',
	'deal_date',
	'deadline'
]

/** Owner sources (7 Oct). Vitevue does not provide or sell owner data. */
export const OWNER_SOURCES: { id: OwnerSource; label: string }[] = [
	{ id: 'agent_contact', label: 'Own contact' },
	{ id: 'agency_import', label: 'Agency file' },
	{ id: 'dld_event', label: 'DLD/Ejari event' }
]

export const INVOICE: Record<InvoiceStatus, { label: string; tone: Tone }> = {
	not_invoiced: { label: 'Not invoiced', tone: 'neutral' },
	due: { label: 'Due', tone: 'amber' },
	received: { label: 'Received', tone: 'green' },
	overdue: { label: 'Overdue', tone: 'red' }
}

export const ORIGIN_TONE: Record<Origin, Tone> = {
	human: 'neutral',
	rule: 'neutral',
	model: 'blue',
	voice: 'dark'
}

/** Score chip: ≥70 dark · 45–69 grey · <45 outline. */
export const scoreClass = (score: number) => (score >= 70 ? '' : score >= 45 ? 'mid' : 'low')

export const ownerSourceTone = (label: string): Tone =>
	label === 'DLD/Ejari event' ? 'blue' : 'neutral'
