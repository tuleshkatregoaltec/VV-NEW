/**
 * Per-screen view state (tabs, filters, switches). Kept for the session so coming back to a
 * screen shows it as it was left, like the reference mockup.
 */
import type {
	BudgetBand,
	DealTab,
	DealType,
	LeadStage,
	LeadType,
	ListingStatus,
	OwnerPropertyKind,
	OwnerSource,
	OwnerTab,
	PropertyKind,
	RenewalWindow,
	Source
} from '../api/types'

export type LeadTab = 'all' | LeadType | 'lost'
export type AssetTab = 'mine' | 'company' | 'portfolio' | 'renewals' | 'market'
export type HomeTodo = 'gaps' | 'file'
export type MatchTabId = 'ready' | 'offplan' | 'sell'

export const ui = $state({
	leadTab: 'all' as LeadTab,
	leadView: 'list' as 'list' | 'board',
	/** Leads › Board: which group's columns show (6 Oct). */
	leadGroup: 'bt' as 'bt' | 'sl',
	q: '',
	source: '' as Source | '',
	stage: '' as LeadStage | '',
	area: '',
	/** Leads → All filters */
	leadMore: false,
	beds: '',
	ptype: '' as PropertyKind | '',
	budget: '' as BudgetBand | '',
	/** Conveyancing is the default Deals view (1 Oct). */
	dealTab: 'conveyancing' as DealTab,
	convType: 'sale' as DealType,
	/** Deals → Conveyancing: All / Sale / Rent and the selected deal. */
	cvType: '' as DealType | '',
	cvSel: '',
	assetTab: 'mine' as AssetTab,
	assetStatus: 'live' as ListingStatus,
	/** Assets › Renewals opens on vacant units of the last 60 days. */
	renewalWindow: 'vacant' as RenewalWindow,
	renewalArea: '',
	renewalPtype: '',
	renewalProject: '',
	renewalBuilding: '',
	ownerTab: 'all' as OwnerTab,
	/** Owners search: owner, unit, building (v2). */
	ownerQ: '',
	ownerSource: '' as OwnerSource | '',
	/** Owners → unit-level filters */
	ownerMore: false,
	ownerArea: '',
	ownerBeds: '',
	ownerPtype: '' as OwnerPropertyKind | '',
	ownerUnits: '',
	/** Owners per page on the Owners table: 50, 100, 200 or 500. */
	ownerPageSize: 50,
	/** YYYY-MM-DD; empty = today */
	weekDay: '',
	/** YYYY-MM; empty = this month */
	calMonth: '',
	homeTodo: 'gaps' as HomeTodo,
	/** Matches: tab, the selected client per tab, up to 10% over budget, show after the move date. */
	matchTab: 'ready' as MatchTabId,
	matchSel: { ready: 0, offplan: 0, sell: 0 } as Record<MatchTabId, number>,
	matchOver: true,
	matchAfter: false,
	/** Inbox: filter and the selected enquiry. */
	inboxFilter: 'all' as 'all' | 'mine' | 'waiting' | 'dismissed',
	inboxSel: ''
})

export function clearLeadFilters() {
	ui.q = ''
	ui.source = ''
	ui.stage = ''
	ui.area = ''
	ui.beds = ''
	ui.ptype = ''
	ui.budget = ''
}

export function clearOwnerFilters() {
	ui.ownerQ = ''
	ui.ownerSource = ''
	ui.ownerArea = ''
	ui.ownerBeds = ''
	ui.ownerPtype = ''
	ui.ownerUnits = ''
}
