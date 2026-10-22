<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { goto } from '$app/navigation'
import type { LeadDetail } from '../../../api/types'
import { crmQueries } from '../../../queries'
import { panel } from '../../../stores/panel.svelte'
import { type MatchTabId, ui } from '../../../stores/ui.svelte'
import { errorText } from '../../../write'
import ErrorLine from '../../ui/ErrorLine.svelte'
import MatchTiers from '../../ui/MatchTiers.svelte'
import Skeleton from '../../ui/Skeleton.svelte'

/** The lead's Matches tab: the same tiers as the Matches section, for this lead. */
let { lead }: { lead: LeadDetail } = $props()

let after = $state(false)
const matches = createQuery(() => crmQueries.leadMatches(lead.id, { over: ui.matchOver, after }))

const tab = $derived<MatchTabId>(
	lead.lead_type === 'seller' || lead.lead_type === 'landlord'
		? 'sell'
		: lead.off_plan
			? 'offplan'
			: 'ready'
)

function openInMatches() {
	ui.matchTab = tab
	ui.matchSel[tab] = lead.id
	panel.closeAll()
	void goto('/crm/matches')
}
</script>

<div class="filters">
	{#if tab !== 'sell'}
		<label class="check small">
			<input type="checkbox" bind:checked={ui.matchOver} />
			Up to 10% over budget
		</label>
	{/if}
	<span class="count">
		<button type="button" class="btn ghost sm" onclick={openInMatches}>Open in Matches</button>
	</span>
</div>

{#if matches.data}
	{#if matches.isError}
		<ErrorLine bar message={errorText(matches.error)} onretry={() => matches.refetch()} />
	{/if}
	<div class="panel-tiers">
		<MatchTiers data={matches.data} bind:after />
	</div>
{:else if matches.isError}
	<ErrorLine message={errorText(matches.error)} onretry={() => matches.refetch()} />
{:else}
	<Skeleton rows={4} />
{/if}
