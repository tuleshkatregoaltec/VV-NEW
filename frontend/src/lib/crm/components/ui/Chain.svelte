<script module lang="ts">
/** Linked records: Listing → Lead → Deal → Portfolio. Every box opens that record. */
export interface ChainPart {
	k: string
	v: string
	current?: boolean
	none?: boolean
	onclick?: () => void
}
</script>

<script lang="ts">
	let { parts }: { parts: ChainPart[] } = $props()
</script>

<div class="chain">
	{#each parts as p, i (i)}
		{#if i > 0}<span class="arr" aria-hidden="true">→</span>{/if}
		{#if p.onclick && !p.current}
			<button type="button" class="lnk" onclick={p.onclick}>
				<span class="lk">{p.k}</span>{p.v}
			</button>
		{:else}
			<span class={['lnk', p.current && 'cur', p.none && 'none']} aria-current={p.current ? 'true' : undefined}>
				<span class="lk">{p.k}</span>{p.v}
			</span>
		{/if}
	{/each}
</div>
