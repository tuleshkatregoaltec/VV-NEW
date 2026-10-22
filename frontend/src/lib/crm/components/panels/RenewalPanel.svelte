<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '../../api'
import type { RenewalDetail } from '../../api/types'
import { aed, fullDate } from '../../format'
import { crmQueries } from '../../queries'
import { write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Kv from '../ui/Kv.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/**
 * One lease: the unit, lease dates and rent, the property history (registered leases and
 * sales) and the owner. Owner fields stay blank until the agent types them in or an owner
 * import matches the unit; nothing is guessed.
 */
let { id }: { id: string } = $props()
const renewal = createQuery(() => crmQueries.renewal(id))

let editing = $state(false)
let owner = $state({ name: '', phone: '', email: '' })
let saving = $state(false)

function editOwner(r: RenewalDetail) {
	owner = { name: r.owner?.name ?? '', phone: r.owner?.phone ?? '', email: r.owner?.email ?? '' }
	editing = true
}

async function saveOwner(e: SubmitEvent) {
	e.preventDefault()
	saving = true
	const r = await write({ call: () => crm.renewals.saveOwner(id, owner), success: 'Owner saved' })
	saving = false
	if (r) editing = false
}

function start(r: RenewalDetail) {
	write({ call: () => crm.renewals.start(r.id), success: `Renewal started — ${r.unit}` })
}
</script>

{#if renewal.data}
	{@const r = renewal.data}
	<Panel kicker="Renewal" title={r.unit} sub="{r.area} · {r.property}">
		{#snippet tags()}<Chip chip={r.end_chip} />{/snippet}
		<div class="kv">
			<Kv k="Lease" v="{fullDate(r.lease_start)} – {fullDate(r.lease_end)}" />
			<Kv k="Rent a year" v={r.rent_aed ? aed(r.rent_aed) : ''} />
			<Kv
				k="Last purchase"
				v={r.last_purchase?.price_aed ? aed(r.last_purchase.price_aed) : ''}
				o={r.last_purchase ? fullDate(r.last_purchase.date) : ''}
			/>
			<Kv k="Gross yield" v={r.gross_yield_pct != null ? `${r.gross_yield_pct}%` : ''} />
		</div>

		<section class="sec">
			<h3>
				Owner
				{#if !editing}
					<button type="button" class="btn ghost sm" onclick={() => editOwner(r)}>
						{r.owner ? 'Edit' : 'Add owner'}
					</button>
				{/if}
			</h3>
			{#if editing}
				<form class="form" onsubmit={saveOwner}>
					<div class="fld">
						<label for="rn-owner-name">Name</label>
						<input id="rn-owner-name" autocomplete="off" bind:value={owner.name} />
					</div>
					<div class="fld">
						<label for="rn-owner-phone">Phone</label>
						<input id="rn-owner-phone" type="tel" autocomplete="off" bind:value={owner.phone} />
					</div>
					<div class="fld">
						<label for="rn-owner-email">Email</label>
						<input id="rn-owner-email" type="email" autocomplete="off" bind:value={owner.email} />
					</div>
					<div class="form-actions">
						<button type="submit" class="btn" disabled={saving}>Save owner</button>
						<button type="button" class="btn ghost" onclick={() => (editing = false)}>Cancel</button>
					</div>
				</form>
			{:else}
				<div class="kv">
					<Kv k="Name" v={r.owner?.name ?? ''} o={r.owner?.source ?? 'Not identified yet'} />
					<Kv k="Phone" v={r.owner?.phone ?? ''} />
					<Kv k="Email" v={r.owner?.email ?? ''} />
				</div>
			{/if}
		</section>

		<section class="sec">
			<h3>Why now</h3>
			<p>{r.reason}</p>
			<p class="small muted">{r.action}</p>
		</section>

		<section class="sec">
			<h3>Property history</h3>
			{#if r.history.length}
				<div class="hist">
					{#each r.history as e (e.id)}
						<div>
							<span class="when">{fullDate(e.date)}{e.end_date ? ` – ${fullDate(e.end_date)}` : ''}</span>
							<span>
								<!-- A lease's description already states its rent; a sale's does not state its price. -->
								{e.title}{e.kind === 'sale' && e.amount_aed ? ` · ${aed(e.amount_aed)}` : ''}
								<span class="small muted">{e.detail}</span>
							</span>
							<Chip tone={e.kind === 'sale' ? 'blue' : 'neutral'}>{e.source}</Chip>
						</div>
					{/each}
				</div>
				<p class="small muted">{r.match_explanation}</p>
			{:else}
				<p class="small muted">No other registered leases or sales for this unit.</p>
			{/if}
		</section>

		{#snippet footer()}
			<button type="button" class="btn" onclick={() => start(r)}>Start renewal</button>
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={renewal} kicker="Renewal" />
{/if}
