<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import { crmQueries } from '../../queries'
import { openOwner } from '../../stores/panel.svelte'
import { write } from '../../write'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/** One-page property report PDF preview for an owner. */
let { id }: { id: string } = $props()
const report = createQuery(() => crmQueries.ownerReport(id))

function send() {
	write({ call: () => crm.owners.sendReport(id), success: 'Report sent' })
}
</script>

{#if report.data}
	{@const r = report.data}
	<Panel kicker="Owner report PDF" title={r.owner} sub="{r.rows.length} unit{r.rows.length > 1 ? 's' : ''}">
		<div class="paper">
			<h4><span>Property report · {r.owner}</span><span style="font-weight:400;color:#666">{r.period}</span></h4>
			{#each r.rows as row (row.unit)}
				<div class="pf"><span>{row.unit}</span><span>{row.line}</span></div>
			{/each}
			<div class="pf"><span>Next event</span><span>{r.next_event}</span></div>
			<div class="foot">Area figures: DLD and Ejari, last 12 months (sample).</div>
		</div>
		{#snippet footer()}
			<button type="button" class="btn" onclick={send}>Send PDF</button>
			<button type="button" class="btn ghost" onclick={() => openOwner(id)}>Back to the owner</button>
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={report} kicker="Owner report PDF" />
{/if}
