<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Search from 'lucide-svelte/icons/search'
import { track } from '../../analytics'
import { crmQueries } from '../../queries'
import { openDeal, openListing, openOwner } from '../../stores/panel.svelte'
import { errorText } from '../../write'
import Chip from '../ui/Chip.svelte'
import Empty from '../ui/Empty.svelte'
import ErrorLine from '../ui/ErrorLine.svelte'
import LeadItem from '../ui/LeadItem.svelte'
import Panel from '../ui/Panel.svelte'

/** One input (2+ letters); results grouped: Leads, Deals, Listings, Owners, Units. */
let text = $state('')
let q = $state('')
let input: HTMLInputElement | undefined = $state()
let timer: ReturnType<typeof setTimeout> | undefined

$effect(() => {
	input?.focus()
})

$effect(() => {
	const v = text.trim()
	clearTimeout(timer)
	timer = setTimeout(() => {
		q = v
		if (v.length >= 2) track('search_used')
	}, 180)
	return () => clearTimeout(timer)
})

const results = createQuery(() => crmQueries.search(q))
const r = $derived(q.length >= 2 ? results.data : undefined)
const total = $derived(
	r ? r.leads.length + r.deals.length + r.listings.length + r.owners.length + r.units.length : 0
)
</script>

<Panel kicker="Search" title="Search the CRM">
	<label class="search">
		<Search size={15} />
		<span class="sr-only">Search leads, deals, listings and owners</span>
		<input
			bind:this={input}
			bind:value={text}
			placeholder="Name, phone, unit, deal number…"
			autocomplete="off"
		/>
	</label>

	{#if text.trim().length < 2}
		<Empty text={text ? 'Type at least two letters.' : 'Leads, deals, listings, owners and units.'} />
	{:else if results.isError}
		<ErrorLine message={errorText(results.error)} onretry={() => results.refetch()} />
	{:else if !r}
		<Empty text="Searching…" />
	{:else if !total}
		<Empty text="Nothing matches “{q}”." />
	{:else}
		{#if r.leads.length}
			<section class="sec">
				<h3>Leads</h3>
				<div class="list boxed">
					{#each r.leads as l (l.id)}<LeadItem lead={l} />{/each}
				</div>
			</section>
		{/if}
		{#if r.deals.length}
			<section class="sec">
				<h3>Deals</h3>
				<div class="list boxed">
					{#each r.deals as d (d.id)}
						<button type="button" class="li" onclick={() => openDeal(d.id)}>
							<span class="mono small">{d.id}</span>
							<span><div class="t">{d.unit}</div><div class="d">{d.client}</div></span>
							<Chip>{d.stage}</Chip>
						</button>
					{/each}
				</div>
			</section>
		{/if}
		{#if r.listings.length}
			<section class="sec">
				<h3>Listings</h3>
				<div class="list boxed">
					{#each r.listings as x (x.id)}
						<button type="button" class="li" onclick={() => openListing(x.id)}>
							<span class="dot" style:background="var(--teal)"></span>
							<span><div class="t">{x.unit}</div><div class="d">{x.area} · {x.status}</div></span>
							<span></span>
						</button>
					{/each}
				</div>
			</section>
		{/if}
		{#if r.owners.length}
			<section class="sec">
				<h3>Owners</h3>
				<div class="list boxed">
					{#each r.owners as o (o.id)}
						<button type="button" class="li" onclick={() => openOwner(o.id)}>
							<span class="dot" style:background="var(--l-vv)"></span>
							<span><div class="t">{o.name}</div><div class="d">{o.units.length} unit{o.units.length === 1 ? '' : 's'}{o.units[0] ? ` · ${o.units[0]}` : ''}</div></span>
							<span></span>
						</button>
					{/each}
				</div>
			</section>
		{/if}
		{#if r.units.length}
			<section class="sec">
				<h3>Units</h3>
				<div class="list boxed">
					{#each r.units as u (u.owner_id + u.unit)}
						<button type="button" class="li" onclick={() => openOwner(u.owner_id)}>
							<span class="dot" style:background="var(--l-vv)"></span>
							<span><div class="t">{u.unit}</div><div class="d">{u.area} · {u.owner_name}</div></span>
							<span></span>
						</button>
					{/each}
				</div>
			</section>
		{/if}
	{/if}
</Panel>
