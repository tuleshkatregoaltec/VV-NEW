<script lang="ts">
import type { Snippet } from 'svelte'
import { setContext } from 'svelte'
import { page } from '$app/state'
import MarketSidebar from '$lib/components/navigation/MarketSidebar.svelte'

let {
	children,
	stableScrollGutter = false
}: {
	children: Snippet
	stableScrollGutter?: boolean
} = $props()

let openDropdownId = $state<string | null>(null)
setContext('openDropdownId', {
	get value() {
		return openDropdownId
	},
	set value(v: string | null) {
		openDropdownId = v
	}
})

// The CRM layout renders its own sidebar (with the same CRM | Market switch).
const crm = $derived(page.url.pathname === '/crm' || page.url.pathname.startsWith('/crm/'))
</script>

<div class="theme-app flex h-screen flex-col bg-app text-fg-1 lg:flex-row">
	{#if !crm}
		<MarketSidebar />
	{/if}

	<main
		class="app-scroll-root min-h-0 min-w-0 flex-1 overflow-auto"
		data-stable-gutter={stableScrollGutter ? 'true' : undefined}
	>
		{@render children()}
	</main>
</div>
