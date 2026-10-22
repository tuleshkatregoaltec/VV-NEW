<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ArrowLeft from 'lucide-svelte/icons/arrow-left'
import ArrowRight from 'lucide-svelte/icons/arrow-right'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import EventItem from '$lib/crm/components/ui/EventItem.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import QueryView from '$lib/crm/components/ui/QueryView.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import { EVENT_KIND_ORDER, EVENT_KINDS } from '$lib/crm/config'
import { openEvent } from '$lib/crm/events'
import {
	addMonths,
	dayLabel,
	dayOfMonth,
	monthGrid,
	monthOf,
	monthTitle,
	shortDate,
	WEEKDAYS
} from '$lib/crm/format'
import { crmQueries } from '$lib/crm/queries'
import { phone } from '$lib/crm/stores/media.svelte'
import { openLead, panel } from '$lib/crm/stores/panel.svelte'
import { ui } from '$lib/crm/stores/ui.svelte'
import { errorText } from '$lib/crm/write'

const me = createQuery(() => crmQueries.me())
const today = $derived(me.data?.today ?? '')
const month = $derived(ui.calMonth || (today ? monthOf(today) : ''))
const grid = $derived(month ? monthGrid(month) : [])
const events = createQuery(() => ({
	...crmQueries.events(grid[0] ?? '', grid[41] ?? ''),
	enabled: grid.length > 0
}))
const side = createQuery(() => crmQueries.calendarSide())

const inMonth = $derived((events.data ?? []).filter((e) => monthOf(e.date) === month))
const byDay = $derived.by(() => {
	const m = new Map<string, typeof inMonth>()
	for (const e of events.data ?? []) m.set(e.date, [...(m.get(e.date) ?? []), e])
	return m
})
const agendaDays = $derived([...new Set(inMonth.map((e) => e.date))].sort())

const shift = (n: number) => (ui.calMonth = addMonths(month, n))
</script>

<svelte:head><title>Calendar · CRM Beta</title></svelte:head>

<PageHeader
	title={month ? monthTitle(month) : 'Calendar'}
	facts={events.data
		? `${inMonth.length} event${inMonth.length === 1 ? '' : 's'} this month · click an event for details, a day to add a task`
		: ''}
>
	{#snippet right()}
		<div class="flex gap-2">
			<button type="button" class="btn ghost sm" aria-label="Previous month" onclick={() => shift(-1)}>
				<ArrowLeft size={14} />
			</button>
			<button type="button" class="btn ghost sm" onclick={() => (ui.calMonth = '')}>Today</button>
			<button type="button" class="btn ghost sm" aria-label="Next month" onclick={() => shift(1)}>
				<ArrowRight size={14} />
			</button>
		</div>
	{/snippet}
</PageHeader>

<div class="legend" aria-label="Legend">
	{#each EVENT_KIND_ORDER as k (k)}
		<span><span class="dot" style:background={EVENT_KINDS[k].color}></span>{EVENT_KINDS[k].label}</span>
	{/each}
</div>

<div class="cal-layout">
	<div class="card">
		{#if !events.data}
			{#if events.isError}
				<ErrorLine message={errorText(events.error)} onretry={() => events.refetch()} />
			{:else}
				<Skeleton rows={6} />
			{/if}
		{:else if phone.current}
			<div class="agenda">
				{#each agendaDays as date (date)}
					<div class={['dayh', date === today && 'today']}>
						{dayLabel(date)}{date === today ? ' · today' : ''}
					</div>
					<div class="list">
						{#each byDay.get(date) ?? [] as e (e.id)}<EventItem event={e} />{/each}
					</div>
				{:else}
					<Empty text="Nothing this month.">
						<button type="button" class="btn ghost sm" onclick={() => panel.open({ kind: 'new-task' })}>＋ Add</button>
					</Empty>
				{/each}
			</div>
		{:else}
			<div class="cal" role="grid" aria-label={monthTitle(month)}>
				<div class="contents" role="row">
					{#each WEEKDAYS as w (w)}<div class="h" role="columnheader">{w}</div>{/each}
				</div>
				{#each [0, 1, 2, 3, 4, 5] as week (week)}
					<div class="contents" role="row">
						{#each grid.slice(week * 7, week * 7 + 7) as date (date)}
							{@const evs = byDay.get(date) ?? []}
							<div
								class={['c', monthOf(date) !== month && 'out', date === today && 'today']}
								role="gridcell"
							>
								<button
									type="button"
									class="addday"
									aria-label="New task on {dayLabel(date)}"
									onclick={() => panel.open({ kind: 'new-task', date })}
								></button>
								<span class="dn">{dayOfMonth(date)}</span>
								{#each evs.slice(0, 3) as e (e.id)}
									<button
										type="button"
										class="ev"
										style:--e={EVENT_KINDS[e.kind].color}
										title={e.title}
										onclick={() => openEvent(e)}
									>
										{e.time ? `${e.time} ` : ''}{e.title}
									</button>
								{/each}
								{#if evs.length > 3}
									<button
										type="button"
										class="ev"
										style:--e="var(--line-2)"
										onclick={() => panel.open({ kind: 'day', date })}
									>
										+{evs.length - 3} more
									</button>
								{/if}
							</div>
						{/each}
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<div class="side">
		<section class="card" aria-labelledby="overdue-h">
			<div class="ch">
				<h2 id="overdue-h">Overdue</h2>
				{#if side.data}<Chip tone="red">{side.data.overdue.length}</Chip>{/if}
			</div>
			<QueryView query={side} rows={2}>
				{#snippet children(s)}
					<div class="list">
						{#each s.overdue as o (o.text)}
							<button type="button" class="li" onclick={() => openLead(o.lead_id)}>
								<span class="dot" style:background="var(--red)"></span>
								<span><div class="t">{o.text}</div><div class="d">{o.detail}</div></span>
								<span></span>
							</button>
						{:else}
							<Empty text="Nothing overdue." />
						{/each}
					</div>
				{/snippet}
			</QueryView>
		</section>
		<section class="card" aria-labelledby="deadlines-h">
			<div class="ch"><h2 id="deadlines-h">Deadlines</h2></div>
			<QueryView query={side} rows={3}>
				{#snippet children(s)}
					<div class="list">
						{#each s.deadlines as d (d.event.id)}
							<button type="button" class="li" onclick={() => openEvent(d.event)}>
								<span class="time">{shortDate(d.event.date)}</span>
								<span><div class="t">{d.event.title}</div><div class="d">{d.event.detail}</div></span>
								<Chip chip={d.chip} />
							</button>
						{:else}
							<Empty text="No deadlines ahead." />
						{/each}
					</div>
				{/snippet}
			</QueryView>
		</section>
	</div>
</div>
