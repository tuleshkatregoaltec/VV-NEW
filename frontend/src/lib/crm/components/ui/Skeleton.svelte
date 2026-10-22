<script lang="ts">
let { rows = 4 }: { rows?: number } = $props()
</script>

<div class="skel" aria-busy="true" aria-label="Loading">
	{#each Array.from({ length: rows }, (_, i) => i) as i (i)}
		<div><i></i><i></i><i></i></div>
	{/each}
</div>
