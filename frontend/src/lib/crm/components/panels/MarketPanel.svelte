<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import type { MarketListingDetail } from '../../api/types'
import { aed, fmt } from '../../format'
import { crmKeys, crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { patchCache, write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Empty from '../ui/Empty.svelte'
import Kv from '../ui/Kv.svelte'
import LeadItem from '../ui/LeadItem.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/** Another agency's portal listing vs comparable sales, buyers who fit, watchlist, Form I reminder. */
let { id }: { id: string } = $props()
const item = createQuery(() => crmQueries.marketListing(id))

function toggleWatch(m: MarketListingDetail) {
	const watch = !m.watch
	write({
		optimistic: () =>
			patchCache<MarketListingDetail>(crmKeys.marketListing(id), (x) => ({ ...x, watch })),
		call: () => crm.market.watch(id, watch),
		success: watch ? 'Added to watchlist' : 'Removed from watchlist'
	})
}
</script>

{#if item.data}
	{@const m = item.data}
	{@const max = Math.max(m.price_aed, m.value_from_comparables_aed)}
	<Panel kicker="Portal listing · {m.deal_type === 'sale' ? 'Sale' : 'Rent'}" title={m.unit} sub="{m.area} · Another agency">
		{#snippet tags()}
			<Chip chip={m.discount_chip} />
			{#if m.watch}<Chip tone="dark">★ Watchlist</Chip>{/if}
		{/snippet}

		<div class="kv">
			<Kv k="Price" v={aed(m.price_aed)} />
			<Kv k="Listed on" v="{m.portal} · {m.days_listed} days" />
			<Kv k="Price cuts" v={m.price_cuts ? `${m.price_cuts} since listing` : 'None'} />
			<Kv k="Value from matching sales" v={aed(m.value_from_comparables_aed)} />
		</div>

		<section class="sec">
			<h3>Price vs matching sales</h3>
			<div>
				{#each [['This listing', m.price_aed], ['Matching sales', m.value_from_comparables_aed]] as [k, v] (k)}
					<div class="hbar">
						<span>{k}</span>
						<span class="tr" aria-hidden="true"><i style:width="{(Number(v) / max) * 100}%"></i></span>
						<span class="v">{fmt(Number(v))}</span>
					</div>
				{/each}
			</div>
			<div><Chip chip={m.discount_chip} /></div>
		</section>

		<section class="sec">
			<h3>Your buyers who fit</h3>
			{#if m.matched.length}
				<div class="list boxed">
					{#each m.matched as l (l.id)}
						<LeadItem lead={l} detail={String(l.fields.wants ?? l.summary.line)} />
					{/each}
				</div>
			{:else}
				<Empty text="No matching buyers yet." />
			{/if}
		</section>

		<div class="notice">Another agency's listing — sign Form I with them before you share it.</div>

		{#snippet footer()}
			<button type="button" class="btn" aria-pressed={m.watch} onclick={() => toggleWatch(m)}>
				{m.watch ? 'Remove from watchlist' : '★ Add to watchlist'}
			</button>
			{#if m.matched.length}
				<button type="button" class="btn ghost" onclick={() => panel.open({ kind: 'send', target: 'market', id })}>
					Send to buyers
				</button>
			{/if}
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={item} kicker="Portal listing" />
{/if}
