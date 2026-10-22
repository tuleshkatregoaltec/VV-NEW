<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '$lib/crm/api'
import type { ListingStatus, RenewalWindow } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import FilterMenu from '$lib/crm/components/ui/FilterMenu.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import QueryView from '$lib/crm/components/ui/QueryView.svelte'
import Seg from '$lib/crm/components/ui/Seg.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import { fmt, price, priceShort, short } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { openListing, panel } from '$lib/crm/stores/panel.svelte'
import { type AssetTab, ui } from '$lib/crm/stores/ui.svelte'
import { write } from '$lib/crm/write'

const me = createQuery(() => crmQueries.me())
const mineAll = createQuery(() => crmQueries.listings('mine'))
/** Renewals: every lease in the dataset, 50 at a time up to the 200 the endpoint serves. */
let renewalLimit = $state(50)
/** Area options: every area in the lease data (the list My areas uses), fetched once. */
const renewalAreas = createQuery(() => ({
	...crmQueries.feedAreas(),
	enabled: ui.assetTab === 'renewals'
}))
const renewals = createQuery(() => ({
	...crmQueries.renewals({
		area: ui.renewalArea,
		window: ui.renewalWindow,
		property_type: ui.renewalPtype,
		project: ui.renewalProject,
		building: ui.renewalBuilding,
		limit: renewalLimit
	}),
	enabled: ui.assetTab === 'renewals'
}))
const scope = $derived(ui.assetTab === 'company' ? 'company' : 'mine')
const listings = createQuery(() => ({
	...crmQueries.listings(scope, ui.assetStatus),
	enabled: ui.assetTab === 'mine' || ui.assetTab === 'company'
}))
const portfolio = createQuery(() => ({
	...crmQueries.portfolio(),
	enabled: ui.assetTab === 'portfolio'
}))
const market = createQuery(() => ({ ...crmQueries.market(), enabled: ui.assetTab === 'market' }))

const facts = $derived.by(() => {
	const c = mineAll.data?.counts
	if (!c) return ''
	const leases =
		renewals.data && !renewals.isPlaceholderData ? ` · ${fmt(renewals.data.total)} leases` : ''
	return `${c.live} live · ${c.pocket} pocket · ${c.expired} expired${leases}`
})

const TABS: { id: AssetTab; label: string }[] = [
	{ id: 'mine', label: 'My listings' },
	{ id: 'company', label: 'Company listings' },
	{ id: 'portfolio', label: 'Portfolio' },
	{ id: 'renewals', label: 'Renewals' },
	{ id: 'market', label: 'Market' }
]

const statusItems = $derived.by(() => {
	const c = listings.data?.counts
	const n = (s: ListingStatus) => (c ? ` · ${c[s]}` : '')
	const items: { id: ListingStatus; label: string }[] = [
		{ id: 'live', label: `Live${n('live')}` },
		{ id: 'pocket', label: `${scope === 'company' ? 'Company pocket' : 'Pocket'}${n('pocket')}` }
	]
	if (scope === 'mine') items.push({ id: 'expired', label: `Expired${n('expired')}` })
	return items
})

function selectTab(t: AssetTab) {
	ui.assetTab = t
	if (t === 'company' && ui.assetStatus === 'expired') ui.assetStatus = 'live'
}

const WINDOWS: { id: Exclude<RenewalWindow, ''>; label: string }[] = [
	{ id: 'vacant', label: 'Vacant · 60 days' },
	{ id: 'expired', label: 'Ended · 12 months' },
	{ id: 'expiring_30', label: 'Ends in 30 days' },
	{ id: 'expiring_60', label: 'Ends in 31–60 days' },
	{ id: 'expiring_90', label: 'Ends in 61–90 days' }
]

function filterRenewals(change: () => void) {
	change()
	renewalLimit = 50
}

/** Projects are listed for the picked area, buildings for the picked project. */
function pickRenewalArea(area: string) {
	filterRenewals(() => {
		ui.renewalArea = area
		ui.renewalProject = ''
		ui.renewalBuilding = ''
	})
}

function pickRenewalProject(project: string) {
	filterRenewals(() => {
		ui.renewalProject = project
		ui.renewalBuilding = ''
	})
}

const options = (list: string[] | undefined) => (list ?? []).map((x) => ({ id: x, label: x }))

function startRenewal(id: string, unit: string) {
	write({
		call: () => crm.renewals.start(id),
		success: `Renewal started — ${unit} · rent deal opened`
	})
}

const statusLabel = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
</script>

<svelte:head><title>Assets · CRM Beta</title></svelte:head>

<PageHeader title="Assets" {facts} />

<Tabs label="Assets" items={TABS} value={ui.assetTab} onselect={selectTab} />

{#if ui.assetTab === 'mine' || ui.assetTab === 'company'}
	<div class="card">
		<div class="filters">
			<Seg label="Status" items={statusItems} value={ui.assetStatus} onselect={(s) => (ui.assetStatus = s)} />
			{#if scope === 'mine'}
				<button type="button" class="btn sm ml-auto" onclick={() => panel.open({ kind: 'new-listing' })}>
					＋ Listing
				</button>
			{/if}
		</div>
		<QueryView query={listings} rows={4}>
			{#snippet children(data)}
				{#if data.items.length}
					<div class="lcards">
						{#each data.items as x (x.id)}
							<button type="button" class="lcard" onclick={() => openListing(x.id)}>
								<div class="img">
									<span class="chip">{x.deal_type === 'sale' ? 'Sale' : 'Rent'}</span>
									<span class="chip">{statusLabel(x.status)}</span>
								</div>
								<div class="body">
									<span class="pr">{priceShort(x.deal_type, x.price_aed)}</span>
									<span class="t">{x.unit}</span>
									<span class="d">
										{x.area} · {x.bedrooms ? `${x.bedrooms} bed` : 'Studio'} · {fmt(x.size_sqft)} sqft{scope ===
										'company'
											? ` · ${x.agent}`
											: ''}
									</span>
									<div class="foot">
										<Chip chip={x.permit_chip} />
										{#if x.status === 'live'}<Chip>{x.days_live} days · {x.enquiries} leads</Chip>{/if}
										<Chip chip={x.form_a_chip} />
									</div>
								</div>
							</button>
						{/each}
					</div>
				{:else}
					<Empty text="No {ui.assetStatus} listings{scope === 'company' ? ' from colleagues' : ''}.">
						{#if scope === 'mine'}
							<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'new-listing' })}>
								＋ Listing
							</button>
						{/if}
					</Empty>
				{/if}
			{/snippet}
		</QueryView>
	</div>
{:else if ui.assetTab === 'portfolio'}
	<QueryView query={portfolio} rows={5}>
		{#snippet children(p)}
			<div class="grid-3">
				<div class="kpi">
					<span class="lbl">Sales handled</span><span class="v">{p.totals.sales}</span>
					<span class="n">{short(p.totals.sales_value_aed)} in value</span>
				</div>
				<div class="kpi">
					<span class="lbl">Lets handled</span><span class="v">{p.totals.lets}</span>
					<span class="n">{fmt(p.totals.lets_value_aed)} a year in rent</span>
				</div>
				<div class="kpi green">
					<span class="lbl">Commission earned</span><span class="v">{short(p.totals.commission_aed)}</span>
					<span class="n">{p.totals.clients} clients</span>
				</div>
			</div>
			<div class="card">
				<div class="tblwrap">
					<table class="tbl">
						<thead>
							<tr><th>Client</th><th>What</th><th>Unit</th><th class="r">Value</th><th>Date</th><th>DLD / Ejari</th><th class="r">Commission</th></tr>
						</thead>
						<tbody>
							{#each p.clients as c (c.client)}
								{#each c.transactions as t, i (i)}
									<tr class="row">
										<td>
											<button type="button" class="rowlink" onclick={() => panel.open({ kind: 'client', client: c.client })}>
												{c.client}
											</button>
										</td>
										<td data-label="What">{t.what}</td>
										<td data-label="Unit">{t.unit}</td>
										<td class="r num" data-label="Value">{t.kind === 'rent' ? `${fmt(t.value_aed)}/yr` : short(t.value_aed)}</td>
										<td class="sub" data-label="Date">{t.date}</td>
										<td class="mono small" data-label="DLD / Ejari">{t.dld_or_ejari}</td>
										<td class="r num" data-label="Commission">{fmt(t.commission_aed)}</td>
									</tr>
								{/each}
							{:else}
								<tr><td colspan="7"><Empty text="No closed deals yet. They land here once transferred." /></td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/snippet}
	</QueryView>
{:else if ui.assetTab === 'renewals'}
	<div class="filters">
		<FilterMenu
			label="Lease end"
			allLabel="All lease ends"
			value={ui.renewalWindow}
			options={WINDOWS}
			onselect={(v) => filterRenewals(() => (ui.renewalWindow = v))}
		/>
		<FilterMenu
			label="Area"
			allLabel="All areas"
			value={ui.renewalArea}
			options={options(renewalAreas.data?.map((a) => a.area))}
			onselect={pickRenewalArea}
		/>
		<FilterMenu
			label="Type"
			allLabel="Any type"
			value={ui.renewalPtype}
			options={options(renewals.data?.property_types)}
			onselect={(v) => filterRenewals(() => (ui.renewalPtype = v))}
		/>
		{#if ui.renewalArea}
			<FilterMenu
				label="Project"
				allLabel="All projects"
				value={ui.renewalProject}
				options={options(renewals.data?.projects)}
				onselect={pickRenewalProject}
			/>
		{/if}
		{#if ui.renewalProject}
			<FilterMenu
				label="Building"
				allLabel="All buildings"
				value={ui.renewalBuilding}
				options={options(renewals.data?.buildings)}
				onselect={(v) => filterRenewals(() => (ui.renewalBuilding = v))}
			/>
		{/if}
		<span class="count" aria-live="polite">
			{renewals.isPlaceholderData
				? 'Updating…'
				: renewals.data
					? `${fmt(renewals.data.total)} lease${renewals.data.total === 1 ? '' : 's'}`
					: ''}
		</span>
	</div>
	<!-- Old rows stay dimmed while a new filter loads, so they never read as its answer. -->
	<div class="card" aria-busy={renewals.isPlaceholderData} style:opacity={renewals.isPlaceholderData ? 0.5 : undefined}>
		<QueryView query={renewals} rows={3}>
			{#snippet children(res)}
				<div class="tblwrap">
					<table class="tbl">
						<thead>
							<tr><th>Unit</th><th>Property</th><th class="r">Rent a year</th><th>Ends</th><th></th></tr>
						</thead>
						<tbody>
							{#each res.items as r (r.id)}
								<tr class="row">
									<td>
										<button type="button" class="rowlink" onclick={() => panel.open({ kind: 'renewal', id: r.id })}>
											{r.unit}
										</button>
										<div class="sub">{r.area}</div>
									</td>
									<td data-label="Property">{r.property}</td>
									<td class="r num" data-label="Rent a year">{r.rent_aed ? fmt(r.rent_aed) : ''}</td>
									<td data-label="Ends"><Chip chip={r.end_chip} /></td>
									<td class="r" data-label="">
										<button type="button" class="btn ghost sm" onclick={() => startRenewal(r.id, r.unit)}>
											Start renewal
										</button>
									</td>
								</tr>
							{:else}
								<tr><td colspan="5"><Empty text="No leases match these filters." /></td></tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if res.items.length < res.total && renewalLimit < 200}
					<div class="cb">
						<button type="button" class="btn ghost sm" onclick={() => (renewalLimit += 50)}>
							Show more · {fmt(res.items.length)} of {fmt(res.total)}
						</button>
					</div>
				{:else if res.items.length < res.total}
					<div class="cb small muted">
						Showing the first {fmt(res.items.length)} of {fmt(res.total)}. Pick an area or a lease end to see the rest.
					</div>
				{/if}
			{/snippet}
		</QueryView>
	</div>
{:else}
	<section class="card" aria-labelledby="market-h">
		<div class="ch"><h2 id="market-h">Other agencies' listings in your areas</h2></div>
		<QueryView query={market} rows={5}>
			{#snippet children(list)}
				<div class="tblwrap">
					<table class="tbl">
						<thead><tr><th>Unit</th><th class="r">Price</th><th>Portal</th><th>Against matching sales</th></tr></thead>
						<tbody>
							{#each list as m (m.id)}
								<tr class="row">
									<td>
										<button type="button" class="rowlink" onclick={() => panel.open({ kind: 'market', id: m.id })}>
											{m.watch ? '★ ' : ''}{m.unit}
										</button>
										<div class="sub">{m.area} · {m.days_listed} days listed</div>
									</td>
									<td class="r num" data-label="Price">{price(m.deal_type, m.price_aed).replace('AED ', '')}</td>
									<td data-label="Portal">{m.portal}</td>
									<td data-label="Against matching sales"><Chip chip={m.discount_chip} /></td>
								</tr>
							{:else}
								<tr><td colspan="4"><Empty text="No portal listings in your areas right now." /></td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/snippet}
		</QueryView>
	</section>
{/if}
