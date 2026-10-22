<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { dayLabel, plural } from '../../format'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import Empty from '../ui/Empty.svelte'
import EventItem from '../ui/EventItem.svelte'
import Panel from '../ui/Panel.svelte'
import QueryView from '../ui/QueryView.svelte'

/** Every event on one day (from a month cell's "+N more"). */
let { date }: { date: string } = $props()
const events = createQuery(() => crmQueries.events(date, date))
</script>

<Panel kicker="Calendar" title={dayLabel(date)} sub={events.data ? plural(events.data.length, 'event') : ''}>
	<QueryView query={events} rows={3}>
		{#snippet children(list)}
			{#if list.length}
				<div class="list boxed">
					{#each list as e (e.id)}<EventItem event={e} />{/each}
				</div>
			{:else}
				<Empty text="Nothing booked this day." />
			{/if}
		{/snippet}
	</QueryView>
	{#snippet footer()}
		<button type="button" class="btn" onclick={() => panel.open({ kind: 'new-task', date })}>＋ Add</button>
	{/snippet}
</Panel>
