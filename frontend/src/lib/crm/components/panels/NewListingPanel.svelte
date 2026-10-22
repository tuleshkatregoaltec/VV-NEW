<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { goto } from '$app/navigation'
import { crm } from '../../api'
import type { DealType } from '../../api/types'
import { crmQueries } from '../../queries'
import { ui } from '../../stores/ui.svelte'
import { write } from '../../write'
import MoneyInput from '../ui/MoneyInput.svelte'
import Panel from '../ui/Panel.svelte'

/**
 * New listing. From a seller/landlord lead ("Create listing") the form is filled from the
 * lead: unit, area, sale/rent, price = target price or expected rent, owner = lead; pocket.
 */
let { fromLeadId }: { fromLeadId?: number } = $props()

const lead = createQuery(() => ({
	...crmQueries.lead(fromLeadId ?? 0),
	enabled: fromLeadId !== undefined
}))

let unit = $state('')
let area = $state('')
let dealType = $state<DealType>('sale')
let priceAed = $state<number | null>(null)
let bedrooms = $state(1)
let size = $state(800)
let status = $state<'pocket' | 'live'>('pocket')
let permit = $state('')
let owner = $state('')
let filled = false
let saving = $state(false)

$effect(() => {
	const l = lead.data
	if (!l || filled) return
	filled = true
	unit = String(l.fields.unit ?? '')
	area = l.area && l.area !== '—' ? l.area : ''
	dealType = l.lead_type === 'seller' ? 'sale' : 'rent'
	priceAed = Number(l.fields.target_price_aed ?? l.fields.expected_rent_aed ?? 0) || null
	owner = l.name
})

async function submit(e: SubmitEvent) {
	e.preventDefault()
	const body = {
		unit,
		area,
		deal_type: dealType,
		price_aed: Number(priceAed) || 0,
		bedrooms: Number(bedrooms) || 0,
		size_sqft: Number(size) || 0,
		status,
		permit_number: permit,
		owner
	}
	saving = true
	const r = await write({
		call: () =>
			fromLeadId !== undefined
				? crm.leads.createListing(fromLeadId, body)
				: crm.listings.create(body),
		success: (res) => res.data.warning ?? `${res.data.listing.unit} created`,
		created: (res) => ({ kind: 'listing', id: res.data.listing.id })
	})
	saving = false
	if (r) {
		ui.assetTab = 'mine'
		ui.assetStatus = r.data.listing.status
		await goto(`/crm/assets?listing=${r.data.listing.id}`)
	}
}
</script>

<Panel kicker="New" title={fromLeadId !== undefined ? 'Listing from a lead' : 'Listing'}>
	{#if lead.data}
		<div class="notice">
			From {lead.data.name}'s lead. Starts as pocket; it goes live once Form A, title deed, photos
			and a permit are on file.
		</div>
	{/if}
	<form class="form" onsubmit={submit}>
		<div class="fld">
			<label for="nls-unit">Unit</label>
			<input id="nls-unit" placeholder="Building · 2BR" required bind:value={unit} />
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nls-area">Area</label>
				<input id="nls-area" required bind:value={area} />
			</div>
			<div class="fld">
				<label for="nls-type">Sale or rent</label>
				<select id="nls-type" bind:value={dealType}>
					<option value="sale">Sale</option>
					<option value="rent">Rent</option>
				</select>
			</div>
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nls-price">{dealType === 'rent' ? 'Rent a year (AED)' : 'Price (AED)'}</label>
				<MoneyInput id="nls-price" required bind:value={priceAed} />
			</div>
			<div class="fld">
				<label for="nls-beds">Bedrooms</label>
				<input id="nls-beds" type="number" min="0" max="10" bind:value={bedrooms} />
			</div>
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nls-size">Size (sqft)</label>
				<input id="nls-size" type="number" min="0" bind:value={size} />
			</div>
			<div class="fld">
				<label for="nls-status">Status</label>
				<select id="nls-status" bind:value={status} disabled={fromLeadId !== undefined}>
					<option value="pocket">Pocket</option>
					<option value="live">Live</option>
				</select>
			</div>
		</div>
		<div class="frow">
			<div class="fld">
				<label for="nls-permit">Trakheesi permit</label>
				<input id="nls-permit" placeholder="Needed to go live" bind:value={permit} />
			</div>
			<div class="fld">
				<label for="nls-owner">Owner</label>
				<input id="nls-owner" bind:value={owner} />
			</div>
		</div>
		<div><button type="submit" class="btn" disabled={saving}>Create listing</button></div>
	</form>
</Panel>
