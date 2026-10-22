<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import { price } from '../../format'
import { crmQueries } from '../../queries'
import { toast } from '../../stores/toast.svelte'
import { errorText } from '../../write'
import Kv from '../ui/Kv.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/** A client from the portfolio: every deal the agent closed with them. */
let { client }: { client: string } = $props()
const portfolio = createQuery(() => crmQueries.portfolio())
const entry = $derived(portfolio.data?.clients.find((c) => c.client === client))

async function checkIn() {
	try {
		await crm.portfolio.checkIn(client)
		toast.show(`Check-in draft for ${client} sent`)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}
</script>

{#if entry}
	<Panel
		kicker="Client"
		title={entry.client}
		sub="{entry.transactions.length} transaction{entry.transactions.length > 1 ? 's' : ''}"
	>
		<div class="kv">
			{#each entry.transactions as t, i (i)}
				<Kv k="{t.what} · {t.date}" v="{t.unit} · {price(t.kind, t.value_aed)}" o={t.dld_or_ejari} wide />
			{/each}
		</div>
		{#snippet footer()}
			<button type="button" class="btn" onclick={checkIn}>Send a check-in draft</button>
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={portfolio} kicker="Client" />
{/if}
