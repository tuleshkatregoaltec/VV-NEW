<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import type { DealRow, DealTab } from '$lib/crm/api/types'
import ConveyancingView from '$lib/crm/components/deals/ConveyancingView.svelte'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import { INVOICE } from '$lib/crm/config'
import { dayLabel, fmt, short } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { openDeal, panel } from '$lib/crm/stores/panel.svelte'
import { ui } from '$lib/crm/stores/ui.svelte'
import { errorText } from '$lib/crm/write'

const me = createQuery(() => crmQueries.me())
const deals = createQuery(() => ({
	...crmQueries.deals(ui.dealTab),
	// The conveyancing view loads its own list (with the All / Sale / Rent filter).
	enabled: ui.dealTab !== 'conveyancing'
}))
// Counts for the header and tabs come from the pipeline list in every view.
const summary = createQuery(() => crmQueries.deals('pipeline'))
const d = $derived(deals.data)
const c = $derived(summary.data)

const tabs = $derived([
	{ id: 'conveyancing' as DealTab, label: 'Conveyancing', count: c?.counts.open },
	{ id: 'pipeline' as DealTab, label: 'All deals' },
	{
		id: 'missing_documents' as DealTab,
		label: 'Missing documents',
		count: c?.counts.missing_documents
	},
	{ id: 'commission' as DealTab, label: 'Commission' },
	{ id: 'closed' as DealTab, label: 'Closed', count: c?.counts.closed }
])

const value = (x: DealRow) =>
	x.deal_type === 'rent' ? `${fmt(x.value_aed)}/yr` : short(x.value_aed)
</script>

<svelte:head><title>Deals · CRM Beta</title></svelte:head>

<PageHeader
	title="Deals"
	facts={c ? `${c.counts.open} in progress · ${short(c.open_value_aed)} in value` : ''}
/>

<Tabs label="Deals" items={tabs} value={ui.dealTab} onselect={(t) => (ui.dealTab = t)} />

{#if ui.dealTab === 'conveyancing'}
	<ConveyancingView />
{:else if !d}
	<div class="card">
		{#if deals.isError}
			<ErrorLine message={errorText(deals.error)} onretry={() => deals.refetch()} />
		{:else}
			<Skeleton rows={5} />
		{/if}
	</div>
{:else}
	{#if deals.isError}
		<ErrorLine bar message={errorText(deals.error)} onretry={() => deals.refetch()} />
	{/if}
	<div class="card">
		<div class="tblwrap">
			{#if ui.dealTab === 'pipeline'}
				<table class="tbl">
					<thead>
						<tr><th>Deal</th><th>Client</th><th>Type</th><th class="r">Value</th><th>Stage</th><th>Documents</th><th>Next</th></tr>
					</thead>
					<tbody>
						{#each d.items as x (x.id)}
							<tr class="row">
								<td>
									<button type="button" class="rowlink" onclick={() => openDeal(x.id)}>{x.unit}</button>
									<div class="sub mono">{x.id}</div>
								</td>
								<td data-label="Client">{x.client}</td>
								<td data-label="Type">{x.deal_type === 'sale' ? 'Sale' : 'Rent'}</td>
								<td class="r num" data-label="Value">{value(x)}</td>
								<td data-label="Stage">
									<div class="grid gap-1">
										<Chip>{x.stage}</Chip>
										<span class="cvbar stagebar" aria-hidden="true">
											<i style:width="{Math.round(((x.stage_index + 1) / x.stages.length) * 100)}%"></i>
										</span>
									</div>
								</td>
								<td data-label="Documents">{x.docs_received}/{x.docs_required}</td>
								<td class="sub" data-label="Next">{x.next_step}</td>
							</tr>
						{:else}
							<tr><td colspan="7">
								<Empty text="No open deals. Move a qualified lead to a deal.">
									<button type="button" class="btn sm" onclick={() => panel.open({ kind: 'new-deal' })}>＋ Deal</button>
								</Empty>
							</td></tr>
						{/each}
					</tbody>
				</table>
			{:else if ui.dealTab === 'missing_documents'}
				<table class="tbl">
					<thead><tr><th>Deal</th><th>Missing</th><th>Due</th><th></th></tr></thead>
					<tbody>
						{#each d.items as x (x.id)}
							<tr class="row">
								<td>
									<button type="button" class="rowlink" onclick={() => openDeal(x.id, 'documents')}>{x.unit}</button>
									<div class="sub">{x.client} · {x.id}</div>
								</td>
								<td data-label="Missing">
									<div class="flex flex-wrap justify-end gap-1 md:justify-start">
										{#each x.missing as m (m)}<Chip tone="red">{m}</Chip>{/each}
									</div>
								</td>
								<td class="sub" data-label="Due">{dayLabel(x.due_date)}</td>
								<td class="r" data-label="">
									<button type="button" class="btn ghost sm" onclick={() => openDeal(x.id, 'documents')}>Upload</button>
								</td>
							</tr>
						{:else}
							<tr><td colspan="4"><Empty text="Every deal has its documents." /></td></tr>
						{/each}
					</tbody>
				</table>
			{:else if ui.dealTab === 'commission'}
				<table class="tbl">
					<thead>
						<tr>
							<th>Deal</th><th class="r">Value</th><th class="r">Rate</th><th class="r">Gross</th><th class="r">Co-broker</th>
							<th class="r">Referral</th><th class="r">Mine</th><th class="r">Agency</th><th class="r">VAT</th><th>Invoice</th>
						</tr>
					</thead>
					<tbody>
						{#each d.items as x (x.id)}
							{@const a = x.commission.amounts}
							<tr class="row">
								<td>
									<button type="button" class="rowlink" onclick={() => openDeal(x.id, 'commission')}>{x.unit}</button>
									<div class="sub">{x.id}</div>
								</td>
								<td class="r num" data-label="Value">{x.deal_type === 'rent' ? fmt(x.value_aed) : short(x.value_aed)}</td>
								<td class="r" data-label="Rate">{x.commission.rate_pct}%</td>
								<td class="r num" data-label="Gross">{fmt(a.gross)}</td>
								<td class="r num" data-label="Co-broker">{a.cobroker ? `−${fmt(a.cobroker)}` : '—'}</td>
								<td class="r num" data-label="Referral">{a.referral ? `−${fmt(a.referral)}` : '—'}</td>
								<td class="r num" data-label="Mine">{fmt(a.agent)}</td>
								<td class="r num" data-label="Agency">{fmt(a.agency)}</td>
								<td class="r num" data-label="VAT">{fmt(a.vat)}</td>
								<td data-label="Invoice">
									<Chip tone={INVOICE[x.commission.invoice_status].tone}>{INVOICE[x.commission.invoice_status].label}</Chip>
								</td>
							</tr>
						{:else}
							<tr><td colspan="10"><Empty text="No deals yet." /></td></tr>
						{/each}
						{#if d.items.length}
							<tr class="tot">
								<td><b>Total</b></td><td></td><td></td>
								<td class="r num" data-label="Gross"><b>{fmt(d.totals.gross)}</b></td><td></td><td></td>
								<td class="r num" data-label="Mine"><b>{fmt(d.totals.agent)}</b></td>
								<td class="r num" data-label="Agency"><b>{fmt(d.totals.agency)}</b></td><td></td><td></td>
							</tr>
						{/if}
					</tbody>
				</table>
			{:else}
				<table class="tbl">
					<thead><tr><th>Deal</th><th>Client</th><th class="r">Value</th><th>DLD transaction</th><th>Closed</th></tr></thead>
					<tbody>
						{#each d.items as x (x.id)}
							<tr class="row">
								<td>
									<button type="button" class="rowlink" onclick={() => openDeal(x.id)}>{x.unit}</button>
									<div class="sub">{x.id}</div>
								</td>
								<td data-label="Client">{x.client}</td>
								<td class="r num" data-label="Value">{value(x)}</td>
								<td class="mono small" data-label="DLD transaction">{x.dld_transaction_id ?? '—'}</td>
								<td class="sub" data-label="Closed">{dayLabel(x.due_date)}</td>
							</tr>
						{:else}
							<tr><td colspan="5"><Empty text="No closed deals yet." /></td></tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	</div>
{/if}
