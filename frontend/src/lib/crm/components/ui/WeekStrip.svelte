<script lang="ts">
import type { EventKind } from '../../api/types'
import { EVENT_KINDS } from '../../config'
import { dayLabel, dayOfMonth, weekdayShort } from '../../format'

/** Monday–Sunday; dots = event kinds that day; tap a day to switch. */
let {
	week,
	selected,
	today,
	onselect
}: {
	week: { date: string; kinds: EventKind[] }[]
	selected: string
	today: string
	onselect: (date: string) => void
} = $props()
</script>

<div class="week" role="group" aria-label="This week">
	{#each week as d (d.date)}
		<button
			type="button"
			class={['day', d.date === today && 'today']}
			aria-pressed={d.date === selected}
			aria-label="{dayLabel(d.date)}{d.date === today ? ', today' : ''}, {d.kinds.length} events"
			onclick={() => onselect(d.date)}
		>
			<div class="w">{weekdayShort(d.date)}</div>
			<div class="n">{dayOfMonth(d.date)}</div>
			<div class="ds">
				{#each d.kinds.slice(0, 4) as k, i (i)}<i style:background={EVENT_KINDS[k].color}></i>{/each}
			</div>
		</button>
	{/each}
</div>
