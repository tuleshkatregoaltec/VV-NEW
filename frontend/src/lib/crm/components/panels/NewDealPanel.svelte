<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { untrack } from 'svelte'
import { goto } from '$app/navigation'
import { crm } from '../../api'
import { addDays } from '../../format'
import { crmQueries } from '../../queries'
import { ui } from '../../stores/ui.svelte'
import { write } from '../../write'
import MoneyInput from '../ui/MoneyInput.svelte'
import Panel from '../ui/Panel.svelte'

/** Deal: lead, unit, agreed value, due date. The document list follows the deal type. */
let { leadId }: { leadId?: number } = $props()

const me = createQuery(() => crmQueries.me())
const leads = createQuery(() => crmQueries.leads({}))
const mine = createQuery(() => crmQueries.listings('mine'))
const company = createQuery(() => crmQueries.listings('company'))

const options = $derived((leads.data?.items ?? []).filter((l) => l.stage !== 'lost'))
const units = $derived(
	[...(mine.data?.items ?? []), ...(company.data?.items ?? [])].filter(
		(x) => x.status !== 'expired'
	)
)

let lead = $state<number | null>(untrack(() => leadId ?? null))
let listing = $state('')
let value = $state<number | null>(null)
let due = $state('')
let saving = $state(false)

$effect(() => {
	if (lead === null && options.length) lead = options[0].id
})
$effect(() => {
	if (!due && me.data) due = addDays(me.data.today, 21)
})
$effect(() => {
	const l = options.find((x) => x.id === lead)
	if (l && value === null && l.fields.budget_aed) value = Number(l.fields.budget_aed)
})

async function submit(e: SubmitEvent) {
	e.preventDefault()
	if (lead === null) return
	const leadIdNow = lead
	saving = true
	const r = await write({
		call: () =>
			crm.deals.create({
				lead_id: leadIdNow,
				listing_id: listing || null,
				value_aed: Number(value) || null,
				due_date: due || null
			}),
		success: (res) => `${res.data.id} opened`,
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
}
</script>

<Panel kicker="New" title="Deal">
	<form class="form" onsubmit={submit}>
		<div class="fld">
			<label for="nd-lead">Lead</label>
			<select id="nd-lead" bind:value={lead} required>
				{#each options as l (l.id)}<option value={l.id}>{l.name} · {l.summary.line}</option>{/each}
			</select>
		</div>
		<div class="fld">
			<label for="nd-unit">Unit</label>
			<select id="nd-unit" bind:value={listing}>
				<option value="">Off-market · to confirm</option>
				{#each units as x (x.id)}<option value={x.id}>{x.unit}</option>{/each}
			</select>
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nd-value">Agreed value (AED)</label>
				<MoneyInput id="nd-value" required bind:value />
			</div>
			<div class="fld">
				<label for="nd-due">Due date</label>
				<input id="nd-due" type="date" bind:value={due} />
			</div>
		</div>
		<div><button type="submit" class="btn" disabled={saving || lead === null}>Open deal</button></div>
	</form>
</Panel>
