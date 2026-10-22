<script lang="ts">
import Building2 from 'lucide-svelte/icons/building-2'
import ChartLine from 'lucide-svelte/icons/chart-line'
import Grid2x2 from 'lucide-svelte/icons/grid-2x2'
import HardHat from 'lucide-svelte/icons/hard-hat'
import LayoutGrid from 'lucide-svelte/icons/layout-grid'
import List from 'lucide-svelte/icons/list'
import Menu from 'lucide-svelte/icons/menu'
import MessageSquare from 'lucide-svelte/icons/message-square'
import Sheet from 'lucide-svelte/icons/sheet'
import Target from 'lucide-svelte/icons/target'
import Workflow from 'lucide-svelte/icons/workflow'
import X from 'lucide-svelte/icons/x'
import { page } from '$app/state'
import ThemeToggle from '$lib/components/preferences/ThemeToggle.svelte'
import { currentUser } from '$lib/stores/user.svelte'
import ProfileDropdown from './ProfileDropdown.svelte'
import SectionSwitch from './SectionSwitch.svelte'

interface Item {
	href: string
	label: string
	icon: typeof LayoutGrid
	activePrefix?: string
}

const groups: { label: string; items: Item[] }[] = [
	{ label: 'Market', items: [{ href: '/dashboard', label: 'Market home', icon: LayoutGrid }] },
	{
		label: 'Sales and rents',
		items: [
			{
				href: '/transactions/sales',
				label: 'Transactions',
				icon: Sheet,
				activePrefix: '/transactions'
			},
			{ href: '/analytics', label: 'Analytics', icon: ChartLine },
			{ href: '/listings', label: 'Listings', icon: List }
		]
	},
	{
		label: 'Off-plan and land',
		items: [
			{ href: '/upcoming', label: 'Launch desk', icon: Building2 },
			{ href: '/developers', label: 'Developers', icon: HardHat },
			{ href: '/feasibility', label: 'Plots and feasibility', icon: Grid2x2 }
		]
	},
	{
		label: 'Tools',
		items: [
			{ href: '/radar', label: 'Radar map', icon: Target },
			{ href: '/chat', label: 'AI chat', icon: MessageSquare },
			{ href: '/workflows', label: 'Workflows', icon: Workflow }
		]
	}
]

let open = $state(false)

function isActive({ href, activePrefix = href }: Item) {
	const pathname = page.url.pathname
	return [href, activePrefix].some((p) => pathname === p || pathname.startsWith(`${p}/`))
}

const displayName = $derived(
	(() => {
		const user = currentUser.data
		if (!user) return ''
		return `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim() || user.email
	})()
)
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<!-- Below lg the sidebar slides over the page; this bar holds the button that opens it. -->
<header class="flex items-center justify-between border-b border-border bg-card px-4 py-2.5 lg:hidden">
	<a href="/dashboard" class="text-[17px] font-bold tracking-[-0.02em] text-fg-1" aria-label="Vitevue home">
		<span class="bg-[linear-gradient(110deg,#29576f_30%,#4cb8b1_70%)] bg-clip-text text-transparent">V</span>itevue
	</a>
	<button
		type="button"
		data-variant="secondary"
		data-size="icon"
		class="ui-button h-9 w-9"
		aria-label={open ? 'Close navigation menu' : 'Open navigation menu'}
		aria-expanded={open}
		aria-controls="market-sidebar"
		onclick={() => (open = !open)}
	>
		{#if open}<X size={16} strokeWidth={2} />{:else}<Menu size={16} strokeWidth={2} />{/if}
	</button>
</header>

{#if open}
	<button
		type="button"
		class="fixed inset-0 z-40 bg-overlay lg:hidden"
		aria-label="Close navigation menu"
		tabindex="-1"
		onclick={() => (open = false)}
	></button>
{/if}

<aside
	id="market-sidebar"
	class="fixed inset-y-0 left-0 z-50 flex w-[240px] flex-col border-r border-border bg-card transition-transform duration-200 lg:static lg:z-auto lg:w-[232px] lg:flex-none lg:translate-x-0 {open
		? 'translate-x-0'
		: '-translate-x-full'}"
	aria-label="Market"
>
	<div class="flex items-center gap-3 px-4 pb-2 pt-3.5">
		<a href="/dashboard" class="text-[17px] font-bold tracking-[-0.02em] text-fg-1" aria-label="Vitevue home">
			<span class="bg-[linear-gradient(110deg,#29576f_30%,#4cb8b1_70%)] bg-clip-text text-transparent">V</span>itevue
		</a>
		<div class="min-w-0 flex-1"><SectionSwitch current="market" /></div>
	</div>

	<nav class="min-h-0 flex-1 overflow-auto px-2 pb-2" aria-label="Market sections">
		{#each groups as g (g.label)}
			<div class="px-2 pb-1 pt-3 text-[11px] font-semibold text-fg-4">{g.label}</div>
			{#each g.items as it (it.href)}
				{@const active = isActive(it)}
				<a
					href={it.href}
					class="flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13.5px] transition-colors {active
						? 'bg-info-bg font-semibold text-info'
						: 'text-fg-2 hover:bg-panel hover:text-fg-1'}"
					aria-current={active ? 'page' : undefined}
					onclick={() => (open = false)}
				>
					<it.icon size={16} class="flex-none text-info" aria-hidden="true" />
					<span class="truncate">{it.label}</span>
				</a>
			{/each}
		{/each}
	</nav>

	<div class="flex items-center gap-2.5 border-t border-border px-3 py-2.5">
		<ProfileDropdown />
		<span class="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-fg-2">{displayName}</span>
		<ThemeToggle />
	</div>
</aside>
