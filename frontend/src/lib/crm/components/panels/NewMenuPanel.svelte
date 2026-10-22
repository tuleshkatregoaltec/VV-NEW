<script lang="ts">
import ChevronRight from 'lucide-svelte/icons/chevron-right'
import { type PanelState, panel } from '../../stores/panel.svelte'
import Panel from '../ui/Panel.svelte'

const items: { label: string; to: PanelState }[] = [
	{ label: 'Lead', to: { kind: 'new-lead' } },
	{ label: 'Listing', to: { kind: 'new-listing' } },
	{ label: 'Deal', to: { kind: 'new-deal' } },
	{ label: 'Task or viewing', to: { kind: 'new-task' } },
	{ label: 'Owner', to: { kind: 'new-owner' } },
	{ label: 'Import a file', to: { kind: 'new-import' } }
]
</script>

<Panel kicker="Create" title="New">
	<div class="list boxed">
		{#each items as item (item.label)}
			<button type="button" class="li" onclick={() => panel.open(item.to, { replace: true })}>
				<span class="dot" style:background="var(--teal)"></span>
				<span><div class="t">{item.label}</div></span>
				<ChevronRight size={16} class="muted" />
			</button>
		{/each}
	</div>
</Panel>
