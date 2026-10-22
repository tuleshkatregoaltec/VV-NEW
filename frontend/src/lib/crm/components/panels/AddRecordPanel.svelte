<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { untrack } from 'svelte'
import { crm } from '../../api'
import type { OwnerRecordCreate } from '../../api/types'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/**
 * Add record (7 Oct): a documented prior transaction with this owner, so the agency may contact
 * them (DLD Circular 02-2026). Sale, tenancy or listing agreement; a date not after today; the
 * unit; a reference. The toast's Undo takes it back. Opens from the owner panel, Matches tier 2
 * and an owner-file lead.
 */
let { ownerId }: { ownerId: string } = $props()

const owner = createQuery(() => crmQueries.owner(ownerId))
const me = createQuery(() => crmQueries.me())

const KINDS: { id: OwnerRecordCreate['kind']; label: string; ref: string }[] = [
	{ id: 'tenancy', label: 'Tenancy', ref: 'Ejari number' },
	{ id: 'sale', label: 'Sale', ref: 'DLD transaction number' },
	{ id: 'listing_agreement', label: 'Listing agreement', ref: 'Form A number' }
]

let kind = $state<OwnerRecordCreate['kind']>('tenancy')
let date = $state('')
let unit = $state('')
let reference = $state('')
let saving = $state(false)

$effect(() => {
	const o = owner.data
	if (o && !unit) untrack(() => (unit = o.units[0] ?? ''))
})

const refLabel = $derived(KINDS.find((k) => k.id === kind)?.ref ?? 'Reference')

async function submit(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const name = owner.data?.name ?? 'the owner'
	const r = await write({
		call: () =>
			crm.owners.addRecord(ownerId, { kind, date, unit: unit.trim(), reference: reference.trim() }),
		success: `Prior deal record added to ${name}`
	})
	saving = false
	if (r) panel.close()
}
</script>

{#if owner.data}
	{@const o = owner.data}
	<Panel kicker="Add record" title={o.name} sub="Prior deal with this owner, from your agency's file">
		<form class="form" onsubmit={submit}>
			<div class="fld">
				<span class="fl" id="ar-kind">Deal type</span>
				<div class="seg" role="radiogroup" aria-labelledby="ar-kind">
					{#each KINDS as k (k.id)}
						<button
							type="button"
							role="radio"
							aria-checked={kind === k.id}
							onclick={() => (kind = k.id)}
						>
							{k.label}
						</button>
					{/each}
				</div>
			</div>
			<div class="frow">
				<div class="fld">
					<label for="ar-date">Date</label>
					<input id="ar-date" type="date" required max={me.data?.today} bind:value={date} />
				</div>
				<div class="fld">
					<label for="ar-unit">Unit</label>
					<input id="ar-unit" required list="ar-units" bind:value={unit} />
					<datalist id="ar-units">
						{#each o.units as u (u)}<option value={u}></option>{/each}
					</datalist>
				</div>
			</div>
			<div class="fld">
				<label for="ar-ref">{refLabel} · required</label>
				<input id="ar-ref" required placeholder="DLD or Ejari number, or the Form A number" bind:value={reference} />
			</div>
			<div class="small muted">
				A deal, a tenancy or a signed listing agreement counts. Leads and call logs do not.
			</div>
			<div class="form-actions">
				<button type="submit" class="btn" disabled={saving}>Add record</button>
				<button type="button" class="btn ghost" onclick={() => panel.close()}>Cancel</button>
			</div>
		</form>
	</Panel>
{:else}
	<PanelLoading query={owner} kicker="Add record" />
{/if}
