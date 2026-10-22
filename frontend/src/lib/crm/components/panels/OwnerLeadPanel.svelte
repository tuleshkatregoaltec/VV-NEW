<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { goto } from '$app/navigation'
import { crm } from '../../api'
import type { LeadType } from '../../api/types'
import { LEAD_TYPE_ORDER, LEAD_TYPES } from '../../config'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/**
 * Owner → lead by hand (automatic through a workflow later). Landlord by default; the type can
 * change. The owner record stays as it is.
 */
let { id }: { id: string } = $props()
const owner = createQuery(() => crmQueries.owner(id))

let type = $state<LeadType | null>(null)
let unitKey = $state('')
let next = $state('')
let nextTouched = false
let saving = $state(false)

$effect(() => {
	const o = owner.data
	if (!o?.create_lead) return
	if (type === null) type = o.create_lead.lead_type
	if (!unitKey && o.unit_rows.length) unitKey = o.unit_rows[0].key
})
$effect(() => {
	const suggestions = owner.data?.create_lead?.next_action
	if (type && suggestions && !nextTouched) next = suggestions[type]
})

async function submit(e: SubmitEvent) {
	e.preventDefault()
	if (!type || !unitKey) return
	const leadType = type
	saving = true
	const r = await write({
		call: () =>
			crm.owners.createLead(id, {
				lead_type: leadType,
				unit_key: unitKey,
				next_action: next.trim()
			}),
		success: (res) => `${res.data.name} is now a ${LEAD_TYPES[leadType].label.toLowerCase()} lead`,
		created: (res) => ({ kind: 'lead', id: res.data.id }),
		event: 'lead_created'
	})
	saving = false
	if (r) await goto(`/crm/leads?lead=${r.data.id}`)
}
</script>

{#if owner.data}
	{@const o = owner.data}
	<Panel kicker="Owner → lead" title={o.name} sub="{o.next_event} · {o.next_event_label}">
		{#if o.create_lead}
			<form class="form" onsubmit={submit}>
				<div class="fld">
					<label for="ol-type">Lead type</label>
					<select id="ol-type" bind:value={type}>
						{#each LEAD_TYPE_ORDER as t (t)}<option value={t}>{LEAD_TYPES[t].label}</option>{/each}
					</select>
				</div>
				<div class="fld">
					<label for="ol-unit">Unit</label>
					<select id="ol-unit" bind:value={unitKey}>
						{#each o.unit_rows as u (u.key)}
							<option value={u.key}>{u.building} #{u.number} · {u.status_chip.label}</option>
						{/each}
					</select>
				</div>
				<div class="fld">
					<label for="ol-next">Next action</label>
					<input id="ol-next" required bind:value={next} oninput={() => (nextTouched = true)} />
				</div>
				<div class="form-actions">
					<button type="submit" class="btn" disabled={saving || !type}>Create lead</button>
					<button type="button" class="btn ghost" onclick={() => panel.close()}>Cancel</button>
				</div>
			</form>
		{:else}
			<div class="small muted">{o.lead ? 'This owner is already a lead.' : 'Nothing actionable for this owner right now.'}</div>
		{/if}
	</Panel>
{:else}
	<PanelLoading query={owner} kicker="Owner" />
{/if}
