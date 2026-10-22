<script lang="ts">
export let step = ''
export let total = 0
export let label = ''
</script>

<div
	class="inline-flex items-center gap-3 rounded-sm border border-border bg-panel px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-fg-4"
>
	<span
		>Step {step}{#if total}
			/ {total}{/if}</span
	>
	{#if label}
		<span class="h-1 w-1 rounded-full bg-fg-5"></span>
		<span class="tracking-[0.18em]">{label}</span>
	{/if}
</div>
