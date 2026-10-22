<script lang="ts">
import Check from 'lucide-svelte/icons/check'
import { goto } from '$app/navigation'
import { crm } from '../../api'
import type { LeadMatches, MatchRow } from '../../api/types'
import { GATE_LINE } from '../../config'
import { dayLabel } from '../../format'
import { openRef, panel } from '../../stores/panel.svelte'
import { ui } from '../../stores/ui.svelte'
import { write } from '../../write'
import Chip from './Chip.svelte'

/**
 * The tiers of one client's matches, the same in Matches and in the lead's Matches tab.
 * Ready: 1 Company listings · 2 Owners on your file · 3 Public listings · Unit events.
 * Off-plan: Launch desk projects that fit · Company allocations. Sellers and landlords: buyers or
 * tenants who fit (leads in the agency, then enquiries waiting in the inbox).
 * Pursue sets the next step; booking a viewing moves the stage (6 Oct, 10:39).
 */
let { data, after = $bindable(false) }: { data: LeadMatches; after?: boolean } = $props()

const leadId = $derived(data.lead.id)
const LINE: Record<MatchRow['line'], string> = {
	own: 'var(--l-own)',
	vv: 'var(--l-vv)',
	in: 'var(--l-in)',
	a2a: 'var(--l-a2a)'
}

function pursue(r: MatchRow) {
	write({
		call: () => crm.leads.pursue(leadId, r.key),
		success: (res) =>
			res.data.pursued
				? `Match pursued · next: ${res.data.pursued.next}`
				: `Stopped pursuing ${r.title}`,
		event: 'match_pursued'
	})
}

function shortlist(r: MatchRow) {
	write({
		call: () => crm.leads.shortlist(leadId, r.key),
		success: (res) => (res.data.on ? `On the shortlist · ${res.data.count}` : 'Off the shortlist')
	})
}

function watch(r: MatchRow) {
	write({
		call: () => crm.matches.watchUnit(r.key),
		success: (res) => (res.data.on ? `Watching ${r.title}` : `Stopped watching ${r.title}`)
	})
}

function renewal(r: MatchRow) {
	write({
		call: () => crm.leads.renewalCall(leadId, r.key),
		success: (res) => `Renewal call planned · ${dayLabel(res.data.date)} ${res.data.time ?? ''}`
	})
}

function ask(r: MatchRow) {
	write({
		call: () => crm.leads.ask(leadId, r.key),
		success: (res) => `Asked ${res.data.asked} · logged on ${data.lead.name}`
	})
}

/** Booking a viewing moves the stage. A seller-side row books it on the buyer or tenant's lead. */
function book(r: MatchRow) {
	const id = r.kind === 'lead' && r.ref ? Number(r.ref.id) : leadId
	panel.open({ kind: 'lead', id, tab: 'book' })
}

function openRow(r: MatchRow) {
	const ref = r.ref
	if (!ref) return
	if (ref.kind === 'market') panel.open({ kind: 'market', id: ref.id })
	else if (ref.kind === 'inbox') {
		ui.inboxSel = ref.id
		void goto('/crm/inbox')
	} else openRef(ref)
}

const OPEN_LABEL: Partial<Record<MatchRow['kind'], string>> = {
	listing: 'Open listing',
	seller_lead: 'Open seller lead',
	owner: 'Open owner',
	lead: 'Open lead',
	inbox: 'Open in inbox'
}
</script>

{#snippet hit(r: MatchRow)}
	<div class={['hit cl', r.kind === 'project' && !r.pursuable && 'nofit']} style:--c={LINE[r.line]}>
		<div class="main2">
			<div class="t1">{r.title}</div>
			<div class="t2">{r.sub}</div>
			{#if r.why.length}<div class="t2 why">{r.why.join(' · ')}</div>{/if}
		</div>
		{#if r.lead_stages}<span class="t2 hide-md">{r.lead_stages}</span>{/if}
		<Chip chip={r.over_budget} />
		<Chip chip={r.chip} />
		{#if OPEN_LABEL[r.kind] && r.ref}
			<button type="button" class="btn ghost sm" onclick={() => openRow(r)}>{OPEN_LABEL[r.kind]}</button>
		{/if}
		{#if r.colleague && r.kind !== 'colleague_lead'}
			<button type="button" class="btn ghost sm" onclick={() => ask(r)}>Ask {r.colleague}</button>
		{:else if r.colleague && data.tab === 'ready'}
			<button type="button" class="btn ghost sm" onclick={() => ask(r)}>Ask {r.colleague}</button>
		{/if}
		{#if r.kind === 'owner' && r.owner}
			{#if r.owner.prior_deal}
				<button type="button" class="btn ghost sm" aria-pressed={r.renewal_planned} disabled={r.renewal_planned} onclick={() => renewal(r)}>
					{#if r.renewal_planned}<Check size={14} />Renewal call planned{:else}Plan a renewal call{/if}
				</button>
			{:else}
				{@const ownerId = r.owner.id}
				<span class="gateline">
					{GATE_LINE}
					<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'add-record', ownerId })}>
						Add record
					</button>
				</span>
			{/if}
		{/if}
		{#if r.kind === 'unit_event'}
			<button type="button" class="btn ghost sm" aria-pressed={r.watching} onclick={() => watch(r)}>
				{#if r.watching}<Check size={14} />Watching{:else}Watch unit{/if}
			</button>
		{/if}
		{#if data.tab === 'ready' && (r.kind === 'listing' || r.kind === 'colleague_listing' || r.kind === 'seller_lead' || r.kind === 'public')}
			<button type="button" class="btn ghost sm" aria-pressed={r.shortlisted} onclick={() => shortlist(r)}>
				{#if r.shortlisted}<Check size={14} />On the shortlist{:else}Shortlist{/if}
			</button>
		{/if}
		{#if r.pursuable}
			<button
				type="button"
				class="btn ghost sm"
				aria-pressed={Boolean(r.pursued)}
				title={r.pursued ? `Next: ${r.pursued.next}. Click to stop.` : 'Sets the next action'}
				onclick={() => pursue(r)}
			>
				{#if r.pursued}<Check size={14} />Pursued{:else}Pursue{/if}
			</button>
			{#if r.pursued && r.can_book}
				<button type="button" class="btn sm" onclick={() => book(r)}>Book viewing</button>
			{/if}
		{/if}
	</div>
{/snippet}

{#each data.tiers as t (t.id)}
	<section class="card tier" aria-label={t.title}>
		<div class="ch">
			<h2>
				{#if t.n}<span class="tn">{t.n}</span>{/if}
				{t.title}
				<span class="facts">
					{t.sub}{#if t.hidden_after && data.move_by}
						· <button type="button" class="linkbtn" onclick={() => (after = true)}>
							Show {t.hidden_after} after {dayLabel(data.move_by)}
						</button>
					{:else if after && data.move_by && (t.id === 'owners' || t.id === 'events')}
						· <button type="button" class="linkbtn" onclick={() => (after = false)}>
							Hide after {dayLabel(data.move_by)}
						</button>
					{/if}
				</span>
			</h2>
			{#if t.chip}<Chip chip={t.chip} title="Raised on the 6 Oct call, not built yet." />{/if}
		</div>
		{#if t.gate}
			{@const ownerId = t.gate.owner_id}
			<div class="note gate gatebar">
				<span>{GATE_LINE}.</span>
				<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'add-record', ownerId })}>
					Add record
				</button>
			</div>
		{/if}
		{#each t.groups as g, gi (gi)}
			{#if g.label}<div class="subh">{g.label}</div>{/if}
			{#each g.rows as r (r.key)}
				{@render hit(r)}
			{:else}
				<div class="nomatch">{g.empty}</div>
			{/each}
		{/each}
	</section>
{/each}
