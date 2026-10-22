<script lang="ts">
import { toast } from '../../stores/toast.svelte'
</script>

<div
	class={['toast', toast.visible && 'on', toast.tone === 'error' && 'error']}
	role={toast.tone === 'error' ? 'alert' : 'status'}
	aria-live={toast.tone === 'error' ? 'assertive' : 'polite'}
>
	<span>{toast.message}</span>
	{#if toast.action}
		<button type="button" onclick={() => toast.runAction()}>{toast.action.label}</button>
	{/if}
</div>
