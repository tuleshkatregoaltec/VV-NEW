<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ChevronRight from 'lucide-svelte/icons/chevron-right'
import { crm } from '../../api'
import type { NavCounts, Notification } from '../../api/types'
import { crmKeys, crmQueries } from '../../queries'
import { openRef, panel } from '../../stores/panel.svelte'
import { patchCache, write } from '../../write'
import Empty from '../ui/Empty.svelte'
import Panel from '../ui/Panel.svelte'
import QueryView from '../ui/QueryView.svelte'

/** Blue dot = unread; tap → record + mark read; "Mark all as read" with Undo. */
const list = createQuery(() => crmQueries.notifications())
const unread = $derived(list.data?.filter((n) => n.unread).length ?? 0)

function markLocal(ids: number[] | null) {
	const a = patchCache<Notification[]>(crmKeys.notifications(), (all) =>
		all.map((n) => (!ids || ids.includes(n.id) ? { ...n, unread: false } : n))
	)
	const b = patchCache<NavCounts>(crmKeys.counts(), (c) => ({
		...c,
		unread_notifications: ids ? Math.max(0, c.unread_notifications - ids.length) : 0
	}))
	return () => {
		a()
		b()
	}
}

/** Mark read through the one write path: the dot comes back and one line shows if it fails. */
function open(n: Notification) {
	if (n.unread) {
		void write({
			optimistic: () => markLocal([n.id]),
			call: () => crm.notifications.read([n.id]),
			success: null
		})
	}
	if (n.ref) openRef(n.ref)
}

async function readAll() {
	const r = await write({
		optimistic: () => markLocal(null),
		call: () => crm.notifications.read(),
		success: 'All read'
	})
	if (r) panel.closeAll()
}
</script>

<Panel kicker="Notifications" title="Notifications" sub="{unread} unread">
	<QueryView query={list} rows={4}>
		{#snippet children(items)}
			{#if items.length}
				<div class="list boxed">
					{#each items as n (n.id)}
						<button type="button" class="li" onclick={() => open(n)}>
							<span
								class="dot"
								style:background={n.unread ? 'var(--blue)' : 'transparent'}
								aria-label={n.unread ? 'Unread' : undefined}
							></span>
							<span><div class="t">{n.text}</div><div class="d">{n.detail}</div></span>
							<ChevronRight size={16} class="muted" />
						</button>
					{/each}
				</div>
			{:else}
				<Empty text="No notifications." />
			{/if}
		{/snippet}
	</QueryView>
	{#snippet footer()}
		<button type="button" class="btn ghost" disabled={!unread} onclick={readAll}>Mark all as read</button>
	{/snippet}
</Panel>
