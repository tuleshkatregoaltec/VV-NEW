<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { untrack } from 'svelte'
import { crm } from '../../../api'
import type { LeadDetail } from '../../../api/types'
import { addDays, dayLabel } from '../../../format'
import { crmQueries } from '../../../queries'
import { panel } from '../../../stores/panel.svelte'
import { write } from '../../../write'

/** Book viewing: date, time, unit, note → calendar. */
let { lead }: { lead: LeadDetail } = $props()

const me = createQuery(() => crmQueries.me())
const mine = createQuery(() => crmQueries.listings('mine'))
const company = createQuery(() => crmQueries.listings('company'))
const units = $derived(
	[...(mine.data?.items ?? []), ...(company.data?.items ?? [])].filter(
		(x) => x.status !== 'expired'
	)
)

let date = $state(untrack(() => (me.data ? addDays(me.data.today, 3) : '')))
let time = $state('12:00')
let listingId = $state(untrack(() => lead.listing?.id ?? ''))
let note = $state('')
let saving = $state(false)

$effect(() => {
	if (!date && me.data) date = addDays(me.data.today, 3)
})

async function submit(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const r = await write({
		call: () =>
			crm.leads.bookViewing(lead.id, {
				date,
				time,
				listing_id: listingId || null,
				note: note.trim()
			}),
		success: `Viewing booked ${dayLabel(date)} ${time} · in the calendar`,
		event: 'viewing_booked'
	})
	saving = false
	if (r) panel.setTab('overview')
}
</script>

<form class="form" onsubmit={submit}>
	<div class="frow">
		<div class="fld">
			<label for="book-date">Date</label>
			<input id="book-date" type="date" required bind:value={date} />
		</div>
		<div class="fld">
			<label for="book-time">Time</label>
			<input id="book-time" type="time" required bind:value={time} />
		</div>
	</div>
	<div class="fld">
		<label for="book-unit">Unit</label>
		<select id="book-unit" bind:value={listingId}>
			<option value="">To confirm</option>
			{#each units as x (x.id)}<option value={x.id}>{x.unit}</option>{/each}
		</select>
	</div>
	<div class="fld">
		<label for="book-note">Note</label>
		<input id="book-note" placeholder="Parking, access, who is meeting them" bind:value={note} />
	</div>
	<div class="form-actions">
		<button type="submit" class="btn" disabled={saving || !date}>Book viewing</button>
		<button type="button" class="btn ghost" onclick={() => panel.setTab('overview')}>Cancel</button>
	</div>
</form>
