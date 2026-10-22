<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ArrowLeft from 'lucide-svelte/icons/arrow-left'
import { goto } from '$app/navigation'
import { page } from '$app/state'
import { crm } from '$lib/crm/api'
import type { OwnerImportDetail } from '$lib/crm/api/types'
import DroppedRows from '$lib/crm/components/imports/DroppedRows.svelte'
import ImportRows from '$lib/crm/components/imports/ImportRows.svelte'
import PropertyRows from '$lib/crm/components/imports/PropertyRows.svelte'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import InfoTip from '$lib/crm/components/ui/InfoTip.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import QueryView from '$lib/crm/components/ui/QueryView.svelte'
import { fmt } from '$lib/crm/format'
import {
	type ClaimStatus,
	DROP_REASON_INFO,
	type DropReason,
	STATUS_HELP,
	STATUS_INFO
} from '$lib/crm/ownerImportReport'
import { crmQueries } from '$lib/crm/queries'
import { write } from '$lib/crm/write'

/**
 * One owner file's report: the row funnel (rows → dropped → merged → kept → matched), a tile per
 * DLD status, and the kept rows themselves, opening on all statuses.
 */
const imports = createQuery(() => crmQueries.ownerImportDetails())
const id = $derived(Number(page.params.id))
const imp = $derived(imports.data?.find((i) => i.id === id) ?? null)

const STATUS = {
	completed: { label: 'Completed', tone: 'green' },
	processing: { label: 'Processing', tone: 'amber' },
	failed: { label: 'Failed', tone: 'red' }
} as const
const statuses = Object.keys(STATUS_INFO) as ClaimStatus[]

let editing = $state(false)
let draft = $state('')
let busy = $state(false)
let confirming = $state(false)

// The rows open on the first failed status that has rows; after that the tiles and the select drive
// it. A processing import has no counts yet, so the choice waits until it has finished. Moving to
// another import also closes any open rename or delete prompt.
// The owner rows open on "All statuses"; the tiles and the select narrow them. Moving to another
// import resets the filter and closes any open rename or delete prompt.
let status = $state<ClaimStatus | null>(null)
// The Dropped card opens the rows the import did not keep, with the reason for each.
let showDropped = $state(false)
// The Properties card opens the import's distinct units, each with its buyer/seller rows.
let showProperties = $state(false)
let shownFor = $state<number | null>(null)
$effect.pre(() => {
	if (!imp || shownFor === imp.id) return
	shownFor = imp.id
	status = null
	showDropped = false
	showProperties = false
	editing = false
	confirming = false
	draft = ''
})

async function rename(i: OwnerImportDetail) {
	const name = draft.trim()
	if (!name || name === i.name) {
		editing = false
		return
	}
	busy = true
	const r = await write({
		call: () => crm.imports.renameOwners(i.id, name),
		success: `Renamed · ${name}`,
		event: 'import_renamed'
	})
	busy = false
	if (r) editing = false
}

async function remove(i: OwnerImportDetail) {
	busy = true
	const r = await write({
		call: () => crm.imports.removeOwners(i.id),
		success: `Deleted · ${i.name}`,
		event: 'import_deleted'
	})
	busy = false
	if (r) await goto('/crm/owners/imports')
}

// "120 No name · 410 Not Buyer/Seller …", largest first; empty for imports made before the log.
const droppedBreakdown = (byReason: Partial<Record<DropReason, number>>) =>
	(Object.entries(byReason) as [DropReason, number][])
		.filter(([, n]) => n > 0)
		.sort((a, b) => b[1] - a[1])
		.map(([r, n]) => `${fmt(n)} ${DROP_REASON_INFO[r]?.label ?? r}`)
		.join(' · ')

const pct = (n: number, of: number) => (of ? `${(n / of) * 100}%` : '0%')
const share = (n: number, of: number) => (of ? `${Math.round((n / of) * 100)}%` : '—')
</script>

<svelte:head><title>{imp ? `${imp.name} · ` : ''}Owner imports · CRM Beta</title></svelte:head>

<PageHeader title={imp?.name ?? 'Import report'} facts={imp ? `${imp.file} · ${imp.at}` : ''}>
	{#snippet right()}
		<a class="btn ghost" href="/crm/owners/imports"><ArrowLeft size={15} /><span>All imports</span></a>
	{/snippet}
</PageHeader>

<QueryView query={imports} rows={8}>
	{#snippet children()}
		{#if !imp}
			<div class="notice" role="status">
				This import isn't there any more. <a href="/crm/owners/imports">Back to all imports</a>
			</div>
		{:else}
			{@const c = imp.counts}
			<!-- 1. Header -->
			<section class="card head">
				{#if editing}
					<form
						class="edit"
						onsubmit={(e) => {
							e.preventDefault()
							rename(imp)
						}}
					>
						<input aria-label="Import name" maxlength="200" bind:value={draft} disabled={busy} />
						<button type="submit" class="btn sm" disabled={busy || !draft.trim()}>Save</button>
						<button type="button" class="btn ghost sm" onclick={() => (editing = false)}>Cancel</button>
					</form>
				{:else}
					<div class="title">
						<h2>{imp.name}</h2>
						<Chip tone={STATUS[imp.status].tone}>{STATUS[imp.status].label}</Chip>
					</div>
					<div class="acts">
						{#if confirming}
							<span class="small">Delete this import, its saved list and its rows?</span>
							<button type="button" class="btn danger sm" disabled={busy} onclick={() => remove(imp)}>
								{busy ? 'Deleting…' : 'Delete'}
							</button>
							<button type="button" class="btn ghost sm" onclick={() => (confirming = false)}>Cancel</button>
						{:else}
							<button
								type="button"
								class="btn ghost sm"
								onclick={() => {
									draft = imp.name
									editing = true
								}}>Rename</button
							>
							<button type="button" class="btn ghost sm" onclick={() => (confirming = true)}>Delete</button>
						{/if}
					</div>
				{/if}
			</section>

			{#if imp.status === 'failed'}
				<div class="notice" role="alert">This import failed (import #{imp.id}). Fix the file and upload it again.</div>
			{:else if imp.status === 'processing'}
				<div class="notice" role="status">This file is still being imported. Counts appear when it finishes.</div>
			{/if}

			<!-- 2. Row funnel -->
			<section class="sec">
				<h3>What happened to the rows</h3>
				<div class="bar" aria-hidden="true">
					<span class="seg matched" style:width={pct(c.matched, c.rows)}></span>
					<span class="seg merged" style:width={pct(c.merged, c.rows)}></span>
					<span class="seg dropped" style:width={pct(c.dropped, c.rows)}></span>
				</div>
				<div class="grid-4 kpis">
					<div class="kpi"><span class="lbl">Rows in file</span><span class="v">{fmt(c.rows)}</span></div>
					<button
						type="button"
						class={['kpi', showDropped && 'on']}
						aria-pressed={showDropped}
						aria-controls="dropped-rows"
						disabled={!c.dropped}
						onclick={() => (showDropped = !showDropped)}
					>
						<span class="lbl"><i class="dot dropped"></i>Dropped</span><span class="v">{fmt(c.dropped)}</span>
						<span class="n">{droppedBreakdown(c.droppedByReason) || 'No rejected source rows'}</span>
						{#if c.dropped}<span class="n link">{showDropped ? 'Hide rows' : 'Show rows'}</span>{/if}
					</button>
					<div class="kpi">
						<span class="lbl"><i class="dot merged"></i>Duplicates merged</span><span class="v">{fmt(c.merged)}</span>
					</div>
					<div class="kpi">
						<span class="lbl">Source records kept</span><span class="v">{fmt(c.kept)}</span>
						<span class="n">{fmt(c.readyToContact)} ready to contact · {fmt(c.rentalLeads)} rental leads</span>
					</div>
					<button
						type="button"
						class={['kpi', showProperties && 'on']}
						aria-pressed={showProperties}
						aria-controls="property-rows"
						disabled={!c.kept}
						onclick={() => (showProperties = !showProperties)}
					>
						<span class="lbl">Property groups after matching</span><span class="v">{c.properties === null ? '—' : fmt(c.properties)}</span>
						<span class="n">{c.properties === null ? 'Counted from the next rematch' : 'Includes inferred identities and plot links'}</span>
						{#if c.kept}<span class="n link">{showProperties ? 'Hide properties' : 'Show properties'}</span>{/if}
					</button>
					<div class="kpi green">
						<span class="lbl"><i class="dot matched"></i>Property evidence found</span><span class="v">{fmt(c.matched)}</span>
						<span class="n">{c.matchRate === null ? '—' : `${Math.round(c.matchRate * 100)}%`} of kept</span>
						{#if c.salesHistoryRows !== null}
							<span class="n">{fmt(c.salesHistoryRows)} with sales history · {fmt(c.plotOnlyRows ?? 0)} plot only</span>
						{/if}
					</div>
				</div>
				{#if c.sourceProperties}
					{@const source = c.sourceProperties}
					<h3>Identifiers in the original file</h3>
					<div class="grid-4 kpis">
						<div class="kpi">
							<span class="lbl">Source property references</span><span class="v">{fmt(source.source_property_references ?? 0)}</span>
							<span class="n">Unit, then property number, then building code; units scoped by location</span>
						</div>
						<div class="kpi">
							<span class="lbl">Property-number fallback</span><span class="v">{fmt(source.distinct_property_numbers_without_unit ?? 0)}</span>
							<span class="n">Distinct property_number values where unit is empty; included in source references</span>
						</div>
						<div class="kpi">
							<span class="lbl">Distinct unit numbers</span><span class="v">{fmt(source.distinct_unit_numbers ?? 0)}</span>
							<span class="n">{fmt(source.location_scoped_units ?? 0)} building/project + unit combinations</span>
						</div>
						<div class="kpi">
							<span class="lbl">Building numbers when unit is empty</span><span class="v">{fmt(source.distinct_building_numbers_without_unit ?? 0)}</span>
							<span class="n">Distinct source building-number values</span>
						</div>
						<div class="kpi">
							<span class="lbl">Combined identifiers</span><span class="v">{fmt(source.distinct_identifiers ?? 0)}</span>
							<span class="n">{fmt(source.identifiers_in_both_columns ?? 0)} values appear in both columns; counted once</span>
						</div>
						<div class="kpi">
							<span class="lbl">Rows missing both identifiers</span><span class="v">{fmt(source.rows_without_identifiers ?? 0)}</span>
							<span class="n">May still contain land numbers or transaction evidence</span>
						</div>
					</div>
					<p>Counts cover all {fmt(source.total_rows ?? 0)} source rows, including rows without an owner name. Repeated apartment numbers across buildings and inconsistent address labels mean these are source references, not a verified physical-property total.</p>
				{/if}
			</section>

			{#if showDropped}
				<section class="sec" id="dropped-rows">
					<h3>Dropped rows</h3>
					<DroppedRows importId={imp.id} />
				</section>
			{/if}

			{#if showProperties}
				<section class="sec" id="property-rows">
					<h3>Properties and unresolved source records</h3>
					<PropertyRows importId={imp.id} />
				</section>
			{/if}

			<!-- 3. Status breakdown -->
			<section class="sec">
				<h3>Ownership assessment</h3>
				<div class="grid-4 kpis">
					{#each statuses as s (s)}
						<!-- The tile filters the owner rows; the ⓘ beside it explains the status. -->
						<div class="stile">
							<button
								type="button"
								class={['kpi', status === s && 'on']}
								aria-pressed={status === s}
								disabled={imp.listId === null}
								onclick={() => (status = status === s ? null : s)}
							>
								<span class="lbl"><Chip tone={STATUS_INFO[s].tone}>{STATUS_INFO[s].label}</Chip></span>
								<span class="v sm">{fmt(c.byStatus[s])} <small>{share(c.byStatus[s], c.kept)}</small></span>
								<span class="n">{STATUS_INFO[s].meaning}</span>
							</button>
							<span class="stip">
								<InfoTip label="What “{STATUS_INFO[s].label}” means">
									<b>{STATUS_INFO[s].label}</b>
									<span>{STATUS_HELP[s].how}</span>
									<span class="todo">{STATUS_HELP[s].todo}</span>
								</InfoTip>
							</span>
						</div>
					{/each}
				</div>
			</section>

			<!-- 4. Owner rows -->
			<section class="sec">
				<h3>Owner rows</h3>
				{#if imp.listId === null}
					<p class="small muted">No rows were saved for this import.</p>
				{:else}
					{#key imp.listId}
						<ImportRows listId={imp.listId} bind:status />
					{/key}
				{/if}
			</section>
		{/if}
	{/snippet}
</QueryView>

<style>
	.sec + .sec,
	.head + .sec,
	.notice + .sec {
		margin-top: 1.5rem;
	}
	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.75rem;
		padding: 0.9rem 1rem;
		margin-bottom: 1rem;
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.title h2 {
		margin: 0;
		font-size: 18px;
	}
	.acts,
	.edit {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.edit {
		flex: 1;
	}
	.edit input {
		flex: 1;
		min-width: 12rem;
	}
	.bar {
		display: flex;
		height: 12px;
		border-radius: 6px;
		overflow: hidden;
		background: var(--card-2);
		margin-bottom: 0.75rem;
	}
	.seg.matched,
	.dot.matched {
		background: var(--green);
	}
	.seg.merged,
	.dot.merged {
		background: var(--text2);
	}
	.seg.dropped,
	.dot.dropped {
		background: var(--amber);
	}
	.dot {
		display: inline-block;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		margin-right: 6px;
	}
	.kpi.on {
		border-color: var(--accent);
		box-shadow: 0 0 0 1px var(--accent);
	}
	.kpi .link {
		color: var(--accent);
	}
	.kpi small {
		font-size: 12.5px;
		font-weight: 400;
		color: var(--text2);
	}
	.stile {
		position: relative;
		display: grid;
	}
	.stile > .kpi {
		width: 100%;
	}
	.stip {
		position: absolute;
		top: 10px;
		right: 10px;
	}
	.todo {
		color: var(--text2);
		font-size: 12px;
	}
</style>
