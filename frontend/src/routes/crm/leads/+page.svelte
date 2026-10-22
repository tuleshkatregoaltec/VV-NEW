<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Search from 'lucide-svelte/icons/search'
import { crm } from '$lib/crm/api'
import type { LeadListParams, LeadRow, LeadStage, LeadType, Source } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import FilterMenu from '$lib/crm/components/ui/FilterMenu.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import ScoreChip from '$lib/crm/components/ui/ScoreChip.svelte'
import Seg from '$lib/crm/components/ui/Seg.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import StageChip from '$lib/crm/components/ui/StageChip.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import {
	BEDS_OPTIONS,
	BUDGET_OPTIONS,
	GATE_LINE,
	groupOf,
	LEAD_GROUPS,
	LEAD_TYPE_ORDER,
	LEAD_TYPES,
	type LeadGroup,
	PTYPE_OPTIONS,
	SOURCES,
	STAGES,
	stagesFor
} from '$lib/crm/config'
import { fmt } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { openLead, panel } from '$lib/crm/stores/panel.svelte'
import { clearLeadFilters, type LeadTab, ui } from '$lib/crm/stores/ui.svelte'
import { errorText, write } from '$lib/crm/write'

const me = createQuery(() => crmQueries.me())
const feed = createQuery(() => crmQueries.feedSummary())

// Search updates the rows as you type; only the rows re-render, the input keeps focus.
let q = $state(ui.q)
$effect(() => {
	const v = ui.q.trim()
	const t = setTimeout(() => (q = v), 150)
	return () => clearTimeout(t)
})

const tabType = $derived(
	LEAD_TYPE_ORDER.includes(ui.leadTab as LeadType) ? (ui.leadTab as LeadType) : null
)
/** On the board, a type tab picks its own group; otherwise the group switch does. */
const group = $derived<LeadGroup>(tabType ? groupOf(tabType) : ui.leadGroup)

const params = $derived<LeadListParams>({
	lead_type: tabType ?? '',
	lost: ui.leadTab === 'lost' ? true : undefined,
	source: ui.source,
	stage: ui.stage,
	area: ui.area,
	beds: ui.beds,
	ptype: ui.ptype,
	budget: ui.budget,
	q,
	// Vitevue lease leads from My areas join the agency's own leads here.
	vitevue: true
})
const leads = createQuery(() => crmQueries.leads(params))
const counts = $derived(leads.data?.counts)
const more = $derived((ui.beds ? 1 : 0) + (ui.ptype ? 1 : 0) + (ui.budget ? 1 : 0))
const filtered = $derived(Boolean(ui.q || ui.source || ui.stage || ui.area || more))

const tabs = $derived([
	{ id: 'all' as LeadTab, label: 'All', count: counts?.all },
	...LEAD_TYPE_ORDER.map((t) => ({
		id: t as LeadTab,
		label: LEAD_TYPES[t].plural,
		count: counts?.[t]
	})),
	{ id: 'lost' as LeadTab, label: 'Lost', count: counts?.lost }
])

/** The Stage filter lists the stages of the type on screen (7 Oct). */
const stageOptions = $derived(
	tabType
		? stagesFor(tabType)
		: ui.leadView === 'board'
			? stagesFor(LEAD_GROUPS[group].types[0])
			: STAGES
)
const boardStages = $derived(stagesFor(LEAD_GROUPS[group].types[0]))

function pickGroup(g: LeadGroup) {
	ui.leadGroup = g
	if (tabType && groupOf(tabType) !== g) ui.leadTab = 'all'
	if (ui.stage && !stagesFor(LEAD_GROUPS[g].types[0]).some((s) => s.id === ui.stage)) ui.stage = ''
}

function selectTab(t: LeadTab) {
	ui.leadTab = t
	const type = LEAD_TYPE_ORDER.includes(t as LeadType) ? (t as LeadType) : null
	if (type && ui.stage && !stagesFor(type).some((s) => s.id === ui.stage)) ui.stage = ''
}

/** A Vitevue lease nobody has opened yet (id 0) becomes the agency's shared lead on open. */
async function open(l: LeadRow) {
	if (l.id) return openLead(l.id)
	if (!l.lease) return
	const leaseId = l.lease.id
	const r = await write({
		call: () => crm.leads.openVitevue(leaseId),
		success: `${l.lease.unit} is now a lead`
	})
	if (r) openLead(r.data.id)
}

const rowKey = (l: LeadRow) => (l.id ? String(l.id) : `lease:${l.lease?.id}`)

const typeLine = (l: LeadRow) =>
	[
		`${LEAD_TYPES[l.lead_type].label}${l.off_plan ? ' · off-plan' : ''}`,
		SOURCES[l.source].label === l.source_short ? null : l.source_short,
		l.listing?.label,
		l.received_label
	]
		.filter(Boolean)
		.join(' · ')
</script>

<svelte:head><title>Leads · CRM Beta</title></svelte:head>

<PageHeader
	title="Leads"
	facts={leads.data
		? `${leads.data.counts.all} leads · ${leads.data.new_count} new · ${me.data?.name ?? ''}`
		: ''}
>
	{#snippet right()}
		<Seg
			label="View"
			items={[
				{ id: 'list', label: 'List' },
				{ id: 'board', label: 'Board' }
			]}
			value={ui.leadView}
			onselect={(v) => (ui.leadView = v)}
		/>
	{/snippet}
</PageHeader>

<div class="card feedstrip">
	<span class="srcdot" style:--c="var(--l-vv)">
		{feed.data
			? `Lease ends · next ${feed.data.forward_days} days, last ${feed.data.lookback_days} days`
			: 'Lease ends'}
	</span>
	{#if feed.data && !feed.data.areas.length}
		<span class="sep">·</span>
		<span class="muted">Pick your areas to get renewal leads in the Inbox</span>
	{:else if feed.data}
		<span class="sep">·</span>
		<b>{feed.data.areas.join(', ')}</b>
		<span class="sep">·</span>
		<span><b class="num">{fmt(feed.data.window_total)}</b> units</span>
		<span class="sep">·</span>
		<span><b class="num">{fmt(feed.data.on_file)}</b> with a known owner</span>
		<span class="sep">·</span>
		<span><b class="num">{fmt(feed.data.entered_today)}</b> entered the inbox today</span>
	{:else if feed.isError}
		<span class="muted">
			Could not load. <button type="button" class="linkbtn" onclick={() => feed.refetch()}>Retry</button>
		</span>
	{/if}
	<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'areas' })}>My areas</button>
</div>

<Tabs label="Lead type" items={tabs} value={ui.leadTab} onselect={selectTab} />

<div class="filters">
	<label class="search">
		<Search size={15} />
		<span class="sr-only">Search leads</span>
		<input placeholder="Name, phone, unit" autocomplete="off" bind:value={ui.q} />
	</label>
	<FilterMenu
		label="Source"
		allLabel="All sources"
		value={ui.source}
		options={Object.entries(SOURCES).map(([id, s]) => ({ id: id as Source, label: s.label }))}
		onselect={(v) => (ui.source = v)}
	/>
	<FilterMenu
		label="Stage"
		allLabel="All stages"
		value={ui.stage}
		options={stageOptions.map((s) => ({ id: s.id as LeadStage, label: s.label }))}
		onselect={(v) => (ui.stage = v)}
	/>
	<FilterMenu
		label="Area"
		allLabel="All areas"
		value={ui.area}
		options={(leads.data?.areas ?? []).map((a) => ({ id: a, label: a }))}
		onselect={(v) => (ui.area = v)}
	/>
	<button
		type="button"
		class={['btn ghost sm fbtn', more && 'on']}
		aria-expanded={ui.leadMore}
		aria-controls="lead-more"
		onclick={() => (ui.leadMore = !ui.leadMore)}
	>
		All filters{more ? ` · ${more}` : ''}
	</button>
	{#if filtered}
		<button type="button" class="btn plain sm" onclick={clearLeadFilters}>Clear</button>
	{/if}
	<span class="count" aria-live="polite">
		{leads.data ? `${leads.data.total} lead${leads.data.total === 1 ? '' : 's'}` : ''}
	</span>
</div>
{#if ui.leadMore}
	<div class="card morefilters" id="lead-more">
		<FilterMenu
			label="Bedrooms"
			allLabel="Any bedrooms"
			value={ui.beds}
			options={BEDS_OPTIONS}
			onselect={(v) => (ui.beds = v)}
		/>
		<FilterMenu
			label="Type"
			allLabel="Any type"
			value={ui.ptype}
			options={PTYPE_OPTIONS}
			onselect={(v) => (ui.ptype = v)}
		/>
		<FilterMenu
			label="Budget"
			allLabel="Any budget"
			value={ui.budget}
			options={BUDGET_OPTIONS}
			onselect={(v) => (ui.budget = v)}
		/>
	</div>
{/if}

{#if ui.leadView === 'board'}
	<div class="boardhead">
		<Seg
			label="Lead group"
			items={(['bt', 'sl'] as LeadGroup[]).map((g) => ({ id: g, label: LEAD_GROUPS[g].label }))}
			value={group}
			onselect={pickGroup}
		/>
		<span class="muted small">
			{boardStages.map((s) => s.label).join(' → ')}
			{#if LEAD_GROUPS[group].proposal}
				<span
					class="chip outline"
					title="Seller and landlord stages were not named on the 6 Oct call. This is a proposal."
				>
					Proposal
				</span>
			{/if}
		</span>
	</div>
{/if}

{#if leads.data?.vitevue_unavailable}
	<ErrorLine bar message="Vitevue lease leads could not load. Your agency's own leads are listed." onretry={() => leads.refetch()} />
{/if}
{#if leads.isError && leads.data}
	<ErrorLine bar message={errorText(leads.error)} onretry={() => leads.refetch()} />
{/if}

{#if leads.data}
	{@const rows = leads.data.items}
	{#if ui.leadView === 'board'}
		{@const grows = rows.filter((l) => LEAD_GROUPS[group].types.includes(l.lead_type))}
		{#if grows.length}
			<div class="lboard">
				{#each boardStages as s (s.id)}
					{@const cards = grows.filter((l) => l.stage === s.id)}
					<section class="col" aria-label="{s.label}, {cards.length} leads">
						<div class="colh"><span>{s.label}</span><span>{cards.length}</span></div>
						{#each cards as l (rowKey(l))}
							<button
								type="button"
								class="bcard cl"
								style:--c={SOURCES[l.source].color}
								onclick={() => open(l)}
							>
								<span class="t">{l.name}</span>
								<span class="d">{LEAD_TYPES[l.lead_type].label} · {SOURCES[l.source].label}</span>
								<span class="d">{l.agent ?? 'Unassigned'} · {l.received_label}</span>
								{#if l.gated}<Chip tone="amber">No prior deal</Chip>{/if}
							</button>
						{:else}
							<span class="none">None</span>
						{/each}
					</section>
				{/each}
			</div>
		{:else}
			<div class="card">
				<Empty
					text={group === 'sl'
						? 'No seller or landlord leads match.'
						: 'No buyer or tenant leads match.'}
				>
					{#if filtered}
						<button type="button" class="btn ghost sm" onclick={clearLeadFilters}>Clear filters</button>
					{/if}
				</Empty>
			</div>
		{/if}
	{:else}
		<div class="card">
			<div class="tblwrap">
				<table class="tbl">
					<thead>
						<tr>
							<th>Lead</th><th>Details</th><th>On your agency's file</th><th>Score</th><th>Stage</th><th
								>Agent</th
							><th>Next</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as l (rowKey(l))}
							<tr class="row">
								<td class="cl" style:--c={SOURCES[l.source].color}>
									<button type="button" class="rowlink" onclick={() => open(l)}>{l.name}</button>
									<div class="sub">{SOURCES[l.source].label} · {typeLine(l)}</div>
								</td>
								<td data-label="Details">
									<div><div>{l.summary.line}</div><div class="sub">{l.summary.sub}</div></div>
								</td>
								<td class="sub" data-label="On file">{l.known}</td>
								<td data-label="Score"><ScoreChip score={l.score} /></td>
								<td data-label="Stage"><StageChip stage={l.stage} /></td>
								<td data-label="Agent" class="nowrap">
									{#if l.agent}{l.agent}{:else}<Chip tone="amber">Unassigned</Chip>{/if}
								</td>
								<td data-label="Next">
									{#if l.gated}
										<Chip tone="amber">No prior deal</Chip>
										<div class="sub">{GATE_LINE}</div>
									{:else}
										<span class="sub">{l.next_action}</span>
									{/if}
								</td>
							</tr>
						{:else}
							<tr>
								<td colspan="7">
									{#if filtered || ui.leadTab !== 'all'}
										<Empty text="No leads match these filters.">
											<button
												type="button"
												class="btn ghost sm"
												onclick={() => {
													clearLeadFilters()
													ui.leadTab = 'all'
												}}>Clear filters</button
											>
										</Empty>
									{:else}
										<Empty text="No leads yet. Enquiries land in the Inbox first.">
											<button type="button" class="btn sm" onclick={() => panel.open({ kind: 'new-lead' })}>
												New lead
											</button>
										</Empty>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
{:else if leads.isError}
	<div class="card"><ErrorLine message={errorText(leads.error)} onretry={() => leads.refetch()} /></div>
{:else}
	<div class="card"><Skeleton rows={6} /></div>
{/if}
