<script lang="ts">
/** One line + Retry. `bar` = the thin version shown above data that is still on screen. */
let {
	message,
	onretry,
	bar = false
}: { message: string; onretry: () => void; bar?: boolean } = $props()
</script>

<div class={['errline', bar && 'bar']} role="alert">
	<span>{message}</span>
	<button type="button" class="btn ghost sm" onclick={onretry}>Retry</button>
</div>
