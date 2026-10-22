<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '$lib/crm/api'
import type { HandoutMode } from '$lib/crm/api/types'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import { crmQueries } from '$lib/crm/queries'
import { errorText, write } from '$lib/crm/write'

/**
 * Manager › Settings, only what the agent-side Inbox needs (scope, 7 Oct): the lead handout
 * setting with its changes log and Restore, and the lead stages per type (read only). The rest
 * of Manager view (agents table, waiting to assign, reports) is out of this build.
 */
const handout = createQuery(() => crmQueries.handout())

function setMode(mode: HandoutMode, label: string) {
	if (mode === handout.data?.mode) return
	write({
		call: () => crm.settings.setHandout(mode),
		success: `Lead handout: ${label.toLowerCase()}`
	})
}

function onkeydown(e: KeyboardEvent, i: number) {
	const opts = handout.data?.options
	if (
		!opts ||
		(e.key !== 'ArrowDown' &&
			e.key !== 'ArrowUp' &&
			e.key !== 'ArrowRight' &&
			e.key !== 'ArrowLeft')
	)
		return
	e.preventDefault()
	const n =
		(i + (e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1) + opts.length) % opts.length
	const group = (e.currentTarget as HTMLElement).parentElement
	group?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[n]?.focus()
}
</script>

<svelte:head><title>Manager view · CRM Beta</title></svelte:head>

<PageHeader
	title="Manager view"
	facts={handout.data
		? `Lead handout: ${handout.data.options.find((o) => o.id === handout.data?.mode)?.label.toLowerCase()} · default not decided`
		: ''}
/>

{#if handout.data}
	{@const h = handout.data}
	<div class="setgrid">
		<section class="card" aria-labelledby="ho-h">
			<div class="ch">
				<h2 id="ho-h">Lead handout <span class="facts">how a waiting enquiry gets an agent</span></h2>
			</div>
			<div class="cb">
				<div class="radios" role="radiogroup" aria-labelledby="ho-h">
					{#each h.options as o, i (o.id)}
						<button
							type="button"
							role="radio"
							aria-checked={h.mode === o.id}
							tabindex={h.mode === o.id ? 0 : -1}
							onclick={() => setMode(o.id, o.label)}
							onkeydown={(e) => onkeydown(e, i)}
						>
							<span class="rdot" aria-hidden="true"></span>
							<span><b>{o.label}</b><span class="d">{o.line}</span></span>
						</button>
					{/each}
				</div>
			</div>
			<div class="ch" style:border-top="1px solid var(--sep)">
				<h2>Changes <span class="facts">{h.log.length}</span></h2>
			</div>
			<div class="list">
				{#each h.log as e (e.id)}
					<div class="li">
						<span class="when mono small">{e.at}</span>
						<span><div class="t">{e.from_label} → {e.to_label}</div><div class="d">{e.who}</div></span>
						<button type="button" class="btn ghost sm" disabled={h.mode === e.from} onclick={() => setMode(e.from, e.from_label)}>
							Restore {e.from_label.toLowerCase()}
						</button>
					</div>
				{:else}
					<div class="nomatch">No changes yet.</div>
				{/each}
			</div>
		</section>

		<section class="card" aria-labelledby="st-h">
			<div class="ch">
				<h2 id="st-h">Lead stages <span class="facts">per lead type · read only</span></h2>
			</div>
			<div class="list">
				{#each h.stages as s (s.types)}
					<div class="li">
						<span></span>
						<span><div class="t">{s.types}</div><div class="d">{s.stages.join(' → ')}</div></span>
						{#if s.proposal}
							<span class="chip outline" title="Seller and landlord stage names were not set on the 6 Oct call.">Proposal</span>
						{:else}<span></span>{/if}
					</div>
				{/each}
			</div>
		</section>
	</div>
	{#if handout.isError}
		<ErrorLine bar message={errorText(handout.error)} onretry={() => handout.refetch()} />
	{/if}
{:else if handout.isError}
	<div class="card"><ErrorLine message={errorText(handout.error)} onretry={() => handout.refetch()} /></div>
{:else}
	<div class="card"><Skeleton rows={4} /></div>
{/if}
