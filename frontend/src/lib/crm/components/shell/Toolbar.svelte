<script lang="ts">
import Bell from 'lucide-svelte/icons/bell'
import ChevronDown from 'lucide-svelte/icons/chevron-down'
import Moon from 'lucide-svelte/icons/moon'
import PanelLeft from 'lucide-svelte/icons/panel-left'
import Plus from 'lucide-svelte/icons/plus'
import Search from 'lucide-svelte/icons/search'
import Sun from 'lucide-svelte/icons/sun'
import { page } from '$app/state'
import { colorTheme } from '$lib/stores/preferences.svelte'
import { chrome } from '../../stores/chrome.svelte'
import { type PanelState, panel } from '../../stores/panel.svelte'

let { unread = 0 }: { unread?: number } = $props()
const dark = $derived(colorTheme.resolved === 'dark')
const path = $derived(page.url.pathname.replace(/\/$/, ''))

/**
 * ＋ New presets to the page: Owners → Owner, Assets → Listing, Deals → Deal, Calendar → Task, else
 * Lead. Home and the owner import pages have no ＋ New (an import's Upload button is the way in).
 */
const preset = $derived.by((): { label: string; to: PanelState } | null => {
	if (path === '/crm') return null
	if (path.includes('/owners/imports')) return null
	if (path.endsWith('/owners')) return { label: 'owner', to: { kind: 'new-owner' } }
	if (path.endsWith('/assets')) return { label: 'listing', to: { kind: 'new-listing' } }
	if (path.endsWith('/deals')) return { label: 'deal', to: { kind: 'new-deal' } }
	if (path.endsWith('/calendar')) return { label: 'task', to: { kind: 'new-task' } }
	return { label: 'lead', to: { kind: 'new-lead' } }
})

/** Laptop hides the sidebar; tablet expands the icon rail; phone opens it over the page. */
function toggleSidebar() {
	if (window.matchMedia('(max-width: 1100px)').matches) chrome.sbOpen = !chrome.sbOpen
	else chrome.sbHidden = !chrome.sbHidden
}

function onkeydown(e: KeyboardEvent) {
	const t = e.target as HTMLElement | null
	const typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
	if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) {
		e.preventDefault()
		panel.open({ kind: 'search' })
	}
	if (e.key === 'Escape' && chrome.sbOpen && !panel.current) chrome.sbOpen = false
}
</script>

<svelte:window {onkeydown} />

<header class="tb">
	<button
		type="button"
		class="ico"
		aria-label="Show or hide the sidebar"
		aria-expanded={chrome.sbOpen || !chrome.sbHidden}
		onclick={toggleSidebar}
	>
		<PanelLeft size={17} />
	</button>
	<div class="tb-title">
		<h1>{chrome.title}</h1>
		{#if chrome.facts || chrome.note}
			<div class="facts">{chrome.facts}{#if chrome.note}{' '}{@render chrome.note()}{/if}</div>
		{/if}
	</div>
	<div class="tb-tools">
		{@render chrome.tools?.()}
		<button
			type="button"
			class="ico"
			aria-label="Search (⌘K)"
			title="Search (⌘K)"
			onclick={() => panel.open({ kind: 'search' })}
		>
			<Search size={17} />
		</button>
		{#if preset}
		{@const p = preset}
		<span class="newgrp">
			<button type="button" class="btn newbtn" aria-label="New {p.label}" onclick={() => panel.open(p.to)}>
				<Plus size={16} /><span class="full">New {p.label}</span>
			</button>
			<button
				type="button"
				class="btn newmore"
				aria-label="Everything you can create"
				aria-haspopup="dialog"
				onclick={() => panel.open({ kind: 'new' })}
			>
				<ChevronDown size={15} />
			</button>
		</span>
		{/if}
		<button
			type="button"
			class="ico"
			aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
			onclick={() => panel.open({ kind: 'notifications' })}
		>
			<Bell size={17} />
			{#if unread}<span class="badge" aria-hidden="true">{unread}</span>{/if}
		</button>
		<button
			type="button"
			class="ico"
			aria-label={dark ? 'Switch to light' : 'Switch to dark'}
			aria-pressed={dark}
			onclick={() => colorTheme.toggle()}
		>
			{#if dark}<Sun size={17} />{:else}<Moon size={17} />{/if}
		</button>
	</div>
</header>
