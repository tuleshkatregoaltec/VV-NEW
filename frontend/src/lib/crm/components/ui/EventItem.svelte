<script lang="ts">
import type { CalendarEvent } from '../../api/types'
import { EVENT_KINDS } from '../../config'
import { openEvent } from '../../events'
import Chip from './Chip.svelte'

/** A day's event (v2): time, kind colour bar, title, then "Buyer · Property Finder" and the unit. */
let { event }: { event: CalendarEvent } = $props()
</script>

<button type="button" class="li evrow" onclick={() => openEvent(event)}>
	<span class="time">{event.time ?? 'Due'}</span>
	<span class="bar" style:background={EVENT_KINDS[event.kind].color} title={EVENT_KINDS[event.kind].label}></span>
	<span>
		<div class="t">{event.title}</div>
		<div class="d">{#if event.context}{event.context}{' · '}{/if}{event.detail}</div>
	</span>
	{#if event.outcome === 'No outcome logged'}<Chip tone="red">No outcome</Chip>{:else}<span></span>{/if}
</button>
