<script lang="ts">
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
</script>

<svelte:head><title>Assistant · CRM Beta</title></svelte:head>

<PageHeader title="Assistant">
	{#snippet note()}
		<span class="chip outline" title="Being built, not a promise.">In build</span>
	{/snippet}
</PageHeader>

<section class="card">
	<div class="empty">The CRM assistant is being built and will appear here.</div>
</section>
