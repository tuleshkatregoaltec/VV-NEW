<script lang="ts">
import { type PanelState, panel } from '../../stores/panel.svelte'
import PanelHost from '../panels/PanelHost.svelte'

/**
 * The side panel: opens over the current screen (full screen under 700px). Escape, the close
 * button and a click outside close it. Content stays while it slides out.
 */
let shown = $state<PanelState | null>(null)
let aside: HTMLElement | undefined = $state()
let wasOpen = false

$effect(() => {
	const current = panel.current
	if (current) {
		shown = current
		return
	}
	const t = setTimeout(() => {
		if (!panel.current) shown = null
	}, 300)
	return () => clearTimeout(t)
})

$effect(() => {
	const open = Boolean(panel.current)
	if (open && !wasOpen) {
		requestAnimationFrame(() => {
			if (aside && !aside.contains(document.activeElement)) aside.focus()
		})
	}
	wasOpen = open
})

function onkeydown(e: KeyboardEvent) {
	if (e.key === 'Escape' && panel.current) {
		e.preventDefault()
		panel.close()
	}
}
</script>

<svelte:window {onkeydown} />

<div class={['scrim', panel.current && 'on']} aria-hidden="true" onclick={() => panel.closeAll()}></div>
<div
	bind:this={aside}
	class={['drawer', panel.current && 'open']}
	role="dialog"
	aria-modal="true"
	aria-labelledby="crm-panel-title"
	tabindex="-1"
	inert={!panel.current}
>
	{#if shown}<PanelHost state={shown} />{/if}
</div>
