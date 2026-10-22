<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import type { MatchClient } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import MatchTiers from '$lib/crm/components/ui/MatchTiers.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import { LEAD_TYPES, SOURCES, stageOf } from '$lib/crm/config'
import { crmQueries } from '$lib/crm/queries'
import { openLead, openListing, panel } from '$lib/crm/stores/panel.svelte'
import { type MatchTabId, ui } from '$lib/crm/stores/ui.svelte'
import { errorText } from '$lib/crm/write'

/**
 * Matches (6 Oct, 07:17): a top-level section, not only a tab inside the lead. Three tabs:
 * Ready buyers and tenants · Off-plan buyers · Sellers and landlords.
 */
const clients = createQuery(() => crmQueries.matchClients())
const list = $derived<MatchClient[]>(clients.data?.[ui.matchTab] ?? [])
const selected = $derived(
	list.find((c) => c.lead.id === ui.matchSel[ui.matchTab]) ?? list[0] ?? null
)
const matches = createQuery(() => ({
	...crmQueries.leadMatches(selected?.lead.id ?? 0, { over: ui.matchOver, after: ui.matchAfter }),
	enabled: Boolean(selected)
}))
/** Keep showing the previous client's tiers only while the same client reloads. */
const m = $derived(matches.data && matches.data.lead.id === selected?.lead.id ? matches.data : null)

const tabs = $derived(
	(
		[
			['ready', 'Ready buyers and tenants'],
			['offplan', 'Off-plan buyers'],
			['sell', 'Sellers and landlords']
		] as [MatchTabId, string][]
	).map(([id, label]) => ({ id, label, count: clients.data?.[id].length }))
)

const EMPTY: Record<MatchTabId, [string, string]> = {
	ready: ['No buyer or tenant leads.', 'Add a lead to search for matches.'],
	offplan: ['No off-plan buyers.', 'Mark a buyer as off-plan to match Launch desk projects.'],
	sell: ['No seller or landlord leads.', 'Add a lead to see who fits their unit.']
}
</script>

<svelte:head><title>Matches · CRM Beta</title></svelte:head>

<PageHeader
	title="Matches"
	facts="Pursue sets the next step. Booking a viewing moves the stage. Telegram can do both."
/>

<Tabs label="Clients" items={tabs} value={ui.matchTab} onselect={(t) => (ui.matchTab = t)} />

{#if clients.data}
	{#if list.length}
		<div class="matches">
			<div class="card mlist" aria-label="Clients">
				<div class="list">
					{#each list as c (c.lead.id)}
						{@const on = selected?.lead.id === c.lead.id}
						<button
							type="button"
							class={['li cl', on && 'sel']}
							style:--c={SOURCES[c.lead.source].color}
							aria-pressed={on}
							onclick={() => (ui.matchSel[ui.matchTab] = c.lead.id)}
						>
							<span>
								<div class="t">{c.lead.name}</div>
								<div class="d">{c.line}</div>
								<div class="d">
									{LEAD_TYPES[c.lead.lead_type].label} · {stageOf(c.lead.stage).label}{c.pursued
										? ` · ${c.pursued} pursued`
										: ''}
								</div>
							</span>
						</button>
					{/each}
				</div>
			</div>

			<div class="mright">
				{#if m}
					<div class="card brief">
						<div class="ch cl" style:--c={SOURCES[m.lead.source].color}>
							<div>
								<h2>{m.lead.name}</h2>
								<div class="small muted">
									{m.tab === 'offplan' ? 'Off-plan buyer' : LEAD_TYPES[m.lead.lead_type].label} · {SOURCES[
										m.lead.source
									].label} · {stageOf(m.lead.stage).label} · next: {m.lead.next_action}
								</div>
							</div>
							<span class="row-chips">
								{#if m.status}<Chip chip={m.status} title="Planned: raised on the 6 Oct call, not built." />{/if}
								{#if m.own_listing}
									{@const own = m.own_listing}
									<button type="button" class="btn ghost sm" onclick={() => openListing(own.id)}>Open listing</button>
								{/if}
								<button type="button" class="btn ghost sm" onclick={() => openLead(m.lead.id)}>Open lead</button>
							</span>
						</div>
						<div class="cb brief-b">
							<dl>
								{#each m.brief as b (b.k)}<dt>{b.k}</dt><dd>{b.v}</dd>{/each}
							</dl>
							{#if m.tab !== 'sell'}
								<span class="row-chips">
									<label class="check">
										<input type="checkbox" bind:checked={ui.matchOver} />
										Up to 10% over budget
									</label>
									{#if m.tab === 'ready'}<span class="small muted">Shortlist: {m.shortlist}</span>{/if}
								</span>
							{/if}
						</div>
					</div>
					{#if matches.isError}
						<ErrorLine bar message={errorText(matches.error)} onretry={() => matches.refetch()} />
					{/if}
					<MatchTiers data={m} bind:after={ui.matchAfter} />
				{:else if matches.isError}
					<div class="card"><ErrorLine message={errorText(matches.error)} onretry={() => matches.refetch()} /></div>
				{:else}
					<div class="card"><Skeleton rows={4} /></div>
					<div class="card"><Skeleton rows={3} /></div>
				{/if}
			</div>
		</div>
	{:else}
		<div class="card">
			<Empty text="{EMPTY[ui.matchTab][0]} {EMPTY[ui.matchTab][1]}">
				<button type="button" class="btn sm" onclick={() => panel.open({ kind: 'new-lead' })}>New lead</button>
			</Empty>
		</div>
	{/if}
{:else if clients.isError}
	<div class="card"><ErrorLine message={errorText(clients.error)} onretry={() => clients.refetch()} /></div>
{:else}
	<div class="matches">
		<div class="card"><Skeleton rows={6} /></div>
		<div class="card"><Skeleton rows={6} /></div>
	</div>
{/if}
