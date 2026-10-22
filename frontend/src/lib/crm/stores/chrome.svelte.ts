/**
 * What the toolbar shows for the current screen: the section name, one facts line and the
 * screen's own tools (v2: "Page header = section name + one facts line"). Each page sets it
 * through <PageHeader>; the layout's toolbar renders it. Also holds the sidebar state.
 */
import type { Snippet } from 'svelte'

export const chrome = $state({
	title: '',
	facts: '',
	/** Optional snippet after the facts text (an "In build" chip). */
	note: null as Snippet | null,
	/** Screen tools left of the global ones (List / Board, month arrows). */
	tools: null as Snippet | null,
	/** Laptop: sidebar hidden with the toolbar button. */
	sbHidden: false,
	/** Tablet: icon rail expanded; phone: sidebar open over the page. */
	sbOpen: false
})
