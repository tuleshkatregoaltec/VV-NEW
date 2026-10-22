<script lang="ts">
import { crm } from '../../../api'
import type { LeadDetail } from '../../../api/types'
import { panel } from '../../../stores/panel.svelte'
import { write } from '../../../write'

let { lead }: { lead: LeadDetail } = $props()

let text = $state('')
let saving = $state(false)

async function submit(e: SubmitEvent) {
	e.preventDefault()
	if (!text.trim()) return
	saving = true
	const r = await write({
		call: () => crm.leads.addNote(lead.id, text.trim()),
		success: 'Note saved'
	})
	saving = false
	if (r) panel.setTab('history')
}
</script>

<form class="form" onsubmit={submit}>
	<div class="fld">
		<label for="note-text">Note</label>
		<!-- svelte-ignore a11y_autofocus -->
		<textarea
			id="note-text"
			required
			autofocus
			placeholder="What happened, what is next"
			bind:value={text}
		></textarea>
	</div>
	<div class="form-actions">
		<button type="submit" class="btn" disabled={saving || !text.trim()}>Save note</button>
		<button type="button" class="btn ghost" onclick={() => panel.setTab('overview')}>Cancel</button>
	</div>
</form>
