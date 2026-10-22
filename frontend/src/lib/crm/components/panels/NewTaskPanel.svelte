<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { untrack } from 'svelte'
import { crm } from '../../api'
import type { EventKind } from '../../api/types'
import { EVENT_KIND_ORDER, EVENT_KINDS } from '../../config'
import { addDays, dayLabel, monthOf } from '../../format'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { ui } from '../../stores/ui.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'

/** Task or viewing: title, date, time, kind. */
let { date: initialDate }: { date?: string } = $props()

const me = createQuery(() => crmQueries.me())

let title = $state('')
let date = $state(untrack(() => initialDate ?? ''))
let time = $state('10:00')
let kind = $state<EventKind>('call')
let saving = $state(false)

$effect(() => {
	if (!date && me.data) date = addDays(me.data.today, 1)
})

async function submit(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const r = await write({
		call: () => crm.events.create({ title: title.trim(), date, time: time || null, kind }),
		success: `Added ${dayLabel(date)}`
	})
	saving = false
	if (r) {
		ui.weekDay = date
		ui.calMonth = monthOf(date)
		panel.close()
	}
}
</script>

<Panel kicker="New" title="Task or viewing">
	<form class="form" onsubmit={submit}>
		<div class="fld">
			<label for="nt-title">Title</label>
			<input id="nt-title" required placeholder="Call, viewing, follow-up" bind:value={title} />
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nt-date">Date</label>
				<input id="nt-date" type="date" required bind:value={date} />
			</div>
			<div class="fld">
				<label for="nt-time">Time</label>
				<input id="nt-time" type="time" bind:value={time} />
			</div>
		</div>
		<div class="fld">
			<label for="nt-kind">Kind</label>
			<select id="nt-kind" bind:value={kind}>
				{#each EVENT_KIND_ORDER as k (k)}<option value={k}>{EVENT_KINDS[k].label}</option>{/each}
			</select>
		</div>
		<div><button type="submit" class="btn" disabled={saving || !date}>Add to calendar</button></div>
	</form>
</Panel>
