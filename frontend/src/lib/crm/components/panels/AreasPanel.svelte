<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import { fmt } from '../../format'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { toast } from '../../stores/toast.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'
import QueryView from '../ui/QueryView.svelte'

/**
 * My areas: leases in these areas that end within the next `forward` days, or ended within the
 * last `lookback` days, arrive in the Inbox as Vitevue renewal leads. The lease data reaches
 * 90 days ahead, so the forward window stops there.
 */
const areas = createQuery(() => crmQueries.feedAreas())
const saved = createQuery(() => crmQueries.feedPreferences())

let picked = $state<Set<string> | null>(null)
const selected = $derived(picked ?? new Set(saved.data?.areas ?? []))
let forward = $state<number | null>(null)
let lookback = $state<number | null>(null)
const forwardDays = $derived(forward ?? saved.data?.forward_days ?? 60)
const lookbackDays = $derived(lookback ?? saved.data?.lookback_days ?? 30)

function toggle(area: string, on: boolean) {
	const next = new Set(selected)
	if (on) next.add(area)
	else next.delete(area)
	picked = next
}

async function save() {
	const list = [...selected]
	if (!list.length) {
		toast.show('Pick at least one area', { tone: 'error' })
		return
	}
	if (!(forwardDays >= 1 && forwardDays <= 90) || !(lookbackDays >= 0 && lookbackDays <= 365)) {
		toast.show('Days ahead: 1 to 90. Days back: 0 to 365.', { tone: 'error' })
		return
	}
	const r = await write({
		call: () =>
			crm.feed.setPreferences({
				areas: list,
				forward_days: forwardDays,
				lookback_days: lookbackDays
			}),
		success: `Areas saved · ${list.length}`
	})
	if (r) panel.close()
}
</script>

<Panel kicker="Renewal leads" title="My areas" sub="{selected.size} chosen">
	<div class="fld">
		<label for="areas-forward">Lease ends in the next (days)</label>
		<input
			id="areas-forward"
			type="number"
			min="1"
			max="90"
			value={forwardDays}
			oninput={(e) => (forward = e.currentTarget.valueAsNumber)}
		/>
	</div>
	<div class="fld">
		<label for="areas-lookback">Or ended in the last (days)</label>
		<input
			id="areas-lookback"
			type="number"
			min="0"
			max="365"
			value={lookbackDays}
			oninput={(e) => (lookback = e.currentTarget.valueAsNumber)}
		/>
	</div>
	<QueryView query={areas} rows={6}>
		{#snippet children(list)}
			<fieldset class="docs">
				<legend class="sr-only">Areas</legend>
				{#each list as a (a.area)}
					<label class="doc" style="grid-template-columns:auto minmax(0,1fr) auto;cursor:pointer">
						<input
							type="checkbox"
							checked={selected.has(a.area)}
							onchange={(e) => toggle(a.area, e.currentTarget.checked)}
						/>
						<span>{a.area}</span>
						<span class="small muted num" title="Units with a lease ending in the next 90 days">
							{fmt(a.owners_in_90_day_window)}
						</span>
					</label>
				{/each}
			</fieldset>
		{/snippet}
	</QueryView>
	{#snippet footer()}
		<button type="button" class="btn" onclick={save}>Save</button>
	{/snippet}
</Panel>
