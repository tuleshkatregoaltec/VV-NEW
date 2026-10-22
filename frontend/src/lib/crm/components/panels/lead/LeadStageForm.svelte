<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { untrack } from 'svelte'
import { goto } from '$app/navigation'
import { crm } from '../../../api'
import type {
	LeadDetail,
	LeadFieldKey,
	LeadFields,
	LeadStage,
	LeadStageMove
} from '../../../api/types'
import { CONTACT_OUTCOMES, LEAD_TYPES, LOST_REASONS, stageOf } from '../../../config'
import { addDays, fileSize } from '../../../format'
import { crmQueries } from '../../../queries'
import { panel } from '../../../stores/panel.svelte'
import { ui } from '../../../stores/ui.svelte'
import { write } from '../../../write'
import Chip from '../../ui/Chip.svelte'
import MoneyInput from '../../ui/MoneyInput.svelte'

/**
 * Moving a lead to a stage asks for what that stage needs, then saves (logged, with Undo).
 * Contacted → how it went + next action · Qualified → the type's fields · Viewing (buyer/tenant)
 * → date, time, unit · Listed (seller/landlord) → the pocket listing, linked to the lead · Offer →
 * amount + unit (or from whom) · Won → the deal (buyer/tenant) · Lost → why. The stage's documents
 * can be added here or later.
 */
let { lead, stage }: { lead: LeadDetail; stage: LeadStage } = $props()

const me = createQuery(() => crmQueries.me())
const mine = createQuery(() => crmQueries.listings('mine'))
const company = createQuery(() => crmQueries.listings('company'))

const buyerSide = $derived(lead.lead_type === 'buyer' || lead.lead_type === 'tenant')
const type = $derived(LEAD_TYPES[lead.lead_type])
const target = $derived(stageOf(stage))
const units = $derived(
	[...(mine.data?.items ?? []), ...(company.data?.items ?? [])].filter(
		(x) => x.status !== 'expired' && (!buyerSide || x.deal_type === type.deal)
	)
)
const docs = $derived(lead.stage_documents.find((s) => s.stage === stage)?.docs ?? [])

const start = untrack(() => ({
	money: Number(
		lead.fields.budget_aed || lead.fields.target_price_aed || lead.fields.expected_rent_aed || 0
	),
	listing: lead.listing?.id ?? lead.own_listing?.id ?? '',
	draft: Object.fromEntries(
		Object.entries(lead.fields).map(([k, v]) => [k, v === null || v === undefined ? '' : String(v)])
	) as Record<string, string>,
	next: lead.next_action,
	unit: String(lead.fields.unit ?? ''),
	beds: Number(String(lead.fields.unit ?? '').match(/(\d)\s?BR/i)?.[1] ?? 1)
}))

let outcome = $state(CONTACT_OUTCOMES[0])
let nextAction = $state(start.next)
let draft = $state<Record<string, string>>(start.draft)
let money = $state<Record<string, number | null>>(
	untrack(() =>
		Object.fromEntries(
			type.fields.filter((f) => f.money).map((f) => [f.key, Number(lead.fields[f.key]) || null])
		)
	)
)
let date = $state('')
let time = $state('12:00')
let listingId = $state(start.listing)
let amount = $state<number | null>(start.money || null)
let from = $state('')
let due = $state('')
let why = $state(LOST_REASONS[0])
let note = $state('')
// Listed: the pocket listing (7 Oct: moving a seller or landlord to Listed opens this form).
let lUnit = $state(start.unit)
let lArea = $state(untrack(() => lead.area))
let lBeds = $state(start.beds)
let lSize = $state<number | null>(null)
let saving = $state(false)
let input: HTMLInputElement | undefined = $state()
let docTarget = ''

$effect(() => {
	const today = me.data?.today
	if (!today) return
	if (!date) date = addDays(today, 3)
	if (!due) due = addDays(today, 21)
})

function move(body: LeadStageMove) {
	return write({
		call: () => crm.leads.moveStage(lead.id, body),
		success: `${lead.name} → ${target.label}`,
		event: 'lead_stage_changed'
	})
}

async function submit(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const base = { stage, note: note.trim() || undefined }
	let ok = false
	if (stage === 'contacted') {
		ok = Boolean(await move({ ...base, outcome, next_action: nextAction.trim() }))
	} else if (stage === 'qualified') {
		const fields: LeadFields = {}
		for (const f of type.fields) {
			const k = f.key as LeadFieldKey
			if (f.money) fields[k] = money[k] ?? null
			else fields[k] = String(draft[k] ?? '').trim() || null
		}
		ok = Boolean(await move({ ...base, fields }))
	} else if (stage === 'viewing') {
		ok = Boolean(await move({ ...base, viewing: { date, time, listing_id: listingId || null } }))
	} else if (stage === 'listed') {
		ok = Boolean(
			await move({
				...base,
				listing: lead.own_listing
					? undefined
					: {
							unit: lUnit.trim(),
							area: lArea.trim(),
							deal_type: type.deal,
							price_aed: amount ?? 0,
							bedrooms: lBeds,
							size_sqft: lSize ?? 0,
							status: 'pocket',
							permit_number: '',
							owner: lead.name
						}
			})
		)
	} else if (stage === 'offer') {
		ok = Boolean(
			await move({
				...base,
				offer: buyerSide
					? { amount_aed: amount ?? 0, listing_id: listingId || null }
					: { amount_aed: amount ?? 0, from: from.trim() }
			})
		)
	} else if (stage === 'lost') {
		ok = Boolean(await move({ ...base, lost_reason: why }))
	} else if (stage === 'won' && buyerSide) {
		const r = await write({
			call: () =>
				crm.deals.create({
					lead_id: lead.id,
					listing_id: listingId || null,
					value_aed: amount,
					due_date: due || null
				}),
			success: (res) => `${res.data.id} opened — request the documents next`,
			created: (res) => ({ kind: 'deal', id: res.data.id }),
			event: 'deal_created'
		})
		saving = false
		if (r) {
			ui.dealTab = 'conveyancing'
			ui.cvType = ''
			ui.cvSel = r.data.id
			await goto(`/crm/deals?deal=${r.data.id}&tab=documents`)
		}
		return
	} else if (stage === 'won') {
		const r = await move(base)
		saving = false
		if (r && !lead.own_listing) panel.open({ kind: 'new-listing', fromLeadId: lead.id })
		else if (r) panel.setTab('overview')
		return
	}
	saving = false
	if (ok) panel.setTab('overview')
}

function pickDoc(t: string) {
	docTarget = t
	if (!input) return
	input.value = ''
	input.click()
}

function onPicked() {
	const f = input?.files?.[0]
	if (!f || !docTarget) return
	const t = docTarget
	write({
		call: () => crm.leads.uploadDocument(lead.id, f, t),
		success: `${t} added to ${lead.name}`,
		event: 'document_uploaded'
	})
}
</script>

<form class="form" onsubmit={submit}>
	<div class="notice"><b>{stageOf(lead.stage).label} → {target.label}</b></div>

	{#if stage === 'contacted'}
		<div class="fld">
			<label for="sf-how">How did it go?</label>
			<select id="sf-how" bind:value={outcome}>
				{#each CONTACT_OUTCOMES as o (o)}<option value={o}>{o}</option>{/each}
			</select>
		</div>
		<div class="fld">
			<label for="sf-next">Next action</label>
			<input id="sf-next" required bind:value={nextAction} />
		</div>
	{:else if stage === 'qualified'}
		{#each type.fields as f (f.key)}
			<div class="fld">
				<label for="sf-{f.key}">{f.label}{f.money ? ' (AED)' : ''}</label>
				{#if f.money}
					<MoneyInput id="sf-{f.key}" required bind:value={money[f.key]} />
				{:else if f.options}
					<select id="sf-{f.key}" bind:value={draft[f.key]}>
						<option value="">Not stated</option>
						{#each f.options as o (o)}<option value={o}>{o}</option>{/each}
					</select>
				{:else}
					<input id="sf-{f.key}" bind:value={draft[f.key]} />
				{/if}
			</div>
		{/each}
	{:else if stage === 'viewing'}
		<div class="frow">
			<div class="fld">
				<label for="sf-date">Date</label>
				<input id="sf-date" type="date" required bind:value={date} />
			</div>
			<div class="fld">
				<label for="sf-time">Time</label>
				<input id="sf-time" type="time" required bind:value={time} />
			</div>
		</div>
		<div class="fld">
			<label for="sf-unit">Unit</label>
			<select id="sf-unit" bind:value={listingId}>
				<option value="">To confirm</option>
				{#each units as x (x.id)}<option value={x.id}>{x.unit} · {x.area}</option>{/each}
			</select>
		</div>
	{:else if stage === 'listed'}
		{#if lead.own_listing}
			<div class="small muted">Links {lead.own_listing.id} · {lead.own_listing.label} to this stage.</div>
		{:else}
			<div class="small muted">Pocket listing · linked to {lead.name}. Add a permit later to publish it.</div>
			<div class="frow">
				<div class="fld">
					<label for="sf-lunit">Unit</label>
					<input id="sf-lunit" required placeholder="Building · unit" bind:value={lUnit} />
				</div>
				<div class="fld">
					<label for="sf-larea">Area</label>
					<input id="sf-larea" required bind:value={lArea} />
				</div>
			</div>
			<div class="frow">
				<div class="fld">
					<label for="sf-lprice">{type.deal === 'rent' ? 'Rent a year (AED)' : 'Price (AED)'}</label>
					<MoneyInput id="sf-lprice" required bind:value={amount} />
				</div>
				<div class="fld">
					<label for="sf-lbeds">Bedrooms</label>
					<input id="sf-lbeds" type="number" min="0" max="9" bind:value={lBeds} />
				</div>
			</div>
			<div class="fld">
				<label for="sf-lsize">Size (sqft)</label>
				<MoneyInput id="sf-lsize" bind:value={lSize} />
			</div>
		{/if}
	{:else if stage === 'offer'}
		<div class="frow">
			<div class="fld">
				<label for="sf-amt">{buyerSide ? 'Offer (AED)' : 'Offer received (AED)'}</label>
				<MoneyInput id="sf-amt" required bind:value={amount} />
			</div>
			{#if buyerSide}
				<div class="fld">
					<label for="sf-unit">Unit</label>
					<select id="sf-unit" bind:value={listingId}>
						<option value="">To confirm</option>
						{#each units as x (x.id)}<option value={x.id}>{x.unit}</option>{/each}
					</select>
				</div>
			{:else}
				<div class="fld">
					<label for="sf-from">From</label>
					<input id="sf-from" placeholder="Buyer or their agent" required bind:value={from} />
				</div>
			{/if}
		</div>
	{:else if stage === 'won' && buyerSide}
		<div class="frow">
			<div class="fld">
				<label for="sf-val">Agreed {type.deal === 'rent' ? 'rent a year' : 'price'} (AED)</label>
				<MoneyInput id="sf-val" required bind:value={amount} />
			</div>
			<div class="fld">
				<label for="sf-due">Target date</label>
				<input id="sf-due" type="date" required bind:value={due} />
			</div>
		</div>
		<div class="fld">
			<label for="sf-unit">Unit</label>
			<select id="sf-unit" bind:value={listingId}>
				<option value="">Off-market · to confirm</option>
				{#each units as x (x.id)}<option value={x.id}>{x.unit}</option>{/each}
			</select>
		</div>
	{:else if stage === 'won'}
		<div class="small muted">
			{lead.own_listing
				? 'The listing is signed. The deal is made with the buyer or tenant who takes it.'
				: 'Signed with us. Move to Listed first to create the listing.'}
		</div>
	{:else if stage === 'lost'}
		<div class="fld">
			<label for="sf-why">Why</label>
			<select id="sf-why" bind:value={why}>
				{#each LOST_REASONS as w (w)}<option value={w}>{w}</option>{/each}
			</select>
		</div>
	{/if}

	<div class="fld">
		<label for="sf-note">Note</label>
		<input id="sf-note" placeholder="Optional" bind:value={note} />
	</div>
	<div class="form-actions">
		<button type="submit" class="btn" disabled={saving}>
			{stage === 'won' && buyerSide ? 'Move to deal' : `Move to ${target.label}`}
		</button>
		<button type="button" class="btn ghost" onclick={() => panel.setTab('overview')}>Cancel</button>
	</div>
</form>

{#if docs.length}
	<input bind:this={input} type="file" hidden aria-hidden="true" tabindex="-1" onchange={onPicked} />
	<section class="sec">
		<h3>Documents for this stage</h3>
		<div class="docs">
			{#each docs as d (d.type)}
				<div class="doc">
					<div>
						<div class="nm">{d.type}</div>
						<div class="fn">
							{#if d.file}{d.file.name} · {fileSize(d.file.size_bytes)}{:else}{d.note ? `${d.note} · ` : ''}not on file{/if}
						</div>
					</div>
					<div class="acts">
						{#if d.file}
							<Chip tone="green">On file</Chip>
						{:else}
							<button type="button" class="btn ghost sm" onclick={() => pickDoc(d.type)}>Upload</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
		<div class="small muted">Optional at this stage.</div>
	</section>
{/if}
