<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import { fileSize } from '../../format'
import { crmQueries } from '../../queries'
import { openDeal, panel } from '../../stores/panel.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'

/** A dropped file the backend could not match: ask which document it is (or skip it). */
let { dealId, files, index }: { dealId: string; files: File[]; index: number } = $props()

const deal = createQuery(() => crmQueries.deal(dealId))
const file = $derived(files[index])
const options = $derived(
	deal.data ? (deal.data.missing.length ? deal.data.missing : deal.data.documents.required) : []
)
let choice = $state('')
let saving = $state(false)

$effect(() => {
	if (!choice && options.length) choice = options[0]
})

function next() {
	if (index + 1 < files.length) {
		choice = ''
		panel.open({ kind: 'which-doc', dealId, files, index: index + 1 }, { replace: true })
	} else {
		panel.close()
		openDeal(dealId, 'documents')
	}
}

/** Move on only when the file was filed; a failure stays on this file with one line. */
async function fileIt() {
	if (!choice) return
	saving = true
	const r = await write({
		call: () => crm.deals.upload(dealId, file, choice),
		success: `${choice} filed on ${dealId}`,
		event: 'document_uploaded'
	})
	saving = false
	if (r) next()
}
</script>

<Panel
	kicker="Which document is this?{files.length > 1 ? ` · ${index + 1} of ${files.length}` : ''}"
	title={file.name}
	sub="{fileSize(file.size)} · {dealId}"
>
	<div class="form">
		<div class="fld">
			<label for="which-doc">File it as</label>
			<select id="which-doc" bind:value={choice}>
				{#each options as o (o)}<option value={o}>{o}</option>{/each}
			</select>
		</div>
		<div class="form-actions">
			<button type="button" class="btn" disabled={!choice || saving} onclick={fileIt}>File it</button>
			<button type="button" class="btn ghost" onclick={next}>Skip this file</button>
		</div>
	</div>
</Panel>
