/**
 * The side panel. Record panels (lead, deal, listing, owner) are deep-linkable through the URL
 * (?lead=3, ?deal=DL-201&tab=documents); the rest (search, forms, feed…) are transient.
 *
 * Panels stack: opening search over a lead and closing it brings the lead (and its URL) back.
 * Close and Escape go back one panel; a click outside closes them all.
 */
import { replaceState } from '$app/navigation'
import type { LeadStage, LeadType } from '../api/types'

export type LeadPanelTab =
	| 'overview'
	| 'conversation'
	| 'matches'
	| 'history'
	| 'edit'
	| 'book'
	| 'note'
	| `stage:${LeadStage}`
export type DealPanelTab = 'overview' | 'documents' | 'commission' | 'forms' | 'compliance'
export type RecordKind = 'lead' | 'deal' | 'listing' | 'owner'

export type PanelState =
	| { kind: 'lead'; id: number; tab?: LeadPanelTab }
	| { kind: 'deal'; id: string; tab?: DealPanelTab }
	| { kind: 'listing'; id: string }
	| { kind: 'owner'; id: string; unit?: string }
	| { kind: 'market'; id: string }
	| { kind: 'client'; client: string }
	| { kind: 'renewal'; id: string }
	| { kind: 'event'; id: number }
	| { kind: 'day'; date: string }
	| { kind: 'search' }
	| { kind: 'notifications' }
	| { kind: 'new' }
	| { kind: 'new-lead'; leadType?: LeadType }
	| { kind: 'new-listing'; fromLeadId?: number }
	| { kind: 'new-deal'; leadId?: number }
	| { kind: 'new-task'; date?: string }
	| { kind: 'new-owner' }
	| { kind: 'new-import'; ownersOnly?: boolean }
	| { kind: 'areas' }
	| { kind: 'owner-lead'; id: string }
	| { kind: 'add-record'; ownerId: string }
	| { kind: 'send'; target: 'listing' | 'market'; id: string }
	| { kind: 'brochure'; id: string }
	| { kind: 'owner-report'; id: string }
	| { kind: 'which-doc'; dealId: string; files: File[]; index: number }

const RECORD_PARAMS = ['lead', 'deal', 'listing', 'owner', 'tab'] as const

function toParams(p: PanelState | null): Record<string, string> {
	if (!p) return {}
	if (p.kind === 'lead')
		return { lead: String(p.id), ...(p.tab && p.tab !== 'overview' ? { tab: p.tab } : {}) }
	if (p.kind === 'deal')
		return { deal: p.id, ...(p.tab && p.tab !== 'overview' ? { tab: p.tab } : {}) }
	if (p.kind === 'listing') return { listing: p.id }
	if (p.kind === 'owner') return { owner: p.id }
	return {}
}

const LEAD_TABS = ['overview', 'conversation', 'matches', 'history']
const DEAL_TABS = ['overview', 'documents', 'commission', 'forms', 'compliance']

export function panelFromUrl(url: URL): PanelState | null {
	const s = url.searchParams
	const tab = s.get('tab') ?? undefined
	const lead = s.get('lead')
	if (lead && Number.isFinite(Number(lead)))
		return {
			kind: 'lead',
			id: Number(lead),
			tab: tab && LEAD_TABS.includes(tab) ? (tab as LeadPanelTab) : undefined
		}
	const deal = s.get('deal')
	if (deal)
		return {
			kind: 'deal',
			id: deal,
			tab: tab && DEAL_TABS.includes(tab) ? (tab as DealPanelTab) : undefined
		}
	const listing = s.get('listing')
	if (listing) return { kind: 'listing', id: listing }
	const owner = s.get('owner')
	if (owner) return { kind: 'owner', id: owner }
	return null
}

/** Build an href that opens a record panel on a given route, e.g. recordHref('/crm/leads', {kind:'lead', id:3}). */
export function recordHref(path: string, p: PanelState): string {
	const q = new URLSearchParams(toParams(p)).toString()
	return q ? `${path}?${q}` : path
}

const MAX_STACK = 12
const sameRecord = (a: PanelState | null, b: PanelState | null) =>
	Boolean(a && b) &&
	JSON.stringify(toParams(a)) === JSON.stringify(toParams(b)) &&
	a?.kind === b?.kind

class PanelStore {
	current = $state<PanelState | null>(null)
	/** Panels under the current one; Close goes back to the last. */
	#stack: PanelState[] = []
	#opener: HTMLElement | null = null

	/**
	 * Open a panel over the current one. `replace` swaps the current panel instead (the next
	 * file of a batch, the next page of a list, a form that became its record).
	 */
	open(p: PanelState, opts: { replace?: boolean } = {}) {
		const c = this.current
		if (!c && typeof document !== 'undefined') {
			this.#opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
		}
		if (c && !opts.replace && !this.#same(c, p)) {
			this.#stack.push(c)
			if (this.#stack.length > MAX_STACK) this.#stack.shift()
		}
		this.current = p
		this.#syncUrl()
	}

	/** Back to the previous panel (and its URL), or closed when there is none. */
	close() {
		if (!this.current) return
		const prev = this.#stack.pop()
		if (prev) {
			this.current = prev
			this.#syncUrl()
			return
		}
		this.closeAll()
	}

	closeAll() {
		this.#stack = []
		if (!this.current) return
		this.current = null
		this.#syncUrl()
		const opener = this.#opener
		this.#opener = null
		if (opener?.isConnected) opener.focus()
	}

	/** A record that no longer exists (undone create): drop every panel showing it. */
	drop(kind: RecordKind, id: string | number) {
		const shows = (p: PanelState) => p.kind === kind && 'id' in p && String(p.id) === String(id)
		this.#stack = this.#stack.filter((p) => !shows(p))
		if (this.current && shows(this.current)) this.close()
	}

	setTab(tab: string) {
		const c = this.current
		if (c?.kind === 'lead') this.current = { ...c, tab: tab as LeadPanelTab }
		else if (c?.kind === 'deal') this.current = { ...c, tab: tab as DealPanelTab }
		this.#syncUrl()
	}

	/** Called after every navigation: open the record the URL names, or close what was open. */
	syncFromUrl(url: URL) {
		const p = panelFromUrl(url)
		if (p && sameRecord(p, this.current)) return
		this.#stack = []
		this.current = p
	}

	#same(a: PanelState, b: PanelState) {
		if (a.kind !== b.kind) return false
		const { tab: _a, ...ra } = a as PanelState & { tab?: string }
		const { tab: _b, ...rb } = b as PanelState & { tab?: string }
		return JSON.stringify(ra) === JSON.stringify(rb)
	}

	#syncUrl() {
		if (typeof window === 'undefined') return
		const url = new URL(window.location.href)
		for (const k of RECORD_PARAMS) url.searchParams.delete(k)
		for (const [k, v] of Object.entries(toParams(this.current))) url.searchParams.set(k, v)
		if (url.href !== window.location.href) {
			try {
				replaceState(url, {})
			} catch {
				// router not ready yet (first render); the URL catches up on the next change
			}
		}
	}
}

export const panel = new PanelStore()

export const openLead = (id: number, tab?: LeadPanelTab) => panel.open({ kind: 'lead', id, tab })
export const openDeal = (id: string, tab?: DealPanelTab) => panel.open({ kind: 'deal', id, tab })
export const openListing = (id: string) => panel.open({ kind: 'listing', id })
/** `unit` (a unit number) opens that unit's row, e.g. the row clicked on an import report. */
export const openOwner = (id: string, unit?: string) => panel.open({ kind: 'owner', id, unit })
export const closeRecord = (kind: RecordKind, id: string | number) => panel.drop(kind, id)

export function openRef(ref: { kind: string; id: string | number }) {
	if (ref.kind === 'lead') openLead(Number(ref.id))
	else if (ref.kind === 'deal') openDeal(String(ref.id))
	else if (ref.kind === 'listing') openListing(String(ref.id))
	else if (ref.kind === 'owner') openOwner(String(ref.id))
}
