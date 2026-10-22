<script lang="ts" generics="T extends string">
import Check from 'lucide-svelte/icons/check'
import ChevronDown from 'lucide-svelte/icons/chevron-down'
import { tick } from 'svelte'

/**
 * Filter dropdown as an in-page menu (7 Oct): native <select> menus do not show in a macOS
 * screen share. ↓ on the button opens it; ↑ ↓ Home End move; Enter/Space pick; Escape closes and
 * returns focus; Tab or a click outside closes. The button reads "Label Value" when a filter is on.
 */
let {
	label,
	value,
	options,
	onselect,
	allLabel,
	alwaysOn = false
}: {
	label: string
	value: T | ''
	options: { id: T; label: string }[]
	onselect: (v: T | '') => void
	/** The "no filter" row ("All sources"). Omit when a value is always set. */
	allLabel?: string
	/** The value is a choice, not a filter: never show it as "on". */
	alwaysOn?: boolean
} = $props()

let open = $state(false)
let btn: HTMLButtonElement | undefined = $state()
let menu: HTMLDivElement | undefined = $state()

const rows = $derived<{ id: T | ''; label: string }[]>(
	allLabel !== undefined ? [{ id: '', label: allLabel }, ...options] : options
)
const current = $derived(rows.find((r) => r.id === value))
const on = $derived(!alwaysOn && value !== '')

async function show(focusIndex = -1) {
	open = true
	await tick()
	const items = menu?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]')
	if (!items?.length) return
	const i =
		focusIndex >= 0
			? focusIndex
			: Math.max(
					0,
					rows.findIndex((r) => r.id === value)
				)
	items[Math.min(i, items.length - 1)]?.focus()
}

function close(focusButton = false) {
	open = false
	if (focusButton) btn?.focus()
}

function pick(id: T | '') {
	onselect(id)
	close(true)
}

function onbuttonkey(e: KeyboardEvent) {
	if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
		e.preventDefault()
		show()
	}
}

function onmenukey(e: KeyboardEvent) {
	const items = [...(menu?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])]
	const i = items.indexOf(document.activeElement as HTMLButtonElement)
	const go = (n: number) => items[(n + items.length) % items.length]?.focus()
	if (e.key === 'ArrowDown') go(i + 1)
	else if (e.key === 'ArrowUp') go(i - 1)
	else if (e.key === 'Home') go(0)
	else if (e.key === 'End') go(items.length - 1)
	else if (e.key === 'Escape') {
		e.stopPropagation()
		close(true)
	} else if (e.key === 'Tab') {
		close()
		return
	} else return
	e.preventDefault()
}

function onwindowclick(e: MouseEvent) {
	if (!open) return
	const t = e.target as Node
	if (!menu?.contains(t) && !btn?.contains(t)) close()
}
</script>

<svelte:window onclick={onwindowclick} />

<span class="fmenu-wrap">
	<button
		bind:this={btn}
		type="button"
		class={['btn ghost sm fbtn', on && 'on']}
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label="{label}: {current?.label ?? label}"
		onclick={() => (open ? close() : show())}
		onkeydown={onbuttonkey}
	>
		{#if on}<span class="fl">{label}</span>{/if}
		<span class="fv">{current?.label ?? label}</span>
		<ChevronDown size={14} />
	</button>
	{#if open}
		<!-- svelte-ignore a11y_interactive_supports_focus -->
		<div bind:this={menu} class="fmenu" role="menu" aria-label={label} tabindex="-1" onkeydown={onmenukey}>
			{#each rows as r (r.id)}
				<button
					type="button"
					role="menuitemradio"
					aria-checked={r.id === value}
					tabindex="-1"
					onclick={() => pick(r.id)}
				>
					<span class="ck">{#if r.id === value}<Check size={15} />{/if}</span>
					{r.label}
				</button>
			{/each}
		</div>
	{/if}
</span>
