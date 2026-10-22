<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import type { ListingDetail } from '../../api/types'
import { stageOf } from '../../config'
import { addDays, fileSize, fmt, price } from '../../format'
import { crmKeys, crmQueries } from '../../queries'
import { openDeal, openLead, panel } from '../../stores/panel.svelte'
import { patchCache, write } from '../../write'
import Chain, { type ChainPart } from '../ui/Chain.svelte'
import Chip from '../ui/Chip.svelte'
import Kv from '../ui/Kv.svelte'
import LeadItem from '../ui/LeadItem.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'
import StageChip from '../ui/StageChip.svelte'

let { id }: { id: string } = $props()

const listing = createQuery(() => crmQueries.listing(id))
const me = createQuery(() => crmQueries.me())

let permitNumber = $state('')
let permitExpires = $state('')
let input: HTMLInputElement | undefined = $state()
let docTarget = $state('')

$effect(() => {
	if (!permitExpires && me.data) permitExpires = addDays(me.data.today, 91)
})

const statusLabel = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function chainOf(x: ListingDetail): ChainPart[] {
	const from = x.from_lead
	const deal = x.deals[0]
	if (!from) return []
	return [
		{
			k: `${from.lead_type === 'landlord' ? 'Landlord' : 'Seller'} lead`,
			v: from.name,
			onclick: () => openLead(from.id)
		},
		{ k: 'Listing', v: x.unit, current: true },
		{ k: 'Enquiries', v: `${x.leads.length} open`, none: !x.leads.length },
		deal
			? { k: 'Deal', v: `${deal.id} · ${deal.stage}`, onclick: () => openDeal(deal.id) }
			: { k: 'Deal', v: 'not yet', none: true }
	]
}

function addPermit(e: SubmitEvent, renew: boolean) {
	e.preventDefault()
	write({
		call: () => crm.listings.addPermit(id, { number: permitNumber.trim(), expires: permitExpires }),
		success: renew ? 'Permit renewed — ready to publish' : 'Permit added — ready to publish',
		after: () => {
			permitNumber = ''
		}
	})
}

function pickDoc(type: string) {
	docTarget = type
	if (!input) return
	input.value = ''
	input.click()
}

function onPicked(unit: string) {
	const f = input?.files?.[0]
	if (!f || !docTarget) return
	const type = docTarget
	write({ call: () => crm.listings.upload(id, f, type), success: `${type} filed on ${unit}` })
}

function setStatus(x: ListingDetail, action: 'publish' | 'unpublish') {
	const status = action === 'publish' ? 'live' : 'pocket'
	write({
		optimistic: () => patchCache<ListingDetail>(crmKeys.listing(id), (l) => ({ ...l, status })),
		call: () => (action === 'publish' ? crm.listings.publish(id) : crm.listings.unpublish(id)),
		success:
			action === 'publish'
				? `${x.unit} published to Property Finder and Bayut`
				: `${x.unit} taken off the portals`,
		event: action === 'publish' ? 'listing_published' : undefined
	})
}

function relist(x: ListingDetail) {
	write({
		call: () => crm.listings.relist(id),
		success: (r) => r.message ?? `${x.unit} is live again`
	})
}
</script>

{#if listing.data}
	{@const x = listing.data}
	{@const mine = x.scope === 'mine'}
	{@const received = new Map(x.documents.received.map((d) => [d.type, d]))}
	{@const chain = chainOf(x)}
	{@const needsPermit = mine && x.permit_status !== 'valid'}
	{@const renew = x.permit_status === 'expired'}
	<Panel
		kicker="{x.deal_type === 'sale' ? 'Sale' : 'Rent'} · {statusLabel(x.status)}"
		title={x.unit}
		sub="{x.area}{mine ? '' : ` · ${x.agent}'s listing`}"
	>
		{#snippet tags()}
			<Chip chip={x.permit_chip} />
			<Chip>{statusLabel(x.status)}</Chip>
		{/snippet}

		<input
			bind:this={input}
			type="file"
			hidden
			aria-hidden="true"
			tabindex="-1"
			onchange={() => onPicked(x.unit)}
		/>

		<div class="kv">
			<Kv k="Price" v={price(x.deal_type, x.price_aed)} />
			<Kv k="Size" v="{x.bedrooms ? `${x.bedrooms} bed` : 'Studio'} · {fmt(x.size_sqft)} sqft" />
			<Kv k="Portals" v={x.portals} />
			<Kv k="Days live" v={x.status === 'live' ? x.days_live : '—'} />
			<Kv k="Owner" v={x.owner} />
			<Kv k="Listing agent" v={x.agent} />
			<Kv k="Latest comparables" v={x.comparables_note} o="Vitevue transactions" wide />
		</div>

		<section class="sec">
			<h3>Permit and mandate</h3>
			<div class="docs">
				<div class="doc"><span>Trakheesi permit</span><Chip chip={x.permit_chip} /></div>
				<div class="doc"><span>Form A (mandate)</span><Chip chip={x.form_a_status} /></div>
			</div>
		</section>

		{#if needsPermit}
			<form class="form" onsubmit={(e) => addPermit(e, renew)} aria-label={renew ? 'Renew permit' : 'Add permit'}>
				<div class="frow">
					<div class="fld">
						<label for="permit-no">{renew ? 'New permit number' : 'Permit number'}</label>
						<input id="permit-no" placeholder="Trakheesi number" required bind:value={permitNumber} />
					</div>
					<div class="fld">
						<label for="permit-exp">Valid until</label>
						<input id="permit-exp" type="date" required bind:value={permitExpires} />
					</div>
				</div>
				<div><button type="submit" class="btn ghost sm">{renew ? 'Renew permit' : 'Add permit'}</button></div>
			</form>
		{/if}

		{#if chain.length}
			<section class="sec">
				<h3>Linked records</h3>
				<Chain parts={chain} />
			</section>
		{/if}

		<section class="sec">
			<h3>Documents</h3>
			<div class="docs">
				{#each x.documents.required as type (type)}
					{@const doc = received.get(type)}
					{@const note = x.documents.notes[type]}
					<div class="doc">
						<div>
							<div class="nm">{type}</div>
							{#if doc}
								<div class="fn">{doc.file} · {fileSize(doc.size_bytes)} · {doc.uploaded}</div>
							{:else}
								<div class="fn missing">Missing{note ? ` · ${note}` : ''}</div>
							{/if}
						</div>
						<div class="acts">
							{#if doc}
								<Chip tone="green">On file</Chip>
								{#if mine}<button type="button" class="btn ghost sm" onclick={() => pickDoc(type)}>Replace</button>{/if}
							{:else if mine}
								<button type="button" class="btn sm" onclick={() => pickDoc(type)}>Upload</button>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		</section>

		{#if x.lead_stages}
			{@const total = x.lead_stages.reduce((s, r) => s + r.count, 0)}
			<section class="sec">
				<h3>Leads on this listing · {total}</h3>
				{#if total}
					<div class="docs">
						{#each x.lead_stages as r (r.stage)}
							<div class="doc"><span><StageChip stage={r.stage} /></span><span class="num">{r.count}</span></div>
						{/each}
					</div>
				{:else}
					<div class="small muted">No open leads.</div>
				{/if}
				<div class="small muted">{x.agent}'s leads: how many and their stage, not names or details.</div>
			</section>
		{:else}
			<section class="sec">
				<h3>Leads on this listing · {x.leads.length} open</h3>
				{#if x.leads.length}
					<div class="list boxed">
						{#each x.leads as l (l.id)}
							<LeadItem lead={l} detail={stageOf(l.stage).label} />
						{/each}
					</div>
				{:else}
					<div class="small muted">None open · {x.enquiries} enquiries since it went live.</div>
				{/if}
				{#if x.deals.length}
					<div class="list boxed">
						{#each x.deals as d (d.id)}
							<button type="button" class="li" onclick={() => openDeal(d.id)}>
								<span class="mono small">{d.id}</span>
								<span><div class="t">{d.client}</div><div class="d">{d.stage}</div></span>
								<Chip tone="dark">Deal</Chip>
							</button>
						{/each}
					</div>
				{/if}
			</section>
		{/if}

		<section class="sec">
			<h3>{mine ? 'Matched buyers and tenants' : 'My buyers and tenants who fit'} · {x.matched.length}</h3>
			{#if x.matched.length}
				<div class="list boxed">
					{#each x.matched as l (l.id)}
						<LeadItem lead={l} />
					{/each}
				</div>
			{:else}
				<div class="small muted">None in {x.area} right now.</div>
			{/if}
		</section>

		{#snippet footer()}
			{#if x.status === 'pocket' && mine}
				<button type="button" class="btn" disabled={!x.can_publish} onclick={() => setStatus(x, 'publish')}>
					Publish to portals
				</button>
				{#if !x.can_publish}<span class="small muted">{renew ? 'Renew the permit first' : 'Add a permit first'}</span>{/if}
			{:else if x.status === 'live'}
				<button type="button" class="btn" onclick={() => panel.open({ kind: 'brochure', id })}>
					Brochure PDF
				</button>
				<button
					type="button"
					class="btn ghost"
					onclick={() => panel.open({ kind: 'send', target: 'listing', id })}
				>
					Send to matched buyers
				</button>
				{#if mine}
					<button type="button" class="btn ghost" onclick={() => setStatus(x, 'unpublish')}>
						Take off portals
					</button>
				{/if}
			{:else if x.status === 'expired' && mine}
				<button type="button" class="btn ghost" onclick={() => relist(x)}>Relist</button>
			{:else if !mine}
				<span class="small muted">Ask {x.agent} before sharing it.</span>
			{/if}
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={listing} kicker="Listing" />
{/if}
