<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import type { DealDetail } from '../../api/types'
import { crmKeys, crmQueries } from '../../queries'
import type { DealPanelTab } from '../../stores/panel.svelte'
import { toast } from '../../stores/toast.svelte'
import { errorText, patchCache, write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'
import DealCommission from './deal/DealCommission.svelte'
import DealCompliance from './deal/DealCompliance.svelte'
import DealDocuments from './deal/DealDocuments.svelte'
import DealForms from './deal/DealForms.svelte'
import DealOverview from './deal/DealOverview.svelte'

let { id, tab = 'overview' }: { id: string; tab?: DealPanelTab } = $props()

const deal = createQuery(() => ({
	...crmQueries.deal(id),
	// While a screening check runs, poll until the result is back.
	refetchInterval: (q: { state: { data?: DealDetail } }) =>
		q.state.data?.compliance.screening === 'running' ? 600 : false
}))

const TABS = [
	{ id: 'overview', label: 'Overview' },
	{ id: 'documents', label: 'Documents' },
	{ id: 'commission', label: 'Commission' },
	{ id: 'forms', label: 'Forms' },
	{ id: 'compliance', label: 'Compliance' }
]

function move(d: DealDetail, direction: 'next' | 'back') {
	const index = d.stage_index + (direction === 'next' ? 1 : -1)
	const stage = d.stages[index]
	write({
		optimistic: () =>
			patchCache<DealDetail>(crmKeys.deal(d.id), (x) => ({
				...x,
				stage_index: index,
				stage,
				next_stage: x.stages[index + 1] ?? null
			})),
		call: () => crm.deals.move(d.id, direction),
		success: direction === 'next' ? `${d.id} → ${stage}` : `${d.id} back to ${stage}`,
		event: 'deal_stage_changed'
	})
}

async function requestMissing(d: DealDetail) {
	try {
		await crm.deals.requestDocuments(d.id, d.missing)
		toast.show(
			`Request sent to the client for ${d.missing.length} document${d.missing.length > 1 ? 's' : ''}`
		)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}
</script>

{#if deal.data}
	{@const d = deal.data}
	<Panel
		kicker="{d.deal_type === 'sale' ? 'Sale' : 'Rent'} · {d.id}"
		title={d.unit}
		sub="{d.client} · {d.agent}"
		tabs={TABS}
		{tab}
	>
		{#snippet tags()}
			<Chip tone="dark">{d.stage}</Chip>
			<Chip>{d.docs_received}/{d.docs_required} documents</Chip>
			{#if d.approval === 'pending'}
				<Chip tone="amber">Approval pending</Chip>
			{:else}
				<Chip tone="green">Approved</Chip>
			{/if}
		{/snippet}

		{#if tab === 'documents'}
			<DealDocuments deal={d} />
		{:else if tab === 'commission'}
			<DealCommission deal={d} />
		{:else if tab === 'forms'}
			<DealForms deal={d} />
		{:else if tab === 'compliance'}
			<DealCompliance deal={d} />
		{:else}
			<DealOverview deal={d} />
		{/if}

		{#snippet footer()}
			{#if d.next_stage}
				<button type="button" class="btn" onclick={() => move(d, 'next')}>
					Move to {d.next_stage}
				</button>
			{/if}
			{#if d.stage_index > 0}
				<button type="button" class="btn ghost" onclick={() => move(d, 'back')}>Back a stage</button>
			{/if}
			{#if d.missing.length}
				<button type="button" class="btn ghost" onclick={() => requestMissing(d)}>
					Request missing documents
				</button>
			{/if}
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={deal} kicker="Deal" />
{/if}
