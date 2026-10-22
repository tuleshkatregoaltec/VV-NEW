<script lang="ts">
import X from 'lucide-svelte/icons/x'
import type { Snippet } from 'svelte'
import { panel } from '../../stores/panel.svelte'
import Tabs, { type TabItem } from './Tabs.svelte'

/** Side panel content: header (kicker, title, sub line, chips, close), tabs, body, footer. */
let {
	kicker = '',
	title,
	sub = '',
	subline,
	tags,
	tabs = [],
	tab = '',
	ontab,
	footer,
	children
}: {
	kicker?: string
	title: string
	sub?: string
	/** A sub line with links in it (e.g. the listing a portal lead enquired on). */
	subline?: Snippet
	tags?: Snippet
	tabs?: TabItem<string>[]
	tab?: string
	ontab?: (id: string) => void
	footer?: Snippet
	children: Snippet
} = $props()
</script>

<div class="dh">
	<div class="top">
		<div class="min-w-0">
			{#if kicker}<div class="lbl">{kicker}</div>{/if}
			<h2 id="crm-panel-title">{title}</h2>
			{#if subline}<div class="sub">{@render subline()}</div>{:else if sub}<div class="sub">{sub}</div>{/if}
		</div>
		<button type="button" class="x" aria-label="Close" onclick={() => panel.close()}>
			<X size={16} />
		</button>
	</div>
	{#if tags}<div class="tags">{@render tags()}</div>{/if}
</div>
{#if tabs.length}
	<Tabs
		variant="panel"
		items={tabs}
		value={tab}
		label="{title} sections"
		onselect={(id) => (ontab ? ontab(id) : panel.setTab(id))}
	/>
{/if}
<div class="db">{@render children()}</div>
{#if footer}<div class="df">{@render footer()}</div>{/if}
