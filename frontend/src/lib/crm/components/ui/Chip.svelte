<script lang="ts">
import type { Snippet } from 'svelte'
import type { Chip, Tone } from '../../api/types'

let {
	chip,
	tone = 'neutral',
	title,
	children
}: { chip?: Chip | null; tone?: Tone; title?: string; children?: Snippet } = $props()

const t = $derived(chip?.tone ?? tone)
</script>

{#if chip || children}
	<span class={['chip', t !== 'neutral' && t]} {title}>
		{#if children}{@render children()}{:else}{chip?.label}{/if}
	</span>
{/if}
