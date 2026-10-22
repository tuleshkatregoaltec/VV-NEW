<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Search from 'lucide-svelte/icons/search'
import { aed, fmt, fullDate } from '../../format'
import { type ClaimStatus, clampOffset, STATUS_INFO } from '../../ownerImportReport'
import { crmQueries } from '../../queries'
import { openOwner } from '../../stores/panel.svelte'
import Chip from '../ui/Chip.svelte'
import QueryView from '../ui/QueryView.svelte'

/**
 * The kept rows of one owner file, 100 at a time, filtered by status and searched by name, phone,
 * building or unit. Each row names its sheet and row number so it can be found in the file.
 * Changing the filter or the search goes back to the first page; while the new page loads the old
 * rows are dimmed and the pager waits. The search stops at 160 characters, the backend's limit.
 * A row opens its owner, on the row's unit, with the file's record next to DLD's.
 */
let { listId, status = $bindable(null) }: { listId: number; status?: ClaimStatus | null } = $props()

let typed = $state('')
let search = $state('')
let offset = $state(0)

$effect(() => {
	const v = typed.trim()
	const t = setTimeout(() => (search = v), 300)
	return () => clearTimeout(t)
})
// Back to the first page whenever the filter or the search changes.
$effect.pre(() => {
	void status
	void search
	offset = 0
})

const rows = createQuery(() => crmQueries.ownerImportRows(listId, { status, search, offset }))
const loading = $derived(rows.isPlaceholderData)

// After a refresh with fewer rows, step back to the last page that still has rows.
$effect(() => {
	const d = rows.data
	if (!d || loading) return
	const next = clampOffset(offset, d.total, d.limit)
	if (next !== offset) offset = next
})
const statuses = Object.keys(STATUS_INFO) as ClaimStatus[]
const usable = (value: string | undefined) =>
	value && !['0', 'null', 'none', 'n/a', '-'].includes(value.trim().toLowerCase()) ? value : ''
</script>

<div class="filters">
	<label class="search">
		<Search size={15} />
		<span class="sr-only">Search rows</span>
		<input placeholder="Name, phone, building, unit" autocomplete="off" maxlength="160" bind:value={typed} />
	</label>
	<label class="fsel">
		<span class="sr-only">Status</span>
		<select bind:value={status}>
			<option value={null}>All statuses</option>
			{#each statuses as s (s)}<option value={s}>{STATUS_INFO[s].label}</option>{/each}
		</select>
	</label>
	{#if rows.data}
		<span class="count" aria-live="polite">
			{loading
				? 'Loading…'
				: rows.data.total
				? `Showing ${fmt(rows.data.offset + 1)}–${fmt(rows.data.offset + rows.data.rows.length)} of ${fmt(rows.data.total)}`
				: 'No rows'}
		</span>
	{/if}
</div>

<QueryView query={rows} rows={6}>
	{#snippet children(page)}
		<div class={['card', loading && 'stale']} aria-busy={loading}>
			<div class="tblwrap">
				<table class="tbl">
					<thead>
						<tr>
							<th>Owner</th>
							<th>Building · unit</th>
							<th>Community · project</th>
							<th>Type · completion</th>
							<th>Source date</th>
							<th class="r">Source amount</th>
							<th>Latest registered sale</th>
							<th>Status</th>
							<th>Why</th>
							<th>Source</th>
						</tr>
					</thead>
					<tbody>
						{#each page.rows as r (r.id)}
							<tr class="row">
								<td>
									<button type="button" class="rowlink nm" onclick={() => openOwner(r.ownerKey, r.unit)}>
										{r.name}
									</button>
									<div class="sub mono">{r.phone || '—'}</div>
								</td>
								<td data-label="Building · unit">
									{usable(r.building) || '—'}{r.unit ? ` · ${r.unit}` : ''}
									{#if usable(r.propertyDetails.source_land_number)}<div class="sub">Source land: {r.propertyDetails.source_land_number}</div>{/if}
								</td>
								<td data-label="Community · project">
									{(r.latestSaleDate ? usable(r.area) : r.propertyDetails.file_location || usable(r.area)) || '—'}
									{#if r.propertyDetails.file_location}<div class="sub">Source location context: {r.propertyDetails.file_location}</div>{/if}
									<div class="sub">{usable(r.project) || 'No project identified'}</div>
								</td>
								<td data-label="Type · completion">
									{r.propertyDetails.registry_property_type || usable(r.propertyType) || '—'}
									{#if r.propertyDetails.registry_property_type}<div class="sub">Registry type</div>{/if}
									<div class="sub">File: {usable(r.propertyDetails.source_property_type) || '—'} · {usable(r.propertyDetails.source_completion_status) || 'Completion unspecified'}</div>
									{#if r.propertyDetails.registry_completion_status}<div class="sub">Latest sale: {r.propertyDetails.registry_completion_status}</div>{/if}
								</td>
								<td data-label="Date" class="nowrap">{r.date ? fullDate(r.date) : '—'}</td>
								<td data-label="Price" class="r nowrap">{r.price ? aed(r.price) : '—'}</td>
								<td data-label="Latest registered sale" class="nowrap">
									{r.latestSaleDate ? fullDate(r.latestSaleDate) : '—'}
									{#if r.latestSalePrice}<div class="sub">{aed(r.latestSalePrice)}</div>{/if}
								</td>
								<td data-label="Status">
									<Chip tone={STATUS_INFO[r.status].tone}>{STATUS_INFO[r.status].label}</Chip>
									<div class="sub">{r.confidence}% confidence</div>
								</td>
								<td data-label="Why" class="why">
									{r.reason || '—'}
									<details class="sub">
										<summary>Source property details</summary>
										{#each Object.entries(r.propertyDetails).filter(([key, value]) => key.startsWith('source_') && usable(value)) as [key, value] (key)}
											<div>{key.replace('source_', '').replaceAll('_', ' ')}: {value}</div>
										{/each}
										{#if r.propertyDetails.matched_sale_date}<div>Matched acquisition: {fullDate(r.propertyDetails.matched_sale_date)}</div>{/if}
									</details>
								</td>
								<td data-label="Source" class="sub nowrap">{r.sheet} · row {r.rowNumber}</td>
							</tr>
						{:else}
							<tr><td colspan="10" class="sub">No rows match.</td></tr>
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
	.fsel select {
		height: 34px;
		border: 1px solid var(--line-2);
		border-radius: 8px;
		background: var(--card);
		color: var(--text);
		padding: 0 0.6rem;
	}
	.why {
		max-width: 28rem;
		font-size: 12.5px;
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
