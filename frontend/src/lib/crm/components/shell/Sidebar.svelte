<script lang="ts">
import Building2 from 'lucide-svelte/icons/building-2'
import CalendarDays from 'lucide-svelte/icons/calendar-days'
import ChartNoAxesColumn from 'lucide-svelte/icons/chart-no-axes-column'
import CircleDot from 'lucide-svelte/icons/circle-dot'
import FileText from 'lucide-svelte/icons/file-text'
import House from 'lucide-svelte/icons/house'
import Inbox from 'lucide-svelte/icons/inbox'
import LayoutGrid from 'lucide-svelte/icons/layout-grid'
import Send from 'lucide-svelte/icons/send'
import User from 'lucide-svelte/icons/user'
import { page } from '$app/state'
import SectionSwitch from '$lib/components/navigation/SectionSwitch.svelte'
import type { Me, NavCounts } from '../../api/types'
import { chrome } from '../../stores/chrome.svelte'

let { counts, me }: { counts: NavCounts | undefined; me: Me | undefined } = $props()

interface Item {
	href: string
	label: string
	icon: typeof House
	count: number | null
	countLabel?: string
}

/**
 * v2 sidebar (6 Oct): Agent · Home, Leads, Inbox, Matches, Owners, Deals, Calendar, Assistant; Assets
 * stays until the founders decide (open question 3). Manager · Manager view (8 Oct) opens the lead
 * handout setting the Inbox depends on; the rest of Manager view is out of this build.
 * Counts: new leads, inbox waiting for me, open deals, today's events.
 */
const groups = $derived<{ label: string; items: Item[] }[]>([
	{
		label: 'Agent',
		items: [
			{ href: '/crm', label: 'Home', icon: House, count: null },
			{
				href: '/crm/leads',
				label: 'Leads',
				icon: User,
				count: counts?.new_leads ?? null,
				countLabel: 'new'
			},
			{
				href: '/crm/inbox',
				label: 'Inbox',
				icon: Inbox,
				count: counts?.inbox ?? null,
				countLabel: 'open'
			},
			{ href: '/crm/matches', label: 'Matches', icon: CircleDot, count: null },
			{ href: '/crm/owners', label: 'Owners', icon: Building2, count: null },
			{
				href: '/crm/deals',
				label: 'Deals',
				icon: FileText,
				count: counts?.open_deals ?? null,
				countLabel: 'open'
			},
			{
				href: '/crm/calendar',
				label: 'Calendar',
				icon: CalendarDays,
				count: counts?.events_today ?? null,
				countLabel: 'today'
			},
			{ href: '/crm/assistant', label: 'Assistant', icon: Send, count: null },
			{ href: '/crm/assets', label: 'Assets', icon: LayoutGrid, count: null }
		]
	},
	{
		label: 'Manager',
		items: [
			{
				href: '/crm/settings',
				label: 'Manager view',
				icon: ChartNoAxesColumn,
				count: counts?.waiting ?? null,
				countLabel: 'waiting to assign'
			}
		]
	}
])

const path = $derived(page.url.pathname.replace(/\/$/, ''))
</script>

<aside class="sb" aria-label="CRM">
	<div class="sb-head">
		<a class="logo" href="/crm" aria-label="Vitevue CRM home"><i>V</i><span class="lt">itevue</span></a>
		<div class="sb-switch"><SectionSwitch current="crm" /></div>
	</div>
	<nav aria-label="CRM sections">
		{#each groups as g (g.label)}
			<div class="sb-grp">{g.label}</div>
			{#each g.items as it (it.href)}
				<a
					class="nav-i"
					href={it.href}
					title={it.label}
					aria-current={path === it.href ? 'page' : undefined}
					onclick={() => (chrome.sbOpen = false)}
				>
					<it.icon size={17} aria-hidden="true" />
					<span class="lbltxt">{it.label}</span>
					{#if it.count}<span class="ct" aria-label="{it.count} {it.countLabel}">{it.count}</span>{/if}
				</a>
			{/each}
		{/each}
	</nav>
	<div class="sb-foot">
		<span class="avatar" aria-hidden="true">{me?.initials ?? ''}</span>
		<span class="who"><b>{me?.name ?? ''}</b><br /><span class="muted">Agent{me?.areas.length ? ` · ${me.areas.join(', ')}` : ''}</span></span>
	</div>
</aside>
<button
	type="button"
	class="sb-scrim"
	aria-label="Close the sidebar"
	tabindex="-1"
	onclick={() => (chrome.sbOpen = false)}
></button>
