<script lang="ts">
import { crm } from '../../../api'
import type { DealDetail } from '../../../api/types'
import { fmt } from '../../../format'
import { toast } from '../../../stores/toast.svelte'
import { errorText, refreshCrm, write } from '../../../write'
import Chip from '../../ui/Chip.svelte'

/** Due diligence (Mark complete) · screening (Run check → result) · goAML only when cash ≥ AED 55,000. */
let { deal }: { deal: DealDetail } = $props()

let wasRunning = false
$effect(() => {
	const s = deal.compliance.screening
	if (wasRunning && s === 'clear') toast.show(`Screening clear — ${deal.client}`)
	if (wasRunning && s === 'review')
		toast.show(`Screening needs review — ${deal.client}`, { tone: 'error' })
	wasRunning = s === 'running'
})

function complete() {
	write({
		call: () => crm.deals.compliance(deal.id, { due_diligence: 'complete' }),
		success: `Due diligence complete for ${deal.client}`
	})
}

async function screen() {
	try {
		await crm.deals.screening(deal.id)
		wasRunning = true
		await refreshCrm()
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}

async function goaml() {
	try {
		await crm.deals.goaml(deal.id)
		toast.show(`goAML report prepared for ${deal.id} — ready to file`)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}
</script>

<section class="sec">
	<h3>Customer due diligence</h3>
	<div class="doc">
		<div>
			<div class="nm">{deal.client}</div>
			<div class="fn">ID, address, source of funds · kept five years</div>
		</div>
		<div class="acts">
			{#if deal.compliance.due_diligence === 'complete'}
				<Chip tone="green">Complete</Chip>
			{:else}
				<Chip tone="amber">Pending</Chip>
				<button type="button" class="btn sm" onclick={complete}>Mark complete</button>
			{/if}
		</div>
	</div>
</section>

<section class="sec">
	<h3>Sanctions and PEP screening</h3>
	<div class="doc">
		<div>
			<div class="nm">{deal.client}</div>
			<div class="fn">UAE Local Terrorist List · UN list · international lists</div>
		</div>
		<div class="acts" aria-live="polite">
			{#if deal.compliance.screening === 'clear'}
				<Chip tone="green">Clear</Chip>
			{:else if deal.compliance.screening === 'running'}
				<Chip>Running…</Chip>
			{:else if deal.compliance.screening === 'review'}
				<Chip tone="red">Review</Chip>
			{:else}
				<button type="button" class="btn sm" onclick={screen}>Run check</button>
			{/if}
		</div>
	</div>
</section>

{#if deal.compliance.goaml_required}
	<section class="sec">
		<h3>goAML</h3>
		<div class="doc">
			<div>
				<div class="nm">Real Estate Activity Report</div>
				<div class="fn">AED {fmt(deal.compliance.cash_aed)} paid in cash · file before transfer</div>
			</div>
			<div class="acts">
				<Chip tone="amber">Draft</Chip>
				<button type="button" class="btn ghost sm" onclick={goaml}>Export for goAML</button>
			</div>
		</div>
	</section>
{:else}
	<div class="notice">No cash payment of AED 55,000 or more on this deal — no goAML report needed.</div>
{/if}
