<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import DealBoard from '$lib/crm/components/ui/DealBoard.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import EventItem from '$lib/crm/components/ui/EventItem.svelte'
import LeadItem from '$lib/crm/components/ui/LeadItem.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import ScoreChip from '$lib/crm/components/ui/ScoreChip.svelte'
import Seg from '$lib/crm/components/ui/Seg.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import StageChip from '$lib/crm/components/ui/StageChip.svelte'
import Tabs from '$lib/crm/components/ui/Tabs.svelte'
import WeekStrip from '$lib/crm/components/ui/WeekStrip.svelte'
import { GATE_LINE, LEAD_TYPES } from '$lib/crm/config'
import { dayLabel, fmt, fullDate, plural, short } from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { openLead, openRef, panel } from '$lib/crm/stores/panel.svelte'
import { ui } from '$lib/crm/stores/ui.svelte'
import { errorText } from '$lib/crm/write'

const me = createQuery(() => crmQueries.me())
const home = createQuery(() => crmQueries.home(ui.weekDay))
const selected = $derived(ui.weekDay || home.data?.date || '')
const facts = $derived.by(() => {
	const h = home.data
	if (!h) return ''
	return [
		fullDate(h.date),
		`${plural(h.facts.events_today, 'event')} today`,
		`${h.facts.conveyancing} deals in conveyancing`,
		`${h.facts.permits_under_14} permits or Form A under 14 days`
	].join(' · ')
})
</script>

<svelte:head><title>Home · CRM Beta</title></svelte:head>

<PageHeader title="Home" {facts} />

{#if home.data}
	{@const h = home.data}
	{@const c = h.counters}
	<div class="grid-4 kpis">
		<a class="kpi" href="/crm/calendar">
			<span class="lbl">Viewings this week</span>
			<span class="v">{c.viewings_week.count}</span>
			<span class="n">{c.viewings_week.next ?? 'none left this week'}</span>
		</a>
		<a
			class={['kpi', c.expiring_14.count && 'amber']}
			href="/crm/assets"
			onclick={() => (ui.assetTab = 'mine')}
		>
			<span class="lbl">Expiring in 14 days</span>
			<span class="v">{c.expiring_14.count}</span>
			<span class="n">{plural(c.expiring_14.permits, 'permit')} · {c.expiring_14.form_a} Form A</span>
		</a>
		<a class="kpi" href="/crm/assets" onclick={() => (ui.assetTab = 'market')}>
			<span class="lbl">Market · {c.market?.area ?? 'pick an area'}</span>
			<span class="v">
				{#if c.market}AED {fmt(c.market.psf_aed)}<small> /sqft</small>{:else}—{/if}
			</span>
			<span class="n">
				{c.market ? `rent median ${short(c.market.rent_median_aed)} · yield ${c.market.yield_pct}%` : ''}
			</span>
		</a>
		<a class="kpi green" href="/crm/deals" onclick={() => (ui.dealTab = 'commission')}>
			<span class="lbl">Commission</span>
			<span class="v">{short(c.commission.received_aed)}</span>
			<span class="n">received · {short(c.commission.due_aed)} due</span>
		</a>
	</div>
	{#if home.isError}
		<ErrorLine bar message={errorText(home.error)} onretry={() => home.refetch()} />
	{/if}

	<div class="homefit">
		<section class="card hf-next" aria-labelledby="h-next">
			<div class="ch">
				<h2 id="h-next">Up next · {dayLabel(selected)}</h2>
				<a class="btn ghost sm" href="/crm/calendar">Calendar</a>
			</div>
			<WeekStrip week={h.week} {selected} today={h.date} onselect={(d) => (ui.weekDay = d)} />
			<div class="list scroll">
				{#each h.day as e (e.id)}
					<EventItem event={e} />
				{:else}
					<Empty text="Nothing booked this day." />
				{/each}
			</div>
		</section>

		<section class="card hf-conv" aria-labelledby="h-conv">
			<div class="ch">
				<h2 id="h-conv">Conveyancing</h2>
				<div class="form-actions">
					<a class="btn ghost sm" href="/crm/deals" onclick={() => (ui.dealTab = 'conveyancing')}>
						Full view
					</a>
					<Seg
						label="Deal type"
						items={[
							{ id: 'sale', label: 'Sale' },
							{ id: 'rent', label: 'Rent' }
						]}
						value={ui.convType}
						onselect={(t) => (ui.convType = t)}
					/>
				</div>
			</div>
			<div class="scroll"><DealBoard deals={h.conveyancing} type={ui.convType} /></div>
		</section>

		<section class="card hf-leads" aria-labelledby="h-leads">
			<div class="ch">
				<h2 id="h-leads">New leads · {h.new_leads.length}</h2>
				<a class="btn ghost sm" href="/crm/leads">All leads</a>
			</div>
			<div class="list scroll">
				{#each h.new_leads as l (l.id)}
					<LeadItem lead={l} bar />
				{:else}
					<Empty text="No new leads. New enquiries land in the Inbox first.">
						<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'new-lead' })}>＋ Lead</button>
					</Empty>
				{/each}
			</div>
		</section>

		<section class="card hf-todo" aria-label="To do">
			<Tabs
				variant="card"
				label="To do"
				items={[
					{ id: 'gaps', label: 'Gaps', count: h.gaps.length },
					{ id: 'file', label: 'Owner file', count: h.file_leads.length }
				]}
				value={ui.homeTodo}
				onselect={(t) => (ui.homeTodo = t)}
			/>
			<div class="list scroll">
				{#if ui.homeTodo === 'gaps'}
					{#each h.gaps as g, i (i)}
						<button type="button" class="li" onclick={() => openRef({ kind: g.ref_type, id: g.ref_id })}>
							<span class="dot" style:background="var(--amber)"></span>
							<span><div class="t">{g.text}</div><div class="d">{g.detail}</div></span>
							<Chip>Open</Chip>
						</button>
					{:else}
						<Empty text="No gaps. Every record is current." />
					{/each}
				{:else}
					{#each h.file_leads as l (l.id)}
						<button type="button" class="li" onclick={() => openLead(l.id)}>
							<span class="dot" style:background="var(--l-vv)"></span>
							<span>
								<div class="t">{l.name}</div>
								<div class="d">{l.source_detail} · {LEAD_TYPES[l.lead_type].label}</div>
							</span>
							<span class="row-chips">
								{#if l.gated}<Chip tone="amber" title={GATE_LINE}>No prior deal</Chip>{/if}
								<StageChip stage={l.stage} />
							</span>
						</button>
					{:else}
						<Empty
							text="No owner-file leads waiting. Owners on your agency's file arrive here at 30, 60 and 90 days to lease end."
						/>
					{/each}
				{/if}
			</div>
		</section>
	</div>
{:else if home.isError}
	<div class="card"><ErrorLine message={errorText(home.error)} onretry={() => home.refetch()} /></div>
{:else}
	<div class="grid-4 kpis">
		{#each [1, 2, 3, 4] as i (i)}<div class="kpi"><Skeleton rows={1} /></div>{/each}
	</div>
	<div class="homefit">
		<div class="card hf-next"><Skeleton rows={6} /></div>
		<div class="card hf-conv"><Skeleton rows={3} /></div>
		<div class="card hf-leads"><Skeleton rows={3} /></div>
		<div class="card hf-todo"><Skeleton rows={3} /></div>
	</div>
{/if}
