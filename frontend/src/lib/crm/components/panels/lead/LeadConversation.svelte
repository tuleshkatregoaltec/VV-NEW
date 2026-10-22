<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crmQueries } from '../../../queries'
import QueryView from '../../ui/QueryView.svelte'

/** Messages read from the portal / WhatsApp. Read only in phase 1. */
let { id }: { id: number } = $props()

const conversation = createQuery(() => crmQueries.leadConversation(id))
</script>

<QueryView query={conversation} rows={3}>
	{#snippet children(messages)}
		<div class="thread" aria-label="Conversation">
			{#each messages as m, i (i)}
				<div class={['msg', m.from === 'agent' && 'me']}>
					<div class="w">{m.who} · {m.at}</div>
					{m.text}
				</div>
			{/each}
		</div>
		<div class="notice">Read only — replies go from your own phone or WhatsApp.</div>
	{/snippet}
</QueryView>
