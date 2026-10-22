<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import type { Snippet } from 'svelte'
import { afterNavigate } from '$app/navigation'
import { page } from '$app/state'
import '$lib/crm/crm.css'
import AppFrame from '$lib/components/layout/AppFrame.svelte'
import Drawer from '$lib/crm/components/shell/Drawer.svelte'
import Sidebar from '$lib/crm/components/shell/Sidebar.svelte'
import Toast from '$lib/crm/components/shell/Toast.svelte'
import Toolbar from '$lib/crm/components/shell/Toolbar.svelte'
import { crmQueries } from '$lib/crm/queries'
import { chrome } from '$lib/crm/stores/chrome.svelte'
import { panel } from '$lib/crm/stores/panel.svelte'

let { children }: { children: Snippet } = $props()

const me = createQuery(() => crmQueries.me())
const counts = createQuery(() => crmQueries.counts())

// Deep links: ?lead=3, ?deal=DL-201&tab=documents, ?listing=L1, ?owner=O1
afterNavigate(({ to }) => {
	if (to?.url) panel.syncFromUrl(to.url)
})

/** Home and Inbox fit one screen on a laptop (no page scroll, v2). */
const fit = $derived(/^\/crm(\/inbox)?\/?$/.test(page.url.pathname))
</script>

<svelte:head>
	<title>CRM - Vitevue</title>
</svelte:head>

<AppFrame>
	<div class={['crm-root app', panel.current && 'panel-open', chrome.sbHidden && 'nosb', chrome.sbOpen && 'sbopen']}>
		<Sidebar counts={counts.data} me={me.data} />
		<div class="main">
			<Toolbar unread={counts.data?.unread_notifications ?? 0} />
			<!-- AppFrame already renders the page's <main>. -->
			<div class={['content', fit && 'fit']}>
				{@render children()}
			</div>
		</div>
		<Drawer />
		<Toast />
	</div>
</AppFrame>
