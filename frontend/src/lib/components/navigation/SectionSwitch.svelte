<script lang="ts">
/** CRM | Market switch at the top of both sidebars. CRM is its own section, not part of Market. */
let { current }: { current: 'crm' | 'market' } = $props()

const sections = [
	{ id: 'crm', label: 'CRM', href: '/crm' },
	{ id: 'market', label: 'Market', href: '/dashboard' }
] as const
</script>

<nav class="section-switch flex rounded-lg bg-panel p-0.5" aria-label="Switch section">
	{#each sections as s (s.id)}
		<a
			href={s.href}
			class="flex-1 rounded-md px-2.5 py-1 text-center text-[12.5px] font-semibold transition-colors {current === s.id
				? 'bg-card text-fg-1 shadow-xs'
				: 'text-fg-4 hover:text-fg-1'}"
			aria-current={current === s.id ? 'page' : undefined}
		>
			{s.label}
		</a>
	{/each}
</nav>
