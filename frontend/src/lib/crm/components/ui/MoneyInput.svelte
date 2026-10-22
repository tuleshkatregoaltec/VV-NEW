<script lang="ts">
import { untrack } from 'svelte'
import { fmt } from '../../format'

/**
 * Whole AED, any amount (95,500 is fine — no 1,000 steps). Shows separators when not being
 * edited; plain digits while typing. Bind `value` (number, or null when empty).
 */
let {
	id,
	// No fallback: the bound field may start out undefined (an empty form).
	value = $bindable(),
	required = false,
	placeholder = '',
	disabled = false
}: {
	id: string
	value?: number | null
	required?: boolean
	placeholder?: string
	disabled?: boolean
} = $props()

const show = (v: number | null | undefined) => (v === null || v === undefined ? '' : fmt(v))

let focused = $state(false)
let text = $state(untrack(() => show(value)))

$effect(() => {
	const v = value
	if (!untrack(() => focused)) text = show(v)
})

function oninput(e: Event & { currentTarget: HTMLInputElement }) {
	text = e.currentTarget.value
	const digits = text.replace(/[^\d]/g, '')
	value = digits ? Number(digits) : null
}
</script>

<input
	{id}
	type="text"
	inputmode="numeric"
	autocomplete="off"
	pattern="[0-9,\s]*"
	title="Whole AED, e.g. 95,500"
	{required}
	{placeholder}
	{disabled}
	value={text}
	{oninput}
	onfocus={() => {
		focused = true
		text = value === null || value === undefined ? '' : String(value)
	}}
	onblur={() => {
		focused = false
		text = show(value)
	}}
/>
