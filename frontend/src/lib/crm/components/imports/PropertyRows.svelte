<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ChevronDown from 'lucide-svelte/icons/chevron-down'
import Search from 'lucide-svelte/icons/search'
import { aed, fmt, fullDate } from '../../format'
import { clampOffset, STATUS_INFO } from '../../ownerImportReport'
import { crmQueries } from '../../queries'
import Chip from '../ui/Chip.svelte'
import QueryView from '../ui/QueryView.svelte'

/**
 * The distinct properties (building + unit) of one owner file, 100 at a time, searched by
 * building, project, area, unit or owner name. A unit sold several times, with a buyer and a
 * seller row per sale, is one line; opening it lists those rows, newest first, with their sheet
 * and row number so they can be found in the file.
 */
let { importId }: { importId: number } = $props()

let typed = $state('')
let search = $state('')
let offset = $state(0)
// The property whose rows are open, one at a time.
let open = $state<string | null>(null)

$effect(() => {
	const v = typed.trim()
	const t = setTimeout(() => (search = v), 300)
	return () => clearTimeout(t)
})
// Back to the first page whenever the search changes.
$effect.pre(() => {
	void search
	offset = 0
	open = null
})

const list = createQuery(() => crmQueries.ownerImportProperties(importId, { search, offset }))
const loading = $derived(list.isPlaceholderData)
const usable = (value: string | undefined) =>
	value && !['0', 'null', 'none', 'n/a', '-'].includes(value.trim().toLowerCase()) ? value : ''

$effect(() => {
	const d = list.data
	if (!d || loading) return
	const next = clampOffset(offset, d.total, d.limit)
	if (next !== offset) offset = next
})
</script>

<div class="filters">
	<label class="search">
		<Search size={15} />
		<span class="sr-only">Search properties</span>
		<input placeholder="Building, project, area, unit, land number, owner" autocomplete="off" maxlength="160" bind:value={typed} />
	</label>
	{#if list.data}
		<span class="count" aria-live="polite">
			{loading
				? 'Loading…'
				: list.data.total
				? `Showing ${fmt(list.data.offset + 1)}–${fmt(list.data.offset + list.data.rows.length)} of ${fmt(list.data.total)}`
				: 'No properties'}
		</span>
	{/if}
</div>

<QueryView query={list} rows={6}>
	{#snippet children(page)}
		<div class={['card', loading && 'stale']} aria-busy={loading}>
			<div class="tblwrap">
				<table class="tbl">
					<thead>
						<tr>
							<th>Building · unit</th>
							<th>Community · project</th>
							<th>Type · completion</th>
							<th>Current owner</th>
							<th>Latest source event</th>
							<th class="r">Rows</th>
						</tr>
					</thead>
					<tbody>
						{#each page.rows as p (p.key)}
							<tr class={['row', open === p.key && 'opened']}>
								<td>
									<button
										type="button"
										class="toggle"
										aria-expanded={open === p.key}
										aria-controls="prop-{p.key}"
										onclick={() => (open = open === p.key ? null : p.key)}
									>
										<ChevronDown size={14} class={open === p.key ? 'chev up' : 'chev'} />
										<span>
											<span class="nm">{usable(p.building) || usable(p.project) || '—'}{p.unit ? ` · ${p.unit}` : ''}</span>
											{#if !p.unit}<span class="sub">Land {p.records[0]?.identifiers.land_number || 'unknown'} · {p.key.startsWith('dld-land:') ? 'Plot identified; ownership unverified' : 'Property needs resolution'}</span>{/if}
											{#if (usable(p.building) && usable(p.project)) || usable(p.type)}
												<span class="sub">{[usable(p.building) ? usable(p.project) : '', usable(p.type)].filter(Boolean).join(' · ')}</span>
											{/if}
										</span>
									</button>
								</td>
								<td data-label="Community · project">
									{(p.records[0]?.propertyDetails.registry_property_type ? usable(p.area) : p.records[0]?.propertyDetails.file_location || usable(p.area)) || '—'}
									<div class="sub">{usable(p.project) || '—'}</div>
									{#if p.records[0]?.propertyDetails.file_location}<div class="sub">Source location context: {p.records[0].propertyDetails.file_location}</div>{/if}
								</td>
								<td data-label="Type · completion">
									{p.records[0]?.propertyDetails.registry_property_type || p.type || '—'}
									<div class="sub">Source: {p.type || '—'} · {p.records[0]?.propertyDetails.source_completion_status || 'Completion unspecified'}</div>
									{#if p.records[0]?.propertyDetails.registry_completion_status}<div class="sub">Latest sale: {p.records[0].propertyDetails.registry_completion_status}</div>{/if}
								</td>
								<td data-label="Current owner">{p.currentOwner ?? '—'}</td>
								<td data-label="Latest source event" class="nowrap">
									{p.latestDate ? fullDate(p.latestDate) : '—'}
									{#if p.latestPrice}<div class="sub">{aed(p.latestPrice)}</div>{/if}
								</td>
								<td data-label="Rows" class="r nowrap">
									{fmt(p.rows)}
									<div class="sub">{fmt(p.buyers)} buyer{p.buyers === 1 ? '' : 's'} · {fmt(p.sellers)} seller{p.sellers === 1 ? '' : 's'}</div>
								</td>
							</tr>
							{#if open === p.key}
								<tr class="detail" id="prop-{p.key}">
									<td colspan="6">
										<table class="inner">
											<thead>
												<tr>
													<th>Name</th>
													<th>Role · procedure</th>
													<th>Date</th>
													<th class="r">Source amount</th>
													<th>Status</th>
													<th>Source</th>
												</tr>
											</thead>
											<tbody>
												{#each p.records as r (`${r.sheet}:${r.rowNumber}`)}
													<tr>
														<td>{r.name || '—'}</td>
														<td data-label="Role · procedure">{r.role || 'Unspecified'}<div class="sub">{r.procedure || 'Owner snapshot'}</div></td>
														<td data-label="Date" class="nowrap">{r.date ? fullDate(r.date) : '—'}</td>
														<td data-label="Source amount" class="r nowrap">{r.price ? aed(r.price) : '—'}</td>
														<td data-label="Status">
															<Chip tone={STATUS_INFO[r.status]?.tone ?? 'neutral'}>{STATUS_INFO[r.status]?.label ?? r.status}</Chip>
															<div class="sub">{r.matchReason}</div>
														</td>
														<td data-label="Source" class="sub nowrap">{r.sheet} · row {r.rowNumber}</td>
													</tr>
												{/each}
											</tbody>
										</table>
										{#if p.rows > p.records.length}
											<p class="sub more">Showing the newest {fmt(p.records.length)} of {fmt(p.rows)} rows.</p>
										{/if}
									</td>
								</tr>
							{/if}
						{:else}
							<tr><td colspan="6" class="sub">No properties match.</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
		{#if page.total > page.limit}
			<div class="pager">
				<button
					type="button"
					class="btn ghost sm"
					disabled={loading || offset === 0}
					onclick={() => (offset = Math.max(0, offset - page.limit))}
				>
					Previous
				</button>
				<button
					type="button"
					class="btn ghost sm"
					disabled={loading || offset + page.limit >= page.total}
					onclick={() => (offset = offset + page.limit)}
				>
					Next
				</button>
			</div>
		{/if}
	{/snippet}
</QueryView>

<style>
	.toggle {
		display: inline-flex;
		align-items: flex-start;
		gap: 0.4rem;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.toggle > span {
		display: grid;
	}
	.toggle :global(.chev) {
		margin-top: 3px;
		color: var(--text2);
		transition: transform 0.15s;
	}
	.toggle:hover :global(.chev),
	.toggle:focus-visible :global(.chev) {
		color: var(--accent);
	}
	.toggle :global(.chev.up) {
		transform: rotate(180deg);
	}
	.nm {
		font-weight: 600;
	}
	.opened td {
		border-bottom-color: transparent;
	}
	.detail > td {
		background: var(--card-2);
		font-size: 13px;
	}
	.inner {
		width: 100%;
		border-collapse: collapse;
	}
	.inner th {
		text-align: left;
		font-weight: 500;
		color: var(--text2);
		padding: 0.25rem 0.75rem 0.25rem 0;
	}
	.inner td {
		padding: 0.3rem 0.75rem 0.3rem 0;
		border: 0;
	}
	.inner .r {
		text-align: right;
	}
	.more {
		margin: 0.5rem 0 0;
	}
	.stale {
		opacity: 0.55;
		transition: opacity 0.15s;
	}
	.pager {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}
</style>
