<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import type { LeadRow } from '../../api/types'
import { priceShort } from '../../format'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { toast } from '../../stores/toast.svelte'
import { write } from '../../write'
import Empty from '../ui/Empty.svelte'
import Panel from '../ui/Panel.svelte'
import ScoreChip from '../ui/ScoreChip.svelte'
import Skeleton from '../ui/Skeleton.svelte'

/** Send a listing to matched buyers/tenants — pick who, drafts go out. */
let { target, id }: { target: 'listing' | 'market'; id: string } = $props()

const listing = createQuery(() => ({ ...crmQueries.listing(id), enabled: target === 'listing' }))
const market = createQuery(() => ({
	...crmQueries.marketListing(id),
	enabled: target === 'market'
}))
const record = $derived(target === 'listing' ? listing.data : market.data)
const leads = $derived<LeadRow[]>(record?.matched ?? [])
const label = $derived(
	record
		? `${record.unit} · ${priceShort(record.deal_type, 'price_aed' in record ? record.price_aed : 0)}`
		: 'Send'
)

let unpicked = $state<Set<number>>(new Set())

function toggle(leadId: number, on: boolean) {
	const next = new Set(unpicked)
	if (on) next.delete(leadId)
	else next.add(leadId)
	unpicked = next
}

async function send() {
	const ids = leads.filter((l) => !unpicked.has(l.id)).map((l) => l.id)
	if (!ids.length) {
		toast.show('Pick at least one', { tone: 'error' })
		return
	}
	const r = await write({
		call: () => (target === 'listing' ? crm.listings.send(id, ids) : crm.market.send(id, ids)),
		success: `${ids.length} draft${ids.length > 1 ? 's' : ''} sent`
	})
	if (r) panel.close()
}
</script>

<Panel kicker="Send" title={label} sub="Drafts go to each lead's thread">
	{#if target === 'market'}
		<div class="notice">Form I first — another agency's listing.</div>
	{/if}
	{#if !record}
		<Skeleton rows={3} />
	{:else if leads.length}
		<fieldset class="list boxed">
			<legend class="sr-only">Who gets it</legend>
			{#each leads as l (l.id)}
				<label class="li">
					<input
						type="checkbox"
						checked={!unpicked.has(l.id)}
						onchange={(e) => toggle(l.id, e.currentTarget.checked)}
					/>
					<span>
						<div class="t">{l.name}</div>
						<div class="d">{l.fields.wants ?? l.summary.line}</div>
					</span>
					<ScoreChip score={l.score} />
				</label>
			{/each}
		</fieldset>
	{:else}
		<Empty text="No matching leads yet." />
	{/if}
	{#snippet footer()}
		{#if leads.length}<button type="button" class="btn" onclick={send}>Send drafts</button>{/if}
	{/snippet}
</Panel>
