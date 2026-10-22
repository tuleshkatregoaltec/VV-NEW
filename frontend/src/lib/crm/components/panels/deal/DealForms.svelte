<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import ChevronRight from 'lucide-svelte/icons/chevron-right'
import type { DealDetail } from '../../../api/types'
import { crmQueries } from '../../../queries'
import { toast } from '../../../stores/toast.svelte'
import Chip from '../../ui/Chip.svelte'
import QueryView from '../../ui/QueryView.svelte'

/** Sale: A, B, F, I. Rent: tenancy contract, Ejari. Preview filled from the deal; missing in red. */
let { deal }: { deal: DealDetail } = $props()

let code = $state<string | null>(null)
const form = createQuery(() => ({
	...crmQueries.dealForm(deal.id, code ?? ''),
	enabled: code !== null
}))

const badge = (c: string) => (c === 'tenancy' ? 'T' : c === 'ejari' ? 'E' : c)

async function copy(fields: { label: string; value: string | null }[]) {
	const text = fields.map((f) => `${f.label}: ${f.value ?? 'Missing'}`).join('\n')
	try {
		await navigator.clipboard.writeText(text)
		toast.show('Fields copied — paste into the Dubai Brokers app')
	} catch {
		toast.show('The browser blocked the copy — select the fields instead', { tone: 'error' })
	}
}
</script>

{#if deal.deal_type === 'sale'}
	<div class="notice">Filled from this deal — copy into DLD's Dubai Brokers app.</div>
{/if}

<div class="list boxed">
	{#each deal.forms as f (f.code)}
		<button type="button" class="li" aria-pressed={code === f.code} onclick={() => (code = f.code)}>
			<Chip tone="dark">{badge(f.code)}</Chip>
			<span>
				<div class="t">{f.title}</div>
				<div class="d">{f.note}</div>
			</span>
			<ChevronRight size={16} class="muted" />
		</button>
	{/each}
</div>

{#if code}
	<QueryView query={form} rows={4}>
		{#snippet children(p)}
			{@const missing = p.fields.filter((f) => !f.value).length}
			<div class="paper">
				<h4><span>{p.title}</span><span style="font-weight:400;color:#666">{p.deal_id}</span></h4>
				{#each p.fields as f (f.label)}
					<div class={['pf', !f.value && 'empty']}>
						<span>{f.label}</span><span>{f.value ?? 'Missing'}</span>
					</div>
				{/each}
				<div class="flex flex-wrap items-center gap-2">
					<button type="button" class="btn sm" onclick={() => copy(p.fields)}>Copy fields</button>
					<span class="foot">
						{missing ? `${missing} field${missing > 1 ? 's' : ''} missing` : 'All fields filled'}
					</span>
				</div>
			</div>
		{/snippet}
	</QueryView>
{/if}
