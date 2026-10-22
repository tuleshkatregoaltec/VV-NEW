<script lang="ts">
import type { Snippet } from 'svelte'
import { chrome } from '../../stores/chrome.svelte'

/**
 * Hands the section name, facts line and screen tools to the toolbar (rendered by the layout).
 * Renders nothing in place.
 */
let {
	title,
	facts = '',
	note,
	right
}: { title: string; facts?: string; note?: Snippet; right?: Snippet } = $props()

$effect.pre(() => {
	chrome.title = title
	chrome.facts = facts
	chrome.note = note ?? null
	chrome.tools = right ?? null
})
</script>
