<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ChevronDown from 'lucide-svelte/icons/chevron-down'
import Search from 'lucide-svelte/icons/search'
import { fmt } from '../../format'
import {
	clampOffset,
	DROP_REASON_INFO,
	DROP_STAGE_LABEL,
	type DropReason,
	dropDetail
} from '../../ownerImportReport'
import { crmQueries } from '../../queries'
import Chip from '../ui/Chip.svelte'
import QueryView from '../ui/QueryView.svelte'

/**
 * The rows of one owner file that were not kept, 100 at a time, with the file's own values and
 * why each was dropped. Filtered by reason (with a count per reason) and searched by name,
 * building or unit; each row names its sheet and row number so it can be found in the file.
 * A row opens its full reason: what happened, the rule, whether it was dropped before or after
 * DLD matching, and what would have kept it. Imports made before the dropped-rows log existed
 * say so instead of showing an empty table.
 */
let { importId }: { importId: number } = $props()

let typed = $state('')
let search = $state('')
let reason = $state<DropReason | null>(null)
let offset = $state(0)
// The row whose full reason is open (sheet:row), one at a time.
let open = $state<string | null>(null)

$effect(() => {
	const v = typed.trim()
	const t = setTimeout(() => (search = v), 300)
	return () => clearTimeout(t)
})
// Back to the first page whenever the filter or the search changes.
$effect.pre(() => {
	void reason
	void search
	offset = 0
	open = null
})

const rows = createQuery(() => crmQueries.ownerImportDropped(importId, { reason, search, offset }))
const loading = $derived(rows.isPlaceholderData)

$effect(() => {
	const d = rows.data
	if (!d || loading) return
	const next = clampOffset(offset, d.total, d.limit)
	if (next !== offset) offset = next
})
const reasons = Object.keys(DROP_REASON_INFO) as DropReason[]
</script>

<QueryView query={rows} rows={6}>
	{#snippet children(page)}
		{#if !page.available}
			<p class="small muted">
				This file was imported before dropped rows were recorded. Upload it again to see which rows
				were dropped and why.
			</p>
		{:else}
			<div class="filters">
				<label class="search">
					<Search size={15} />
					<span class="sr-only">Search dropped rows</span>
					<input placeholder="Name, building, unit" autocomplete="off" maxlength="160" bind:value={typed} />
				</label>
				<label class="fsel">
					<span class="sr-only">Reason</span>
					<select bind:value={reason}>
						<option value={null}>All reasons</option>
						{#each reasons as r (r)}
							<option value={r}>{DROP_REASON_INFO[r].label} ({fmt(page.byReason[r] ?? 0)})</option>
						{/each}
					</select>
				</label>
				<span class="count" aria-live="polite">
					{loading
						? 'Loading…'
						: page.total
						? `Showing ${fmt(page.offset + 1)}–${fmt(page.offset + page.rows.length)} of ${fmt(page.total)}`
						: 'No rows'}
				</span>
			</div>
			<div class={['card', loading && 'stale']} aria-busy={loading}>
				<div class="tblwrap">
					<table class="tbl">
						<thead>
							<tr>
								<th>Name</th>
								<th>Building · unit</th>
								<th>Date</th>
								<th class="r">Price</th>
								<th>Role · procedure</th>
								<th>Why dropped</th>
								<th>Source</th>
							</tr>
						</thead>
						<tbody>
							{#each page.rows as r (`${r.sheet}:${r.rowNumber}`)}
								{@const key = `${r.sheet}:${r.rowNumber}`}
								{@const detail = dropDetail(r)}
								<tr class={['row', open === key && 'opened']}>
									<td>{r.name || '—'}</td>
									<td data-label="Building · unit">{r.building || '—'}{r.unit ? ` · ${r.unit}` : ''}</td>
									<td data-label="Date" class="nowrap">{r.date || '—'}</td>
									<td data-label="Price" class="r nowrap">{r.price || '—'}</td>
									<td data-label="Role · procedure">
										{r.role || '—'}
										{#if r.procedure}<div class="sub">{r.procedure}</div>{/if}
									</td>
									<td data-label="Why dropped" class="why">
										<button
											type="button"
											class="reason"
											aria-expanded={open === key}
											aria-controls="drop-{key}"
											onclick={() => (open = open === key ? null : key)}
										>
											<Chip tone="amber">{DROP_REASON_INFO[r.reason].label}</Chip>
											<span class="sub">{DROP_STAGE_LABEL[detail.stage]}</span>
											<ChevronDown size={14} class={open === key ? 'chev up' : 'chev'} />
										</button>
									</td>
									<td data-label="Source" class="sub nowrap">{r.sheet} · row {r.rowNumber}</td>
								</tr>
								{#if open === key}
									<tr class="detail" id="drop-{key}">
										<td colspan="7">
											<dl>
												<dt>What happened</dt>
												<dd>{detail.what}</dd>
												<dt>When</dt>
												<dd>
													{DROP_STAGE_LABEL[detail.stage]}
													<span class="sub">
														{detail.stage === 'before_match'
															? '— while reading the file; this row was never checked against DLD.'
															: '— DLD was checked first, then the row was dropped.'}
													</span>
												</dd>
												<dt>Rule</dt>
												<dd>{detail.rule}</dd>
												<dt>To keep it</dt>
												<dd>{detail.fix}</dd>
											</dl>
										</td>
									</tr>
								{/if}
							{:else}
								<tr><td colspan="7" class="sub">No rows match.</td></tr>
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
		max-width: 22rem;
		font-size: 12.5px;
	}
	.reason {
		display: inline-flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.reason:hover :global(.chev),
	.reason:focus-visible :global(.chev) {
		color: var(--accent);
	}
	.reason :global(.chev) {
		color: var(--text2);
		transition: transform 0.15s;
	}
	.reason :global(.chev.up) {
		transform: rotate(180deg);
	}
	.opened td {
		border-bottom-color: transparent;
	}
	.detail td {
		background: var(--card-2);
		font-size: 13px;
	}
	.detail dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.35rem 1rem;
		margin: 0;
	}
	.detail dt {
		color: var(--text2);
	}
	.detail dd {
		margin: 0;
	}
	@media (max-width: 640px) {
		.detail dl {
			grid-template-columns: 1fr;
		}
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
