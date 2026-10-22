<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import { fmt, price } from '../../format'
import { crmQueries } from '../../queries'
import { openListing } from '../../stores/panel.svelte'
import { toast } from '../../stores/toast.svelte'
import { errorText } from '../../write'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/** One-page A4 brochure preview. */
let { id }: { id: string } = $props()
const listing = createQuery(() => crmQueries.listing(id))

async function send() {
	try {
		await crm.listings.sendBrochure(id)
		toast.show('Brochure PDF sent')
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}
</script>

{#if listing.data}
	{@const x = listing.data}
	<Panel kicker="Brochure PDF" title={x.unit} sub="One page, A4">
		<div class="paper">
			<h4><span>{x.unit}</span><span style="font-weight:400;color:#666">{x.area}</span></h4>
			<div class="photo">Listing photos</div>
			{#each [['Price', price(x.deal_type, x.price_aed)], ['Size', `${x.bedrooms ? `${x.bedrooms} bedrooms` : 'Studio'} · ${fmt(x.size_sqft)} sqft`], ['Area', x.area], ['Recent sales', x.comparables_note], ['Permit', x.permit ? `Trakheesi ${x.permit.number}` : '—'], ['Agent', x.agent]] as [k, v] (k)}
				<div class="pf"><span>{k}</span><span>{v}</span></div>
			{/each}
			<div class="foot">The permit QR code prints here.</div>
		</div>
		{#snippet footer()}
			<button type="button" class="btn" onclick={send}>Send PDF</button>
			<button type="button" class="btn ghost" onclick={() => openListing(id)}>Back to the listing</button>
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={listing} kicker="Brochure PDF" />
{/if}
