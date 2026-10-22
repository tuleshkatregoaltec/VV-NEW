<script lang="ts" generics="T extends string">
let {
	items,
	value,
	onselect,
	label
}: {
	items: { id: T; label: string }[]
	value: T
	onselect: (id: T) => void
	label: string
} = $props()
</script>

<div class="seg" role="group" aria-label={label}>
	{#each items as item (item.id)}
		<button type="button" aria-pressed={value === item.id} onclick={() => onselect(item.id)}>
			{item.label}
		</button>
	{/each}
</div>
