<script lang="ts">
import type { Snippet } from 'svelte'

/**
 * A small icon that shows an info box while the pointer is over it or it has keyboard focus.
 * The box only explains; it has nothing to click. It floats above the page (fixed) so a table's
 * scroll box or a card can't cut it off, and closes on Escape or a scroll. `align` lines the box
 * up with the icon's left or right edge.
 */
let {
	label,
	glyph = 'ⓘ',
	tone = 'muted',
	align = 'right',
	children
}: {
	/** What the box explains, for screen readers ("Why 105 rows failed to match"). */
	label: string
	glyph?: string
	tone?: 'muted' | 'amber'
	align?: 'left' | 'right'
	children: Snippet
} = $props()

const boxId = $props.id()
let tip = $state<{ x: number; y: number } | null>(null)

function show(e: Event) {
	const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
	tip = { x: align === 'right' ? r.right : r.left, y: r.bottom + 6 }
}
const hide = () => (tip = null)

// Any scroll, in the page or an inner panel, moves the icon away from its box: close it.
$effect(() => {
	if (!tip) return
	const close = () => (tip = null)
	document.addEventListener('scroll', close, { capture: true, passive: true })
	return () => document.removeEventListener('scroll', close, { capture: true })
})
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && hide()} />

<button
	type="button"
	class={['itip', tone]}
	aria-label={label}
	aria-expanded={tip !== null}
	aria-describedby={tip ? boxId : undefined}
	onmouseenter={show}
	onmouseleave={hide}
	onfocus={show}
	onblur={hide}
	onclick={(e) => e.stopPropagation()}
>
	{glyph}
</button>
{#if tip}
	<div
		id={boxId}
		role="tooltip"
		class={['tipbox', align]}
		style:top="{tip.y}px"
		style:left="{tip.x}px"
	>
		{@render children()}
	</div>
{/if}

<style>
	/* Above a table row's stretched link overlay. */
	.itip {
		position: relative;
		z-index: 1;
		padding: 1px 5px;
		border: 0;
		border-radius: 6px;
		background: none;
		font-size: 14px;
		line-height: 1.2;
	}
	.itip.muted {
		color: var(--text2);
	}
	.itip.amber {
		color: var(--amber);
		font-size: 16px;
	}
	.itip {
		cursor: help;
	}
	.itip:hover,
	.itip[aria-expanded='true'] {
		background: var(--hover);
	}
	.itip.amber:hover,
	.itip.amber[aria-expanded='true'] {
		background: var(--amber-bg);
	}
	.tipbox {
		position: fixed;
		z-index: 50;
		width: min(22rem, calc(100vw - 32px));
		padding: 0.7rem 0.85rem;
		border: 1px solid var(--line-2);
		border-radius: 10px;
		background: var(--card);
		box-shadow: var(--shadow);
		color: var(--text);
		font-size: 13px;
		font-weight: 400;
		text-align: left;
		display: grid;
		gap: 0.4rem;
	}
	.tipbox.right {
		transform: translateX(-100%);
	}
</style>
