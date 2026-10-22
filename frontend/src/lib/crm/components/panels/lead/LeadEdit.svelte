<script lang="ts">
import { untrack } from 'svelte'
import { crm } from '../../../api'
import type { LeadDetail, LeadFieldKey, LeadFields, LeadType } from '../../../api/types'
import { LEAD_TYPE_ORDER, LEAD_TYPES } from '../../../config'
import { panel } from '../../../stores/panel.svelte'
import { write } from '../../../write'
import MoneyInput from '../../ui/MoneyInput.svelte'

/**
 * Lead type first; switching type swaps the fields and keeps values already typed. A changed
 * phone is normalised by the backend (+971) and re-runs the owner match.
 */
let { lead }: { lead: LeadDetail } = $props()

const initial = untrack(() => ({
	type: lead.lead_type,
	draft: Object.fromEntries(
		Object.entries(lead.fields).map(([k, v]) => [k, v === null || v === undefined ? '' : String(v)])
	) as Record<string, string>,
	money: {
		budget_aed: Number(lead.fields.budget_aed) || null,
		target_price_aed: Number(lead.fields.target_price_aed) || null,
		expected_rent_aed: Number(lead.fields.expected_rent_aed) || null
	} as Record<string, number | null>,
	phone: lead.phone,
	name: lead.lease && lead.name === lead.lease.unit ? '' : lead.name,
	email: lead.email,
	next: lead.next_action,
	language: lead.language
}))

let type = $state<LeadType>(initial.type)
let draft = $state<Record<string, string>>(initial.draft)
let money = $state<Record<string, number | null>>(initial.money)
let phone = $state(initial.phone)
let name = $state(initial.name)
let email = $state(initial.email)
let next = $state(initial.next)
let language = $state(initial.language)
let saving = $state(false)

const fields = $derived(LEAD_TYPES[type].fields)
/** A Vitevue lease lead stays "seller or landlord" until the agent picks one. */
const types = $derived(
	lead.lead_type === 'owner' ? (['owner', ...LEAD_TYPE_ORDER] as LeadType[]) : LEAD_TYPE_ORDER
)

async function save(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const out: LeadFields = {}
	for (const f of fields) {
		const k = f.key as LeadFieldKey
		if (f.money) out[k] = money[k] ?? null
		else {
			const raw = String(draft[k] ?? '').trim()
			out[k] = raw === '' ? null : raw
		}
	}
	const typeChanged = type !== lead.lead_type
	const phoneChanged = phone.trim() !== lead.phone
	const nameChanged = name.trim() !== initial.name
	const emailChanged = email.trim() !== lead.email
	const r = await write({
		call: () =>
			crm.leads.patch(lead.id, {
				lead_type: type,
				fields: out,
				next_action: next,
				language,
				...(phoneChanged ? { phone: phone.trim() } : {}),
				...(nameChanged ? { name: name.trim() } : {}),
				...(emailChanged ? { email: email.trim() } : {})
			}),
		success: (res) => (res.undo_token ? 'Changes saved' : null),
		event: typeChanged ? 'lead_type_changed' : undefined
	})
	saving = false
	if (r) panel.setTab('overview')
}
</script>

<form class="form" onsubmit={save}>
	<div class="fld">
		<label for="edit-type">Lead type</label>
		<select id="edit-type" bind:value={type}>
			{#each types as t (t)}
				<option value={t}>{LEAD_TYPES[t].label}</option>
			{/each}
		</select>
	</div>
	{#each fields as f (f.key)}
		<div class="fld">
			<label for="edit-{f.key}">{f.label}{f.money ? ' (AED)' : ''}</label>
			{#if f.money}
				<MoneyInput id="edit-{f.key}" bind:value={money[f.key]} />
			{:else if f.options}
				<select id="edit-{f.key}" bind:value={draft[f.key]}>
					<option value="">Not stated</option>
					{#each f.options as o (o)}<option value={o}>{o}</option>{/each}
				</select>
			{:else}
				<input id="edit-{f.key}" type="text" bind:value={draft[f.key]} />
			{/if}
		</div>
	{/each}
	<div class="fld">
		<label for="edit-name">{lead.lease ? 'Owner name' : 'Name'}</label>
		<input id="edit-name" autocomplete="off" required={!lead.lease} bind:value={name} />
	</div>
	<div class="frow">
		<div class="fld">
			<label for="edit-phone">Phone</label>
			<input
				id="edit-phone"
				inputmode="tel"
				autocomplete="off"
				placeholder="050 123 4567"
				required={!lead.lease}
				bind:value={phone}
			/>
		</div>
		<div class="fld">
			<label for="edit-email">Email</label>
			<input id="edit-email" type="email" autocomplete="off" bind:value={email} />
		</div>
	</div>
	<div class="frow">
		<div class="fld">
			<label for="edit-next">Next action</label>
			<input id="edit-next" bind:value={next} />
		</div>
		<div class="fld">
			<label for="edit-lang">Language</label>
			<input id="edit-lang" bind:value={language} />
		</div>
	</div>
	<div class="form-actions">
		<button type="submit" class="btn" disabled={saving}>Save</button>
		<button type="button" class="btn ghost" onclick={() => panel.setTab('overview')}>Cancel</button>
	</div>
</form>
