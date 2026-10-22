<script lang="ts">
import type { Snippet } from 'svelte'

let { text, children }: { text: string; children?: Snippet } = $props()
</script>

<div class="empty">
	<span>{text}</span>
	{@render children?.()}
</div>
