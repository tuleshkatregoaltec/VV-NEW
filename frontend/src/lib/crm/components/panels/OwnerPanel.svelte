<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { goto } from '$app/navigation'
import { crm } from '../../api'
import type { OwnerUnit } from '../../api/types'
import { GATE_LINE, ORIGIN_TONE, stageOf } from '../../config'
import { aed, fmt, fullDate, short } from '../../format'
import { matchLines } from '../../ownerMatch'
import { crmQueries } from '../../queries'
import { openLead, openRef, panel } from '../../stores/panel.svelte'
import { write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Empty from '../ui/Empty.svelte'
import Kv from '../ui/Kv.svelte'
import LeadItem from '../ui/LeadItem.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'

/**
 * Owner: summary, record of relationship (deals, listings and past transactions with this owner
 * on the agency's file), one expandable row per unit (unit number, type, beds, size, last sale,
 * rent and its dates, market range, rent by year, transactions), who fits, the call log.
 * DLD Circular 02-2026: without a prior deal on record there is no call, Create lead or market
 * update — only "Add a prior deal record before contacting" and Add record.
 */
let { id, unit }: { id: string; unit?: string } = $props()
const owner = createQuery(() => crmQueries.owner(id))

let note = $state('')
let open = $state<Record<string, boolean>>({})

// Opened from an import report row: open that unit once the owner has loaded.
let shownUnit = $state('')
$effect(() => {
	const units = owner.data?.unit_rows
	const want = (unit ?? '').trim().toUpperCase()
	if (!units || !want || shownUnit === `${id}|${want}`) return
	shownUnit = `${id}|${want}`
	const hits = units.filter((u) => u.number.trim().toUpperCase() === want)
	if (hits.length) open = { ...open, ...Object.fromEntries(hits.map((u) => [u.key, true])) }
})

function logCall(e: SubmitEvent) {
	e.preventDefault()
	const text = note.trim()
	if (!text) return
	write({
		call: () => crm.owners.addNote(id, text),
		success: 'Call logged',
		after: () => {
			note = ''
		}
	})
}

function marketUpdate(name: string) {
	write({ call: () => crm.owners.sendMarketUpdate(id), success: `Market update for ${name} sent` })
}

const MARK = { match: '✓', differs: '✗', info: '•', none: '–' } as const

const beds = (u: OwnerUnit) =>
	u.bedrooms === null ? '' : u.bedrooms ? `${u.bedrooms}BR` : 'Studio'
const maxRent = (u: OwnerUnit) => Math.max(1, ...u.rent_history.map((h) => h.aed))
</script>

{#if owner.data}
	{@const o = owner.data}
	{@const allOpen = o.unit_rows.length > 0 && o.unit_rows.every((u) => open[u.key])}
	<Panel kicker="Owner" title={o.name} sub="{o.units.length} unit{o.units.length === 1 ? '' : 's'} · {o.source_label}">
		{#snippet tags()}
			{#if o.prior_deal}
				<Chip tone="green">Prior deal on file</Chip>
			{:else}
				<Chip tone="amber">No prior deal on file</Chip>
			{/if}
			{#if o.tag}<Chip tone="blue">{o.tag}</Chip>{/if}
			{#if o.lead}<Chip>Lead · {stageOf(o.lead.stage).label}{o.lead.id === null ? ` · ${o.lead.agent}` : ''}</Chip>{/if}
			{#if o.market_update_due && o.prior_deal}<Chip tone="amber">Update due</Chip>{/if}
		{/snippet}

		<div class="kv">
			<Kv k="Next event" v={o.next_event} o="{o.next_event_label} · from DLD / Ejari" wide />
			<Kv k="Last contact" v={o.last_contact} />
			<Kv k="Relationship" v={o.mine ? 'Mine' : 'Company'} />
			<Kv k="Source" v={o.source_label} o={o.on_file ? "on your agency's file" : 'your own contact'} wide />
			<!-- Which uploaded file(s) this owner came from; each opens that import's report. -->
			<Kv k="Imported from" wide>
				{#if o.imported_from.length}
					<ul class="srcs">
						{#each o.imported_from as f (f.name)}
							<li>
								{#if f.importId !== null}
									{@const importId = f.importId}
									<button
										type="button"
										class="srclink"
										onclick={async () => {
											panel.closeAll()
											await goto(`/crm/owners/imports/${importId}`)
										}}>{f.name}</button
									>
								{:else}
									<span>{f.name}</span>
								{/if}
								<span class="srcmeta">
									{[f.file, f.date && fullDate(f.date), `${fmt(f.units)} unit${f.units === 1 ? '' : 's'}`]
										.filter(Boolean)
										.join(' · ')}
								</span>
							</li>
						{/each}
					</ul>
				{:else}
					—
				{/if}
			</Kv>
		</div>

		{#if !o.prior_deal}
			<div class="note gate">
				<span>{GATE_LINE}.</span>
				<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'add-record', ownerId: id })}>
					Add record
				</button>
			</div>
		{/if}

		<section class="sec">
			<h3>
				Record of relationship
				<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'add-record', ownerId: id })}>
					Add record
				</button>
			</h3>
			{#if o.relationship.length}
				<div class="list boxed rec">
					{#each o.relationship as r, i (i)}
						{@const ref = r.ref}
						{#if ref}
							<button type="button" class="li" onclick={() => openRef(ref)}>
								<Chip tone={r.transaction ? 'green' : 'neutral'}>{r.kind}</Chip>
								<span><div class="t">{r.text}</div>{#if r.date}<div class="d">{fullDate(r.date)}</div>{/if}</span>
								<span class="small muted">{r.transaction ? 'Counts' : 'Does not count'}</span>
							</button>
						{:else}
							<div class="li">
								<Chip tone="green">{r.kind}</Chip>
								<span>
									<div class="t">{r.text}</div>
									<div class="d">{r.date ? fullDate(r.date) : ''}{r.added ? ' · added by hand' : ''}</div>
								</span>
								<span class="small muted">Counts</span>
							</div>
						{/if}
					{/each}
				</div>
			{/if}
			<div class="small {o.prior_deal ? '' : 'muted'}">
				{#if o.prior_deal}
					Prior deal on file: {o.relationship.find((r) => r.transaction)?.text}
				{:else}
					No deal, listing or past transaction with this owner on file.
				{/if}
			</div>
		</section>

		{#if o.lead}
			<section class="sec">
				<h3>Lead</h3>
				{#if o.lead.id !== null}
					{@const leadId = o.lead.id}
					<div class="list boxed">
						<button type="button" class="li" onclick={() => openLead(leadId)}>
							<span class="dot" style:background="var(--l-vv)"></span>
							<span><div class="t">Lead · {stageOf(o.lead.stage).label}</div><div class="d">Open the lead</div></span>
							<span></span>
						</button>
					</div>
				{:else}
					<div class="small muted">
						A lead with {o.lead.agent === 'Unassigned' ? 'no agent yet' : o.lead.agent} · {stageOf(o.lead.stage).label}
					</div>
				{/if}
			</section>
		{/if}

		<section class="sec">
			<h3>
				Units · {o.unit_rows.length}
				{#if o.unit_rows.length > 1}
					<button
						type="button"
						class="btn ghost sm"
						onclick={() => {
							const next = !allOpen
							open = Object.fromEntries(o.unit_rows.map((u) => [u.key, next]))
						}}
					>
						{allOpen ? 'Close all' : 'Open all'}
					</button>
				{/if}
			</h3>
			{#if o.unit_rows.length}
				<div class="docs">
					{#each o.unit_rows as u (u.key)}
						{@const isOpen = Boolean(open[u.key])}
						<div class="urow">
							<button
								type="button"
								aria-expanded={isOpen}
								aria-controls="unit-{u.key}"
								onclick={() => (open = { ...open, [u.key]: !isOpen })}
							>
								<span>
									<span class="no">#{u.number}</span> · {u.building}{beds(u) ? ` · ${beds(u)}` : ''}
									<span class="small muted">· {u.area}</span>
									{#if u.owned_from || u.owned_to}
										<span class="small muted">
											· {[u.owned_from && fullDate(u.owned_from), u.owned_to && `sold ${fullDate(u.owned_to)}`]
												.filter(Boolean)
												.join(' – ')}
										</span>
									{/if}
								</span>
								<Chip chip={u.status_chip} />
								<span class="muted" aria-hidden="true">{isOpen ? '−' : '＋'}</span>
							</button>
							{#if isOpen}
								<div class="ub" id="unit-{u.key}">
									<div class="kv">
										<Kv k="Type" v="{u.type}{u.size_sqft ? ` · ${fmt(u.size_sqft)} sqft` : ''}" />
										<Kv k="Imported from" v={u.source || '—'} o="file · sheet and row" wide />
										<Kv k="Last sale" v={u.bought_aed ? `${aed(u.bought_aed)} · ${u.bought_year}` : '—'} o="DLD" />
										<Kv
											k="Rent now"
											v={u.rent ? `${aed(u.rent.aed)} a year` : '—'}
											o={u.rent ? `${fullDate(u.rent.from)} – ${fullDate(u.rent.to)} · Ejari` : u.status}
										/>
										<Kv
											k="Market range"
											v={u.market
												? `Sale ${short(u.market.sale[0])}–${short(u.market.sale[1])} · rent ${short(u.market.rent[0])}–${short(u.market.rent[1])}`
												: '—'}
											o="Vitevue estimate"
										/>
									</div>
									{#if u.check}
										{@const c = u.check}
										<!-- What the owner file said, what DLD has, and how the import matched them. -->
										<div class="check">
											<div class="cbox">
												<div class="lbl">Imported owner record</div>
												<div class="kv">
													<Kv k="Transaction date" v={c.imported.date ? fullDate(c.imported.date) : '—'} />
													<Kv k="Sale price" v={c.imported.aed ? aed(c.imported.aed) : '—'} />
													<Kv k="Property type" v={c.imported.type || 'Not supplied'} />
													<Kv k="Unit" v={c.imported.unit || 'Not supplied'} />
													<Kv k="Role" v={c.imported.role || 'Not supplied'} />
												</div>
											</div>
											<div class="cbox dld">
												<div class="lbl">DLD record{c.dld.market ? ` · ${c.dld.market}` : ''}</div>
												<div class="kv">
													<Kv
														k="Unit"
														v={c.dld.unit
															? [c.dld.unit, c.dld.building].filter(Boolean).join(' · ')
															: '—'}
														o={c.dld.area || undefined}
														wide
													/>
													<Kv k="Latest recorded sale" v={c.dld.date ? fullDate(c.dld.date) : '—'} />
													<Kv k="Recorded price" v={c.dld.aed ? aed(c.dld.aed) : '—'} />
													<Kv
														k="Type / bedrooms"
														v={[c.dld.type, c.dld.bedrooms].filter(Boolean).join(' · ') || '—'}
													/>
													<Kv k="Recorded size" v={c.dld.size_sqft ? `${fmt(c.dld.size_sqft)} sqft` : '—'} />
													<Kv k="Built-up area" v={c.dld.built_up_sqft ? `${fmt(c.dld.built_up_sqft)} sqft` : '—'} />
													<Kv k="Price / sqft" v={c.dld.aed_per_sqft ? `${aed(c.dld.aed_per_sqft)} / sqft` : '—'} />
												</div>
											</div>
										</div>
										<div class="result">
											<Chip chip={c.result.chip} />
											<span class="small muted">{c.result.confidence}% confidence</span>
											{#if c.lease}
												<span class="small muted">
													· Lease ends {fullDate(c.lease.end)}{c.lease.aed ? ` · ${aed(c.lease.aed)} a year` : ''}
												</span>
											{/if}
										</div>
										{#if c.result.reason}<div class="small">{c.result.reason}</div>{/if}
										<div>
											<div class="lbl">Matching</div>
											<div class="tblwrap boxed">
												<table class="tbl mtbl">
													<thead>
														<tr><th>Checked</th><th>File</th><th>DLD</th><th>Result</th></tr>
													</thead>
													<tbody>
														{#each matchLines(c) as m (m.label)}
															<tr>
																<td>{m.label}</td>
																<td data-label="File">{m.file}</td>
																<td data-label="DLD">{m.dld}</td>
																<td data-label="Result" class={m.result}>
																	<span aria-hidden="true">{MARK[m.result]}</span>
																	{m.note}
																</td>
															</tr>
														{/each}
													</tbody>
												</table>
											</div>
										</div>
									{/if}
									{#if u.rent_history.length}
										<div>
											<div class="lbl">Rent by year</div>
											<div class="uhist" role="img" aria-label="Rent by year: {u.rent_history.map((h) => `${h.year} AED ${fmt(h.aed)}`).join(', ')}">
												{#each u.rent_history as h (h.year)}
													<div title="{h.year}: {aed(h.aed)}">
														<span>{short(h.aed)}</span>
														<i style:height="{Math.round((h.aed / maxRent(u)) * 34)}px"></i>
														<span>{h.year}</span>
													</div>
												{/each}
											</div>
										</div>
									{/if}
									{#if u.transactions.length}
										<div>
											<div class="lbl">Transactions</div>
											<div class="hist">
												{#each u.transactions as t, i (i)}
													<div>
														<span class="when">{t.when}</span>
														<span>{t.what}{t.aed ? ` · ${aed(t.aed)}` : ''}</span>
														<Chip>DLD</Chip>
													</div>
												{/each}
											</div>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{:else}
				<Empty text="No units yet. Checking DLD and Ejari." />
			{/if}
		</section>

		<section class="sec">
			<h3>Buyers and tenants who fit these units · {o.matched.length}</h3>
			{#if o.matched.length}
				<div class="list boxed">
					{#each o.matched as l (l.id)}
						<LeadItem lead={l} />
					{/each}
				</div>
			{:else}
				<div class="small muted">None of your leads fit these units yet.</div>
			{/if}
		</section>

		{#if o.notes.length}
			<section class="sec">
				<h3>Log</h3>
				<div class="hist">
					{#each o.notes as n (n.id)}
						<div>
							<span class="when">{n.at}</span>
							<span>{n.text}</span>
							<Chip tone={ORIGIN_TONE[n.origin]}>{n.origin}</Chip>
						</div>
					{/each}
				</div>
			</section>
		{/if}

		{#if o.prior_deal}
			<form class="form" onsubmit={logCall}>
				<div class="fld">
					<label for="owner-call">Log a call</label>
					<input id="owner-call" placeholder="What was said, what is next" required bind:value={note} />
				</div>
				<div><button type="submit" class="btn ghost sm" disabled={!note.trim()}>Save</button></div>
			</form>
		{/if}

		{#snippet footer()}
			{#if o.lead && o.lead.id !== null}
				{@const leadId = o.lead.id}
				<button type="button" class="btn" onclick={() => openLead(leadId)}>Open lead</button>
			{:else if o.create_lead}
				<button type="button" class="btn" onclick={() => panel.open({ kind: 'owner-lead', id })}>
					Create lead
				</button>
			{:else if !o.prior_deal}
				<button type="button" class="btn" onclick={() => panel.open({ kind: 'add-record', ownerId: id })}>
					Add record
				</button>
			{/if}
			<button type="button" class="btn ghost" onclick={() => panel.open({ kind: 'owner-report', id })}>
				Property report
			</button>
			{#if o.prior_deal}
				<button type="button" class="btn ghost" onclick={() => marketUpdate(o.name)}>
					Send market update
				</button>
			{/if}
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={owner} kicker="Owner" />
{/if}

<style>
	.check {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: 0.6rem;
	}
	.cbox {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 0.6rem 0.7rem;
		background: var(--card-2);
	}
	.cbox.dld {
		border-color: var(--blue);
		background: var(--blue-bg);
	}
	.mtbl td {
		font-size: 12.5px;
	}
	.mtbl .match {
		color: var(--green);
	}
	.mtbl .differs {
		color: var(--red);
	}
	.mtbl .none {
		color: var(--text2);
	}
	.result {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
	}
	.srcs {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.35rem;
	}
	.srcs li {
		display: grid;
	}
	.srclink {
		justify-self: start;
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		font: inherit;
		cursor: pointer;
		text-align: left;
	}
	.srcmeta {
		font-size: 12px;
		color: var(--text2);
	}
</style>
