<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { track } from '../../analytics'
import { crm } from '../../api'
import type { LeadStage } from '../../api/types'
import { GATE_LINE, LEAD_TYPES, SOURCES } from '../../config'
import { crmQueries } from '../../queries'
import { type LeadPanelTab, openListing, panel } from '../../stores/panel.svelte'
import { write } from '../../write'
import Chip from '../ui/Chip.svelte'
import LeadTypeChip from '../ui/LeadTypeChip.svelte'
import Panel from '../ui/Panel.svelte'
import PanelLoading from '../ui/PanelLoading.svelte'
import ScoreChip from '../ui/ScoreChip.svelte'
import StageChip from '../ui/StageChip.svelte'
import LeadBook from './lead/LeadBook.svelte'
import LeadConversation from './lead/LeadConversation.svelte'
import LeadEdit from './lead/LeadEdit.svelte'
import LeadHistory from './lead/LeadHistory.svelte'
import LeadMatches from './lead/LeadMatches.svelte'
import LeadNote from './lead/LeadNote.svelte'
import LeadOverview from './lead/LeadOverview.svelte'
import LeadStageForm from './lead/LeadStageForm.svelte'

let { id, tab = 'overview' }: { id: number; tab?: LeadPanelTab } = $props()

const lead = createQuery(() => crmQueries.lead(id))

const TABS = [
	{ id: 'overview', label: 'Overview' },
	{ id: 'conversation', label: 'Conversation' },
	{ id: 'matches', label: 'Matches' },
	{ id: 'history', label: 'History' }
]
const stageTab = $derived(tab.startsWith('stage:') ? (tab.slice(6) as LeadStage) : null)
const isForm = $derived(tab === 'edit' || tab === 'book' || tab === 'note' || stageTab !== null)

$effect(() => {
	track('lead_opened', { lead_id: id })
})

function logCall(name: string) {
	write({
		call: () => crm.leads.logCall(id),
		success: `Call logged on ${name}`
	})
}
</script>

{#if lead.data}
	{@const l = lead.data}
	{@const owner = l.lead_type === 'seller' || l.lead_type === 'landlord'}
	<Panel
		kicker="{LEAD_TYPES[l.lead_type].label} · {SOURCES[l.source].label}"
		title={l.name}
		tabs={TABS}
		tab={isForm ? '' : tab}
	>
		{#snippet subline()}
			{l.source_detail}{#if l.listing}{' · '}<button
					type="button"
					class="sublink"
					onclick={() => l.listing && openListing(l.listing.id)}>{l.listing.label}</button
				>{/if}{` · ${l.received_label} · ${l.agent ?? 'unassigned'}`}
		{/snippet}
		{#snippet tags()}
			<span
				class="dot"
				style:background={SOURCES[l.source].color}
				style:width="10px"
				style:height="10px"
				title={SOURCES[l.source].label}
			></span>
			<LeadTypeChip type={l.lead_type} />
			<ScoreChip score={l.score} />
			<StageChip stage={l.stage} />
			{#if l.off_plan}<Chip tone="outline">Off-plan</Chip>{/if}
			{#if l.gated}<Chip tone="amber" title={GATE_LINE}>No prior deal</Chip>{/if}
		{/snippet}

		{#if stageTab}
			{#key stageTab}<LeadStageForm lead={l} stage={stageTab} />{/key}
		{:else if tab === 'edit'}
			<LeadEdit lead={l} />
		{:else if tab === 'book'}
			<LeadBook lead={l} />
		{:else if tab === 'note'}
			<LeadNote lead={l} />
		{:else if tab === 'conversation'}
			<LeadConversation {id} />
		{:else if tab === 'matches'}
			<LeadMatches lead={l} />
		{:else if tab === 'history'}
			<LeadHistory lead={l} />
		{:else}
			<LeadOverview lead={l} />
		{/if}

		{#snippet footer()}
			{#if l.gated}
				<!-- DLD Circular 02-2026: no call, viewing or listing until a prior deal is on record. -->
				{#if l.owner_id}
					<button
						type="button"
						class="btn"
						onclick={() => l.owner_id && panel.open({ kind: 'add-record', ownerId: l.owner_id })}
					>
						Add record
					</button>
				{/if}
				<button type="button" class="btn ghost" onclick={() => panel.setTab('note')}>Add note</button>
				<span class="small muted">{GATE_LINE}</span>
			{:else}
			{#if !owner}
				<button type="button" class="btn" onclick={() => panel.setTab('book')}>Book viewing</button>
			{:else if !l.own_listing && l.stage !== 'won' && l.stage !== 'lost'}
				<button type="button" class="btn" onclick={() => panel.setTab('stage:listed')}>Move to Listed</button>
			{/if}
			<button type="button" class="btn ghost" onclick={() => panel.setTab('note')}>Add note</button>
			{#if owner}
				{#if l.own_listing}
					<button type="button" class="btn ghost" onclick={() => l.own_listing && openListing(l.own_listing.id)}>
						Open listing
					</button>
				{/if}
			{:else if l.stage !== 'won' && l.stage !== 'lost'}
				<button type="button" class="btn ghost" onclick={() => panel.setTab('stage:won')}>Move to deal</button>
			{/if}
			<button type="button" class="btn ghost" onclick={() => logCall(l.name)}>Log a call</button>
			{/if}
		{/snippet}
	</Panel>
{:else}
	<PanelLoading query={lead} kicker="Lead" />
{/if}
