import { keepPreviousData } from '@tanstack/svelte-query'
import { crm } from './api'
import type {
	DealTab,
	DealType,
	InboxFilter,
	LeadListParams,
	ListingScope,
	ListingStatus,
	MatchOptions,
	OwnerDroppedRowsParams,
	OwnerImportDetail,
	OwnerImportRowsParams,
	OwnerListParams,
	OwnerPropertyParams,
	RenewalListParams
} from './api/types'

/** Every CRM Beta key starts with 'crm', so a write can refresh the whole CRM at once. */
export const crmKeys = {
	all: ['crm'] as const,
	me: () => [...crmKeys.all, 'me'] as const,
	counts: () => [...crmKeys.all, 'counts'] as const,
	home: (day: string) => [...crmKeys.all, 'home', day] as const,
	leads: (p: LeadListParams) => [...crmKeys.all, 'leads', p] as const,
	lead: (id: number) => [...crmKeys.all, 'lead', id] as const,
	leadEvents: (id: number) => [...crmKeys.all, 'lead', id, 'events'] as const,
	leadMatches: (id: number, o: MatchOptions = {}) =>
		[...crmKeys.all, 'lead', id, 'matches', Boolean(o.over ?? true), Boolean(o.after)] as const,
	matchClients: () => [...crmKeys.all, 'matches', 'clients'] as const,
	inbox: (filter: InboxFilter) => [...crmKeys.all, 'inbox', filter] as const,
	handout: () => [...crmKeys.all, 'settings', 'handout'] as const,
	leadConversation: (id: number) => [...crmKeys.all, 'lead', id, 'conversation'] as const,
	feedSummary: () => [...crmKeys.all, 'feed', 'summary'] as const,
	feedAreas: () => [...crmKeys.all, 'feed', 'areas'] as const,
	feedPreferences: () => [...crmKeys.all, 'feed', 'preferences'] as const,
	dealStages: () => [...crmKeys.all, 'deal-stages'] as const,
	deals: (tab: DealTab, type: DealType | '' = '') => [...crmKeys.all, 'deals', tab, type] as const,
	deal: (id: string) => [...crmKeys.all, 'deal', id] as const,
	dealForm: (id: string, code: string) => [...crmKeys.all, 'deal', id, 'form', code] as const,
	listings: (scope: ListingScope, status?: ListingStatus) =>
		[...crmKeys.all, 'listings', scope, status ?? 'all'] as const,
	listing: (id: string) => [...crmKeys.all, 'listing', id] as const,
	portfolio: () => [...crmKeys.all, 'portfolio'] as const,
	renewals: (p: RenewalListParams) => [...crmKeys.all, 'renewals', p] as const,
	renewal: (id: string) => [...crmKeys.all, 'renewal', id] as const,
	market: () => [...crmKeys.all, 'market'] as const,
	marketListing: (id: string) => [...crmKeys.all, 'market', id] as const,
	owners: (p: OwnerListParams) => [...crmKeys.all, 'owners', p] as const,
	owner: (id: string) => [...crmKeys.all, 'owner', id] as const,
	ownerReport: (id: string) => [...crmKeys.all, 'owner', id, 'report'] as const,
	imports: () => [...crmKeys.all, 'imports'] as const,
	ownerImports: () => [...crmKeys.all, 'imports', 'owners'] as const,
	ownerImportDetails: () => [...crmKeys.all, 'imports', 'owners', 'details'] as const,
	ownerImportRows: (listId: number, p: OwnerImportRowsParams) =>
		[...crmKeys.all, 'imports', 'owners', 'rows', listId, p] as const,
	ownerImportDropped: (importId: number, p: OwnerDroppedRowsParams) =>
		[...crmKeys.all, 'imports', 'owners', 'dropped', importId, p] as const,
	ownerImportProperties: (importId: number, p: OwnerPropertyParams) =>
		[...crmKeys.all, 'imports', 'owners', 'properties', importId, p] as const,
	events: (from: string, to: string) => [...crmKeys.all, 'events', from, to] as const,
	event: (id: number) => [...crmKeys.all, 'event', id] as const,
	calendarSide: () => [...crmKeys.all, 'calendar-side'] as const,
	search: (q: string) => [...crmKeys.all, 'search', q] as const,
	notifications: () => [...crmKeys.all, 'notifications'] as const,
	connections: () => [...crmKeys.all, 'connections'] as const
}

/** Query option factories, same pattern as the main app's `$lib/queries/*`. */
export const crmQueries = {
	me: () => ({ queryKey: crmKeys.me(), queryFn: crm.me, staleTime: Number.POSITIVE_INFINITY }),
	counts: () => ({ queryKey: crmKeys.counts(), queryFn: crm.counts }),
	home: (day: string) => ({
		queryKey: crmKeys.home(day),
		queryFn: () => crm.home(day),
		placeholderData: keepPreviousData
	}),
	leads: (p: LeadListParams) => ({
		queryKey: crmKeys.leads(p),
		queryFn: () => crm.leads.list(p),
		placeholderData: keepPreviousData
	}),
	lead: (id: number) => ({ queryKey: crmKeys.lead(id), queryFn: () => crm.leads.get(id) }),
	leadEvents: (id: number) => ({
		queryKey: crmKeys.leadEvents(id),
		queryFn: () => crm.leads.events(id)
	}),
	leadMatches: (id: number, o: MatchOptions = {}) => ({
		queryKey: crmKeys.leadMatches(id, o),
		queryFn: () => crm.leads.matches(id, o),
		placeholderData: keepPreviousData
	}),
	matchClients: () => ({ queryKey: crmKeys.matchClients(), queryFn: crm.matches.clients }),
	inbox: (filter: InboxFilter) => ({
		queryKey: crmKeys.inbox(filter),
		queryFn: () => crm.inbox.list(filter),
		placeholderData: keepPreviousData
	}),
	handout: () => ({ queryKey: crmKeys.handout(), queryFn: crm.settings.handout }),
	leadConversation: (id: number) => ({
		queryKey: crmKeys.leadConversation(id),
		queryFn: () => crm.leads.conversation(id)
	}),
	feedSummary: () => ({ queryKey: crmKeys.feedSummary(), queryFn: crm.feed.summary }),
	feedAreas: () => ({ queryKey: crmKeys.feedAreas(), queryFn: crm.feed.areas }),
	feedPreferences: () => ({ queryKey: crmKeys.feedPreferences(), queryFn: crm.feed.preferences }),
	dealStages: () => ({
		queryKey: crmKeys.dealStages(),
		queryFn: crm.deals.stages,
		staleTime: Number.POSITIVE_INFINITY
	}),
	deals: (tab: DealTab, type: DealType | '' = '') => ({
		queryKey: crmKeys.deals(tab, type),
		queryFn: () => crm.deals.list(tab, type),
		placeholderData: keepPreviousData
	}),
	deal: (id: string) => ({ queryKey: crmKeys.deal(id), queryFn: () => crm.deals.get(id) }),
	dealForm: (id: string, code: string) => ({
		queryKey: crmKeys.dealForm(id, code),
		queryFn: () => crm.deals.form(id, code)
	}),
	listings: (scope: ListingScope, status?: ListingStatus) => ({
		queryKey: crmKeys.listings(scope, status),
		queryFn: () => crm.listings.list(scope, status),
		placeholderData: keepPreviousData
	}),
	listing: (id: string) => ({ queryKey: crmKeys.listing(id), queryFn: () => crm.listings.get(id) }),
	portfolio: () => ({ queryKey: crmKeys.portfolio(), queryFn: crm.portfolio.get }),
	renewals: (p: RenewalListParams) => ({
		queryKey: crmKeys.renewals(p),
		// Passing the signal lets a filter change abandon the previous request.
		queryFn: ({ signal }: { signal: AbortSignal }) => crm.renewals.list(p, signal),
		placeholderData: keepPreviousData
	}),
	renewal: (id: string) => ({ queryKey: crmKeys.renewal(id), queryFn: () => crm.renewals.get(id) }),
	market: () => ({ queryKey: crmKeys.market(), queryFn: crm.market.list }),
	marketListing: (id: string) => ({
		queryKey: crmKeys.marketListing(id),
		queryFn: () => crm.market.get(id)
	}),
	owners: (p: OwnerListParams) => ({
		queryKey: crmKeys.owners(p),
		queryFn: () => crm.owners.list(p),
		placeholderData: keepPreviousData
	}),
	owner: (id: string) => ({ queryKey: crmKeys.owner(id), queryFn: () => crm.owners.get(id) }),
	ownerReport: (id: string) => ({
		queryKey: crmKeys.ownerReport(id),
		queryFn: () => crm.owners.report(id)
	}),
	imports: () => ({ queryKey: crmKeys.imports(), queryFn: crm.imports.list }),
	ownerImports: () => ({ queryKey: crmKeys.ownerImports(), queryFn: crm.imports.ownerHistory }),
	/** Re-checks every 5 s while an import is still processing, so its counts appear by themselves. */
	ownerImportDetails: () => ({
		queryKey: crmKeys.ownerImportDetails(),
		queryFn: crm.imports.ownerDetails,
		refetchInterval: (q: { state: { data?: OwnerImportDetail[] } }) =>
			q.state.data?.some((i) => i.status === 'processing') ? 5000 : false
	}),
	ownerImportRows: (listId: number, p: OwnerImportRowsParams) => ({
		queryKey: crmKeys.ownerImportRows(listId, p),
		queryFn: () => crm.imports.ownerRows(listId, p),
		placeholderData: keepPreviousData
	}),
	ownerImportDropped: (importId: number, p: OwnerDroppedRowsParams) => ({
		queryKey: crmKeys.ownerImportDropped(importId, p),
		queryFn: () => crm.imports.ownerDropped(importId, p),
		placeholderData: keepPreviousData
	}),
	ownerImportProperties: (importId: number, p: OwnerPropertyParams) => ({
		queryKey: crmKeys.ownerImportProperties(importId, p),
		queryFn: () => crm.imports.ownerProperties(importId, p),
		placeholderData: keepPreviousData
	}),
	events: (from: string, to: string) => ({
		queryKey: crmKeys.events(from, to),
		queryFn: () => crm.events.list(from, to),
		placeholderData: keepPreviousData
	}),
	event: (id: number) => ({ queryKey: crmKeys.event(id), queryFn: () => crm.events.get(id) }),
	calendarSide: () => ({ queryKey: crmKeys.calendarSide(), queryFn: crm.events.side }),
	search: (q: string) => ({
		queryKey: crmKeys.search(q),
		queryFn: () => crm.search(q),
		enabled: q.length >= 2,
		placeholderData: keepPreviousData
	}),
	notifications: () => ({ queryKey: crmKeys.notifications(), queryFn: crm.notifications.list }),
	connections: () => ({ queryKey: crmKeys.connections(), queryFn: crm.connections })
}
