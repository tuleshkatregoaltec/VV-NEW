<script lang="ts">
import ChevronRight from 'lucide-svelte/icons/chevron-right'
import { goto } from '$app/navigation'
import { crm } from '../../../api'
import type { LeadDetail, LeadStage, LeadType } from '../../../api/types'
import { GATE_LINE, LEAD_TYPES, OFF_PLAN_FIELDS, stageOf, stagesFor } from '../../../config'
import { aed, fullDate, priceShort } from '../../../format'
import { openDeal, openListing, openOwner, panel } from '../../../stores/panel.svelte'
import { ui } from '../../../stores/ui.svelte'
import { write } from '../../../write'
import Chain, { type ChainPart } from '../../ui/Chain.svelte'
import Chip from '../../ui/Chip.svelte'
import Kv from '../../ui/Kv.svelte'

let { lead }: { lead: LeadDetail } = $props()

let ownOpen = $state(false)
const type = $derived(LEAD_TYPES[lead.lead_type])
/** The stage bar follows the lead type (6 Oct): Viewing for buyers/tenants, Listed for sellers/landlords. */
const stages = $derived(stagesFor(lead.lead_type))
const current = $derived(stages.findIndex((s) => s.id === lead.stage))
const buyerSide = $derived(lead.lead_type === 'buyer' || lead.lead_type === 'tenant')

function openInMatches() {
	const tab = buyerSide ? (lead.off_plan ? 'offplan' : 'ready') : 'sell'
	ui.matchTab = tab
	ui.matchSel[tab] = lead.id
	panel.closeAll()
	void goto('/crm/matches')
}

function setOffPlan(on: boolean) {
	write({
		call: () => crm.leads.patch(lead.id, { off_plan: on }),
		success: on ? `${lead.name} marked off-plan` : `${lead.name} no longer off-plan`
	})
}

function value(key: (typeof type.fields)[number]) {
	const v = lead.fields[key.key]
	if (v === undefined || v === null || v === '' || v === 0) return 'Not stated'
	return key.money ? aed(Number(v)) : String(v)
}

/** A Vitevue lease lead starts undecided; the agent classifies it as a seller or a landlord. */
function classify(to: LeadType) {
	write({
		call: () => crm.leads.patch(lead.id, { lead_type: to }),
		success: `${lead.name} → ${LEAD_TYPES[to].label}`,
		event: 'lead_type_changed'
	})
}

/** A stage opens its short form; back to New needs nothing. */
function pickStage(stage: LeadStage) {
	if (stage === lead.stage) return
	if (stage === 'new') {
		write({
			call: () => crm.leads.moveStage(lead.id, { stage: 'new' }),
			success: `${lead.name} → ${stageOf('new').label}`,
			event: 'lead_stage_changed'
		})
		return
	}
	panel.setTab(`stage:${stage}`)
}

const chain = $derived.by((): ChainPart[] => {
	const dealPart: ChainPart = lead.deal
		? {
				k: 'Deal',
				v: `${lead.deal.id} · ${lead.deal.stage}`,
				onclick: () => lead.deal && openDeal(lead.deal.id)
			}
		: { k: 'Deal', v: 'not yet', none: true }
	const leadPart: ChainPart = { k: 'Lead', v: lead.name, current: true }
	if (!buyerSide) {
		const own = lead.own_listing
		return [
			{ k: 'Source', v: lead.source_detail },
			leadPart,
			own
				? { k: 'Listing', v: `${own.label} · ${own.status}`, onclick: () => openListing(own.id) }
				: { k: 'Listing', v: 'not yet', none: true },
			dealPart
		]
	}
	const l = lead.listing
	return [
		l
			? { k: 'Listing', v: l.label, onclick: () => openListing(l.id) }
			: { k: 'Source', v: lead.source_detail },
		leadPart,
		dealPart
	]
})
</script>

{#if lead.gated}
	<div class="note gate">
		<span>{GATE_LINE}. No deal, listing or past transaction with {lead.name} is on file.</span>
		{#if lead.owner_id}
			<button
				type="button"
				class="btn ghost sm"
				onclick={() => lead.owner_id && panel.open({ kind: 'add-record', ownerId: lead.owner_id })}
			>
				Add record
			</button>
		{/if}
	</div>
{/if}

<section class="sec">
	<h3>
		Stage
		<span class="hint">
			{lead.gated ? 'only Lost until a prior deal is on record' : 'click to change'}
		</span>
	</h3>
	<div class="stages" role="group" aria-label="Stage">
		{#each stages as s, i (s.id)}
			<button
				type="button"
				class={['stg', i < current && lead.stage !== 'lost' && 'done', s.id === 'lost' && 'lost']}
				aria-pressed={s.id === lead.stage}
				disabled={lead.gated && s.id !== 'lost' && s.id !== lead.stage}
				onclick={() => pickStage(s.id)}
			>
				{s.label}
			</button>
		{/each}
	</div>
</section>

{#if lead.lease}
	{@const l = lead.lease}
	<section class="sec">
		<h3>
			Vitevue lease lead
			<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'renewal', id: l.id })}>
				Property history
			</button>
		</h3>
		{#if lead.lead_type === 'owner'}
			<div class="form-actions">
				<span class="small muted">Sale or letting?</span>
				<button type="button" class="btn sm" onclick={() => classify('seller')}>Seller</button>
				<button type="button" class="btn sm" onclick={() => classify('landlord')}>Landlord</button>
			</div>
		{/if}
		<div class="kv">
			<Kv k="Unit" v={l.unit} o={[l.area, l.property].filter(Boolean).join(' · ')} />
			<Kv k="Last tenancy">
				{l.lease_start ? `${fullDate(l.lease_start)} – ${fullDate(l.lease_end)}` : fullDate(l.lease_end)}
				<Chip chip={l.end_chip} />
			</Kv>
			<Kv k="Last rent a year" v={l.rent_aed ? aed(l.rent_aed) : ''} />
			<Kv
				k="Last purchase"
				v={l.last_purchase?.price_aed ? aed(l.last_purchase.price_aed) : l.last_purchase ? 'Price not recorded' : 'None on record'}
				o={l.last_purchase ? fullDate(l.last_purchase.date) : ''}
			/>
		</div>
	</section>

	<section class="sec">
		<h3>
			Owner
			<button type="button" class="btn ghost sm" onclick={() => panel.setTab('edit')}>
				{lead.name === l.unit && !lead.phone && !lead.email ? 'Add owner' : 'Edit'}
			</button>
		</h3>
		<div class="kv">
			<Kv k="Name" v={lead.name === l.unit ? '' : lead.name} o={lead.name === l.unit ? 'Not identified yet' : ''} />
			<Kv k="Phone" v={lead.phone} />
			<Kv k="Email" v={lead.email} />
		</div>
	</section>
{/if}

<section class="sec">
	<h3>
		{type.label} details
		<button type="button" class="btn ghost sm" onclick={() => panel.setTab('edit')}>Edit</button>
	</h3>
	<div class="kv">
		{#each type.fields as f (f.key)}
			<Kv k={f.label} v={value(f)} />
		{/each}
		<Kv k="Next action" v={lead.next_action || '—'} />
		<Kv k="Phone" o="normalised · rule"><span class="mono">{lead.phone}</span></Kv>
		<Kv k="Language" v={lead.language} />
	</div>
</section>

{#if lead.lead_type === 'buyer'}
	<section class="sec">
		<h3>
			Off-plan
			<span class="hint"><span class="chip outline" title="Off-plan matching is planned.">Planned</span></span>
		</h3>
		<label class="check">
			<input type="checkbox" checked={lead.off_plan} onchange={(e) => setOffPlan(e.currentTarget.checked)} />
			Off-plan buyer · matched to Launch desk projects
		</label>
		{#if lead.off_plan}
			<div class="kv">
				{#each OFF_PLAN_FIELDS as f (f.key)}
					<Kv k={f.label} v={value(f)} />
				{/each}
			</div>
		{/if}
	</section>
{/if}

<section class="sec">
	<h3>On your agency's file</h3>
	<div class="ow-sum">
		<span>{lead.owner_match.summary}</span>
		{#if lead.owner_match.records.length}
			<button
				type="button"
				class="btn ghost sm"
				aria-expanded={ownOpen}
				aria-controls="owner-match-{lead.id}"
				onclick={() => (ownOpen = !ownOpen)}
			>
				{ownOpen
					? '− Hide'
					: `＋ ${lead.owner_match.records.length} record${lead.owner_match.records.length > 1 ? 's' : ''}`}
			</button>
		{/if}
	</div>
	{#if ownOpen}
		<div class="docs" id="owner-match-{lead.id}">
			{#each lead.owner_match.records as r, i (i)}
				<div class="doc">
					<div>
						<div class="nm">{r.unit}</div>
						<div class="fn">{[r.area, r.status, r.purchase].filter(Boolean).join(' · ')}</div>
					</div>
					<div class="acts">
						{#if r.owner_id}
							<button type="button" class="btn ghost sm" onclick={() => r.owner_id && openOwner(r.owner_id)}>
								Owner
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</section>

{#if buyerSide && lead.listing_card}
	{@const x = lead.listing_card}
	<section class="sec">
		<h3>Enquired on</h3>
		<div class="list boxed">
			<button type="button" class="li" onclick={() => openListing(x.id)}>
				<span class="dot" style:background="var(--teal)"></span>
				<span>
					<div class="t">{x.unit}</div>
					<div class="d">
						{priceShort(x.deal_type, x.price_aed)} · {x.status} · {lead.source_short}{x.scope === 'company'
							? ` · ${x.agent}'s listing`
							: ''}
					</div>
				</span>
				<ChevronRight size={16} class="muted" />
			</button>
		</div>
	</section>
{/if}

{#if lead.pursued.length}
	<section class="sec">
		<h3>
			Pursued matches · {lead.pursued.length}
			<button type="button" class="btn ghost sm" onclick={openInMatches}>Open in Matches</button>
		</h3>
		<div class="list boxed">
			{#each lead.pursued as p (p.key)}
				<div class="li">
					<span class="dot" style:background="var(--accent)"></span>
					<span><div class="t">{p.title}</div><div class="d">Next: {p.next} · {p.at}</div></span>
					<span></span>
				</div>
			{/each}
		</div>
	</section>
{/if}

<section class="sec">
	<h3>Linked records</h3>
	<Chain parts={chain} />
</section>

{#if lead.suggested_agent}
	<div class="notice">
		Suggested agent: <b>{lead.suggested_agent.name}</b> — {lead.suggested_agent.reason}
	</div>
{/if}
{#if lead.source === 'agent_to_agent'}
	<div class="notice">Get Form I on file before sharing unit details.</div>
{/if}
