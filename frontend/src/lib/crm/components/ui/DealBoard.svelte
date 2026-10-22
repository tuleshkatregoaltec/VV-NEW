<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import type { DealRow, DealType } from '../../api/types'
import { crmQueries } from '../../queries'
import { openDeal } from '../../stores/panel.svelte'
import Chip from './Chip.svelte'

/**
 * Conveyancing board: open deals by stage. Card = unit, client, documents bar, due chip.
 * Column names come from the API (agencies may rename stages).
 */
let { deals, type }: { deals: DealRow[]; type: DealType } = $props()

const stageSets = createQuery(() => crmQueries.dealStages())
const ofType = $derived(deals.filter((d) => d.deal_type === type))
const stages = $derived(ofType[0]?.stages ?? stageSets.data?.[type] ?? [])
</script>

<div class="board">
	{#each stages as stage, i (stage)}
		{@const cards = ofType.filter((d) => d.stage_index === i)}
		<section class="col" aria-label="{stage}, {cards.length} deals">
			<div class="colh"><span>{stage}</span><span>{cards.length}</span></div>
			{#each cards as d (d.id)}
				<button type="button" class="bcard" onclick={() => openDeal(d.id)}>
					<span class="t">{d.unit}</span>
					<span class="d">{d.client}</span>
					<div class="prog" aria-hidden="true">
						<i style:width="{(d.docs_received / d.docs_required) * 100}%"></i>
					</div>
					<div class="f">
						<span class="d">{d.docs_received}/{d.docs_required} docs</span>
						<Chip chip={d.due_chip} />
					</div>
				</button>
			{:else}
				<div class="small muted px-1">—</div>
			{/each}
		</section>
	{/each}
</div>
