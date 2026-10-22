<script lang="ts">
import type { LeadRow } from '../../api/types'
import { LEAD_TYPES, SOURCES } from '../../config'
import { openLead } from '../../stores/panel.svelte'
import ScoreChip from './ScoreChip.svelte'

/**
 * One lead as a list row: source colour, name, then the lead type and source wherever a lead
 * appears ("Buyer · Property Finder"), an optional detail, and the score.
 */
let { lead, detail, bar = false }: { lead: LeadRow; detail?: string; bar?: boolean } = $props()

const line = $derived(
	[LEAD_TYPES[lead.lead_type].label, lead.source_short, lead.listing?.label, detail]
		.filter(Boolean)
		.join(' · ')
)
</script>

<button type="button" class="li" onclick={() => openLead(lead.id)}>
	<span
		class={bar ? 'srcbar' : 'dot'}
		style:background={SOURCES[lead.source].color}
		title={SOURCES[lead.source].label}
	></span>
	<span>
		<div class="t">{lead.name}</div>
		<div class="d">{line}</div>
	</span>
	<ScoreChip score={lead.score} />
</button>
