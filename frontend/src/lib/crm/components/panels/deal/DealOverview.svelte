<script lang="ts">
import Check from 'lucide-svelte/icons/check'
import { crm } from '../../../api'
import type { DealDetail } from '../../../api/types'
import { dayLabel, price } from '../../../format'
import { openLead, openListing } from '../../../stores/panel.svelte'
import { write } from '../../../write'
import Chain, { type ChainPart } from '../../ui/Chain.svelte'
import Chip from '../../ui/Chip.svelte'
import Kv from '../../ui/Kv.svelte'

let { deal }: { deal: DealDetail } = $props()

let dld = $state('')
let editing = $state(false)

const chain = $derived.by((): ChainPart[] => {
	const listing = deal.listing
	const lead = deal.lead
	return [
		listing
			? { k: 'Listing', v: listing.label, onclick: () => openListing(listing.id) }
			: { k: 'Listing', v: 'off-market', none: true },
		lead
			? { k: 'Lead', v: lead.label, onclick: () => openLead(Number(lead.id)) }
			: { k: 'Client', v: deal.client },
		{ k: 'Deal', v: deal.id, current: true },
		{ k: 'Portfolio', v: deal.closed ? 'added' : 'when transferred', none: !deal.closed }
	]
})

function saveDld(e: SubmitEvent) {
	e.preventDefault()
	const v = dld.trim()
	if (!v) return
	write({
		call: () => crm.deals.patch(deal.id, { dld_transaction_id: v }),
		success: `Linked to ${v}`,
		after: () => {
			dld = ''
			editing = false
		}
	})
}

function edit() {
	dld = deal.dld_transaction_id ?? ''
	editing = true
}
</script>

<section class="sec">
	<h3>Linked records</h3>
	<Chain parts={chain} />
</section>

<section class="sec">
	<h3>Conveyancing</h3>
	<ol class="timeline">
		{#each deal.stages as s, i (s)}
			<li class={[i < deal.stage_index && 'done', i === deal.stage_index && 'now']} aria-current={i === deal.stage_index ? 'step' : undefined}>
				<span class="ck" aria-hidden="true">{#if i < deal.stage_index}<Check size={11} strokeWidth={3} />{/if}</span>
				<span>{s}</span>
				<span class="small muted">
					{i < deal.stage_index ? 'done' : i === deal.stage_index ? deal.next_step : ''}
				</span>
			</li>
		{/each}
	</ol>
</section>

<div class="kv">
	<Kv k={deal.deal_type === 'sale' ? 'Buyer' : 'Tenant'} v={deal.client} />
	<Kv k={deal.deal_type === 'sale' ? 'Seller' : 'Landlord'} v={deal.counterparty} />
	<Kv k="Value" v={price(deal.deal_type, deal.value_aed)} />
	<Kv k="Due" v="{dayLabel(deal.due_date)} · {deal.next_step}" />
	<Kv k="Agent" v={deal.agent} />
	<Kv k="Approval">
		{#if deal.approval === 'pending'}
			<Chip tone="amber">Waiting for manager</Chip>
		{:else}
			<Chip tone="green">Approved</Chip>
		{/if}
	</Kv>
	<Kv k="DLD transaction" wide>
		{#if deal.closed && (editing || !deal.dld_transaction_id)}
			<form class="flex gap-2" onsubmit={saveDld}>
				<label class="sr-only" for="dld-{deal.id}">DLD transaction number</label>
				<input id="dld-{deal.id}" class="flex-1" placeholder="DLD-2026-…" bind:value={dld} />
				<button type="submit" class="btn sm" disabled={!dld.trim() || dld.trim() === deal.dld_transaction_id}>Save</button>
				{#if editing}<button type="button" class="btn ghost sm" onclick={() => (editing = false)}>Cancel</button>{/if}
			</form>
		{:else if deal.dld_transaction_id}
			<span class="flex flex-wrap items-center gap-2">
				<span class="mono">{deal.dld_transaction_id}</span>
				<button type="button" class="btn ghost sm" onclick={edit}>Edit</button>
			</span>
		{:else}
			Linked when transferred
		{/if}
	</Kv>
</div>

{#if deal.commission.cobroker_name}
	<div class="notice">
		Co-broker: {deal.commission.cobroker_name} · {deal.commission.cobroker_pct}% of gross.
	</div>
{/if}
