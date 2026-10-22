<script lang="ts">
import type { Snippet } from 'svelte'

let {
	k,
	v = '',
	o = '',
	wide = false,
	children
}: { k: string; v?: string | number; o?: string; wide?: boolean; children?: Snippet } = $props()
</script>

<div class={['kvi', wide && 'wide']}>
	<div class="k">{k}</div>
	<div class="v">{#if children}{@render children()}{:else}{v}{/if}</div>
	{#if o}<div class="o">{o}</div>{/if}
</div>
