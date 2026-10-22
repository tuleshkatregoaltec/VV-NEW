<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ArrowLeft from 'lucide-svelte/icons/arrow-left'
import Upload from 'lucide-svelte/icons/upload'
import { goto } from '$app/navigation'
import type { OwnerImportDetail } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import InfoTip from '$lib/crm/components/ui/InfoTip.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import QueryView from '$lib/crm/components/ui/QueryView.svelte'
import { fmt } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { panel } from '$lib/crm/stores/panel.svelte'

/**
 * Owner imports: what each uploaded owner file gave — rows kept, matched to DLD, dropped —
 * so a manager can see which files need fixing. ⚠ marks a failed import or a sheet whose mapping
 * means its rows were dropped or can't be tied to a property. Totals count completed imports.
 */
const imports = createQuery(() => crmQueries.ownerImportDetails())

const STATUS = {
	completed: { label: 'Completed', tone: 'green' },
	processing: { label: 'Processing', tone: 'amber' },
	failed: { label: 'Failed', tone: 'red' }
} as const

function totals(list: OwnerImportDetail[]) {
	const done = list.filter((i) => i.status === 'completed')
	const sum = (k: 'rows' | 'kept' | 'matched' | 'dropped' | 'readyToContact') =>
		done.reduce((t, i) => t + i.counts[k], 0)
	return [
		{ label: 'Imports', v: list.length },
		{ label: 'Rows in files', v: sum('rows') },
		{ label: 'Owners kept', v: sum('kept') },
		{ label: 'Matched to DLD', v: sum('matched'), tone: 'green' },
		{ label: 'Dropped', v: sum('dropped') },
		{ label: 'Ready to contact', v: sum('readyToContact') }
	]
}

const rate = (r: number | null) => (r === null ? '—' : `${Math.round(r * 100)}%`)
/** Why an import is flagged: the failure, then each sheet's error-level mapping checks. */
const problems = (i: OwnerImportDetail) => [
	...(i.status === 'failed' ? [{ sheet: '', text: 'The import failed.' }] : []),
	...i.sheets.flatMap((s) =>
		s.checks.filter((c) => c.level === 'error').map((c) => ({ sheet: s.name, text: c.text }))
	)
]
</script>

<svelte:head><title>Owner imports · CRM Beta</title></svelte:head>

<PageHeader title="Owner imports" facts="Each owner file, checked against DLD · rows kept, matched and dropped">
	{#snippet right()}
		<a class="btn ghost" href="/crm/owners"><ArrowLeft size={15} /><span>Owners</span></a>
		<button type="button" class="btn ghost" onclick={() => panel.open({ kind: 'new-import', ownersOnly: true })}>
			<Upload size={15} /><span>Upload</span>
		</button>
	{/snippet}
</PageHeader>

<QueryView query={imports} rows={6}>
	{#snippet children(list)}
		{#if !list.length}
			<div class="card empty-imports">
				<p>No owner files imported yet.</p>
				<button type="button" class="btn" onclick={() => panel.open({ kind: 'new-import', ownersOnly: true })}>
					<Upload size={15} /><span>Upload</span>
				</button>
			</div>
		{:else}
			<div class="grid-4 kpis">
				{#each totals(list) as t (t.label)}
					<div class={['kpi', t.tone]}>
						<span class="lbl">{t.label}</span>
						<span class="v">{fmt(t.v)}</span>
					</div>
				{/each}
			</div>

			<div class="card">
				<div class="tblwrap">
					<table class="tbl">
						<thead>
							<tr>
								<th>Import</th>
								<th>Date</th>
								<th class="r">Rows</th>
								<th class="r">Kept</th>
								<th class="r">Properties</th>
								<th class="r">Matched</th>
								<th class="r">Dropped</th>
								<th class="r">Match rate</th>
								<th>Status</th>
								<th><span class="sr-only">Problems</span></th>
							</tr>
						</thead>
						<tbody>
							{#each list as i (i.id)}
								<tr class="row">
									<td>
										<button type="button" class="rowlink" onclick={() => goto(`/crm/owners/imports/${i.id}`)}>
											{i.name}
										</button>
										<div class="sub">{i.file}</div>
									</td>
									<td data-label="Date" class="nowrap">{i.at}</td>
									<td data-label="Rows" class="r">{fmt(i.counts.rows)}</td>
									<td data-label="Kept" class="r">{fmt(i.counts.kept)}</td>
									<td data-label="Properties" class="r">{i.counts.properties === null ? '—' : fmt(i.counts.properties)}</td>
									<td data-label="Matched" class="r">{fmt(i.counts.matched)}</td>
									<td data-label="Dropped" class="r">{fmt(i.counts.dropped)}</td>
									<td data-label="Match rate" class="r">{rate(i.counts.matchRate)}</td>
									<td data-label="Status"><Chip tone={STATUS[i.status].tone}>{STATUS[i.status].label}</Chip></td>
									<td data-label="Problems">
										{#if i.flagged}
											<InfoTip label="Why {i.name} needs a look" glyph="⚠" tone="amber">
												<b>Needs a look</b>
												<ul class="probs">
													{#each problems(i) as p, n (n)}
														<li>{#if p.sheet}<span class="sheet">{p.sheet}:</span>{/if} {p.text}</li>
													{/each}
												</ul>
											</InfoTip>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/if}
	{/snippet}
</QueryView>

<style>
	.kpis {
		margin-bottom: 1rem;
	}
	.probs {
		margin: 0;
		padding-left: 1.1rem;
		display: grid;
		gap: 0.3rem;
	}
	.probs .sheet {
		font-weight: 600;
	}
	.hint {
		color: var(--text2);
		font-size: 12px;
	}
	.empty-imports {
		display: grid;
		justify-items: start;
		gap: 0.75rem;
		padding: 1.25rem;
	}
</style>
