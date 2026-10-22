<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ChartBar from 'lucide-svelte/icons/chart-no-axes-column'
import Search from 'lucide-svelte/icons/search'
import Upload from 'lucide-svelte/icons/upload'
import type { OwnerListParams, OwnerSource, OwnerTab, PropertyKind } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import FilterMenu from '$lib/crm/components/ui/FilterMenu.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import QueryView from '$lib/crm/components/ui/QueryView.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import { OWNER_PTYPE_OPTIONS, OWNER_SOURCES, stageOf } from '$lib/crm/config'
import { fmt } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { openOwner, panel } from '$lib/crm/stores/panel.svelte'
import { clearOwnerFilters, ui } from '$lib/crm/stores/ui.svelte'
import { errorText } from '$lib/crm/write'

// Search updates the rows as you type; the input keeps focus.
let q = $state(ui.ownerQ)
$effect(() => {
	const v = ui.ownerQ.trim()
	const t = setTimeout(() => (q = v), 150)
	return () => clearTimeout(t)
})

// The whole owner file, 50–500 owners a page. A new tab, search, filter or page size goes back to
// the first page.
const PAGE_SIZES = [50, 100, 200, 500]
let offset = $state(0)
$effect.pre(() => {
	void [ui.ownerTab, q, ui.ownerSource, ui.ownerArea, ui.ownerPtype, ui.ownerPageSize]
	offset = 0
})

const params = $derived<OwnerListParams>({
	tab: ui.ownerTab,
	q,
	source: ui.ownerSource,
	area: ui.ownerArea,
	ptype: ui.ownerPtype,
	offset,
	limit: ui.ownerPageSize
})
const owners = createQuery(() => crmQueries.owners(params))
const imports = createQuery(() => crmQueries.imports())
const o = $derived(owners.data)
const loading = $derived(owners.isPlaceholderData)
/** "Showing 501–1,000 of 73,224" for the page in view. */
const pageText = $derived(
	o
		? o.page.total
			? `Showing ${fmt(o.page.offset + 1)}–${fmt(o.page.offset + o.page.loaded)} of ${fmt(o.page.total)} owners`
			: 'No owners'
		: ''
)

function goPage(next: number) {
	offset = next
	document.querySelector('.owners-table')?.scrollIntoView({ block: 'start' })
}
const filtered = $derived(Boolean(ui.ownerQ || ui.ownerSource || ui.ownerArea || ui.ownerPtype))
const waiting = $derived(imports.data?.filter((r) => r.status === 'Waiting').length ?? 0)

/** v2 tabs (6 Oct, 11:20): All · 3+ units · Next event ≤ 90 days · Just sold · Update due (My contacts removed). */
const tabs = $derived(
	(
		[
			['all', 'All'],
			['multi', '3+ units'],
			['event', 'Next event ≤ 90 days'],
			// "Former owners" (was "Just sold"): every owner the file or DLD shows as a seller, of any date.
			// "Update due" is hidden until market updates exist.
			['sold', 'Former owners']
		] as [OwnerTab, string][]
	).map(([id, label]) => ({
		id,
		label,
		count: o?.counts[id],
		hint: o && o.page.total > o.page.limit && o.pageOnlyTabs.includes(id) ? 'this page' : undefined
	}))
)

const facts = $derived(o ? `Agency file · ${o.on_file} owners` : '')
</script>

<svelte:head><title>Owners · CRM Beta</title></svelte:head>

<PageHeader title="Owners" {facts}>
	{#snippet right()}
		<a class="btn ghost" href="/crm/owners/imports"><ChartBar size={15} /><span>Imports</span></a>
		<button type="button" class="btn ghost" onclick={() => panel.open({ kind: 'new-import', ownersOnly: true })}>
			<Upload size={15} /><span>Upload</span>
		</button>
	{/snippet}
</PageHeader>

<Tabs label="Owners" items={tabs} value={ui.ownerTab} onselect={(t) => (ui.ownerTab = t)} />

<div class="filters">
	<label class="search">
		<Search size={15} />
		<span class="sr-only">Search owners</span>
		<input placeholder="Owner, unit, building" autocomplete="off" bind:value={ui.ownerQ} />
	</label>
	<!-- Every owner comes from an imported file today; "Own contact" and "DLD/Ejari event" have no data yet. -->
	<FilterMenu
		label="Source"
		allLabel="All sources"
		value={ui.ownerSource}
		options={OWNER_SOURCES.filter((s) => s.id === 'agency_import').map((s) => ({ id: s.id as OwnerSource, label: s.label }))}
		onselect={(v) => (ui.ownerSource = v)}
	/>
	<FilterMenu
		label="Type"
		allLabel="Any type"
		value={ui.ownerPtype}
		options={OWNER_PTYPE_OPTIONS}
		onselect={(v) => (ui.ownerPtype = v)}
	/>
	<FilterMenu
		label="Area"
		allLabel="All areas"
		value={ui.ownerArea}
		options={(o?.areas ?? []).map((a) => ({ id: a, label: a }))}
		onselect={(v) => (ui.ownerArea = v)}
	/>
	{#if filtered}
		<button type="button" class="btn plain sm" onclick={clearOwnerFilters}>Clear</button>
	{/if}
	<span class="count" aria-live="polite">{loading ? 'Loading…' : pageText}</span>
	<label class="psize">
		<span>Rows per page</span>
		<select bind:value={ui.ownerPageSize}>
			{#each PAGE_SIZES as n (n)}<option value={n}>{n}</option>{/each}
		</select>
	</label>
</div>

{#if owners.isError && o}
	<ErrorLine bar message={errorText(owners.error)} onretry={() => owners.refetch()} />
{/if}

<div class={['card owners-table', loading && 'stale']} aria-busy={loading}>
	{#if o}
		<div class="tblwrap">
			<table class="tbl">
				<thead>
					<tr><th>Owner</th><th>Units</th><th>Next event</th><th>Last contact</th><th>Record</th><th>Source</th></tr>
				</thead>
				<tbody>
					{#each o.items as x (x.id)}
						<tr class="row">
							<td>
								<button type="button" class="rowlink" onclick={() => openOwner(x.id)}>{x.name}</button>
								{#if x.tag}<Chip>{x.tag}</Chip>{/if}
								{#if x.lead}
									<div class="sub">
										Lead · {stageOf(x.lead.stage).label}{x.lead.id === null ? ` · ${x.lead.agent}` : ''}
									</div>
								{/if}
							</td>
							<td data-label="Units">
								<div>
									<div>{x.units.length}</div>
									<div class="sub">
										{x.units.slice(0, 3).join(' · ')}{x.units.length > 3 ? ` · +${x.units.length - 3}` : ''}
									</div>
								</div>
							</td>
							<td data-label="Next event">
								<div><div>{x.next_event}</div><div class="sub">{x.next_event_label}</div></div>
							</td>
							<td data-label="Last contact">
								<span class="nowrap">
									{x.last_contact}
									{#if x.market_update_due && x.prior_deal}<Chip tone="amber">Update due</Chip>{/if}
								</span>
							</td>
							<td data-label="Record">
								{#if x.prior_deal}<Chip tone="green">Prior deal</Chip>{:else}<span class="sub">None</span>{/if}
							</td>
							<td data-label="Source">
								<span class="row-chips" style:justify-content="flex-start">
									{#each x.sources as s (s)}
										<Chip tone={s === 'dld_event' ? 'blue' : 'neutral'}>
											{OWNER_SOURCES.find((v) => v.id === s)?.label}
										</Chip>
									{/each}
								</span>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="6">
								{#if filtered}
									<Empty text="No owners match.">
										<button type="button" class="btn ghost sm" onclick={clearOwnerFilters}>Clear filters</button>
									</Empty>
								{:else}
									<Empty text="No owners in this view.">
										<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'new-owner' })}>
											New owner
										</button>
									</Empty>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if o.page.total > o.page.limit}
			<div class="pager">
				<span class="small muted">
					{pageText} · the source, type and area filters cover this page
				</span>
				<button
					type="button"
					class="btn ghost sm"
					disabled={loading || offset === 0}
					onclick={() => goPage(Math.max(0, offset - o.page.limit))}
				>
					Previous
				</button>
				<button
					type="button"
					class="btn ghost sm"
					disabled={loading || offset + o.page.limit >= o.page.total}
					onclick={() => goPage(offset + o.page.limit)}
				>
					Next
				</button>
			</div>
		{/if}
	{:else if owners.isError}
		<ErrorLine message={errorText(owners.error)} onretry={() => owners.refetch()} />
	{:else}
		<Skeleton rows={6} />
	{/if}
</div>

<section class="card" aria-labelledby="imports-h">
	<div class="ch">
		<h2 id="imports-h">
			Owner file and import requests
			<span class="facts">Vitevue does not provide or sell owner data · {waiting} waiting for the admin</span>
		</h2>
		<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'new-import', ownersOnly: true })}>
			Import a file
		</button>
	</div>
	<QueryView query={imports} rows={2}>
		{#snippet children(list)}
			{#if list.length}
				<div class="tblwrap">
					<table class="tbl">
						<thead><tr><th>File</th><th>What</th><th>Areas</th><th>Sent</th><th>Status</th></tr></thead>
						<tbody>
							{#each list as r (r.id)}
								<tr>
									<td class="nm mono">{r.file}</td>
									<td data-label="What">{r.kind} · {fmt(r.rows)} rows</td>
									<td class="sub" data-label="Areas">{r.areas}</td>
									<td class="sub" data-label="Sent">{r.who} · {r.at}</td>
									<td data-label="Status"><Chip tone={r.tone}>{r.status}</Chip></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<Empty text="No import requests. Send a CSV of owners or leads for the admin to approve." />
			{/if}
		{/snippet}
	</QueryView>
</section>

<style>
	.owners-table {
		scroll-margin-top: 5rem;
	}
	.stale {
		opacity: 0.55;
		transition: opacity 0.15s;
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding: 0.75rem 1rem;
		border-top: 1px solid var(--sep);
	}
	.psize {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 12.5px;
		color: var(--text2);
	}
	.psize select {
		height: 30px;
		border: 1px solid var(--line-2);
		border-radius: 8px;
		background: var(--card);
		color: var(--text);
		padding: 0 0.4rem;
	}
	.pager .small {
		margin-right: auto;
	}
</style>
