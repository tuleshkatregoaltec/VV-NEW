<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../../api'
import type { LeadDetail } from '../../../api/types'
import { ORIGIN_TONE } from '../../../config'
import { crmQueries } from '../../../queries'
import { toast } from '../../../stores/toast.svelte'
import { errorText, refreshCrm } from '../../../write'
import Chip from '../../ui/Chip.svelte'
import QueryView from '../../ui/QueryView.svelte'

/** Every change, newest first, with its origin; "Undo last change" reverts the latest. */
let { lead }: { lead: LeadDetail } = $props()

const events = createQuery(() => crmQueries.leadEvents(lead.id))
let busy = $state(false)

async function undoLast() {
	busy = true
	try {
		const r = await crm.leads.undoLast(lead.id)
		toast.show(`Undone: ${r.undone}`)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	} finally {
		busy = false
		await refreshCrm()
	}
}
</script>

<section class="sec">
	<h3>
		History
		{#if lead.can_undo}
			<button type="button" class="btn ghost sm" disabled={busy} onclick={undoLast}>
				Undo last change
			</button>
		{/if}
	</h3>
	<QueryView query={events}>
		{#snippet children(rows)}
			<div class="hist">
				{#each rows as e (e.id)}
					<div>
						<span class="when">{e.at}</span>
						<span>{e.text}</span>
						<Chip tone={ORIGIN_TONE[e.origin]}>{e.origin}</Chip>
					</div>
				{/each}
			</div>
		{/snippet}
	</QueryView>
</section>
