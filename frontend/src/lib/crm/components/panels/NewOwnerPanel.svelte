<script lang="ts">
import { goto } from '$app/navigation'
import { crm } from '../../api'
import { ui } from '../../stores/ui.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'

/** Owner: name, units (one per line). Source = the agent's contact. */
let name = $state('')
let units = $state('')
let saving = $state(false)

async function submit(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const r = await write({
		call: () => crm.owners.create({ name: name.trim(), units: units.split('\n') }),
		success: (res) => `${res.data.name} added`
	})
	saving = false
	if (r) {
		ui.ownerTab = 'all'
		ui.ownerSource = ''
		await goto(`/crm/owners?owner=${r.data.id}`)
	}
}
</script>

<Panel kicker="New" title="Owner">
	<form class="form" onsubmit={submit}>
		<div class="fld">
			<label for="no-name">Name</label>
			<input id="no-name" required bind:value={name} />
		</div>
		<div class="fld">
			<label for="no-units">Units (one per line)</label>
			<textarea id="no-units" placeholder="Building · unit" bind:value={units}></textarea>
		</div>
		<div><button type="submit" class="btn" disabled={saving}>Add owner</button></div>
	</form>
</Panel>
