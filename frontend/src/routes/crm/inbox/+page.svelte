<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import { crm } from '$lib/crm/api'
import type { InboxFilter, InboxItem } from '$lib/crm/api/types'
import Chip from '$lib/crm/components/ui/Chip.svelte'
import Empty from '$lib/crm/components/ui/Empty.svelte'
import ErrorLine from '$lib/crm/components/ui/ErrorLine.svelte'
import PageHeader from '$lib/crm/components/ui/PageHeader.svelte'
import Skeleton from '$lib/crm/components/ui/Skeleton.svelte'
import { crmQueries } from '$lib/crm/queries'
import { openLead } from '$lib/crm/stores/panel.svelte'
import { ui } from '$lib/crm/stores/ui.svelte'
import { errorText, write } from '$lib/crm/write'

/**
 * Inbox (6 Oct, 04:25–06:10): every enquiry in one list, colour line = source. Detail:
 * conversation, owner check, wants, priority, suggested agent, who asked for it and the handout
 * mode. What Ask does depends on the agency's handout setting (Manager › Settings).
 */
const inbox = createQuery(() => crmQueries.inbox(ui.inboxFilter))
const items = $derived(inbox.data?.items ?? [])
const sel = $derived<InboxItem | null>(items.find((x) => x.id === ui.inboxSel) ?? items[0] ?? null)
const sources = $derived(inbox.data?.sources ?? [])

const LINE: Record<InboxItem['line'], string> = {
	in: 'var(--l-in)',
	vv: 'var(--l-vv)',
	own: 'var(--l-own)',
	a2a: 'var(--l-a2a)'
}
const FILTERS: [InboxFilter, string][] = [
	['all', 'All'],
	['mine', 'Assigned to me'],
	['waiting', 'Waiting'],
	['dismissed', 'Not real']
]

const facts = $derived(
	inbox.data
		? `${inbox.data.today} today · ${inbox.data.waiting} waiting to assign · Handout: ${inbox.data.handout_label.toLowerCase()} · colour = source`
		: ''
)

function act(
	call: () => Promise<{ data: InboxItem; undo_token: string | null }>,
	success: (x: InboxItem) => string
) {
	write({ call, success: (r) => success(r.data) })
}
</script>

<svelte:head><title>Inbox · CRM Beta</title></svelte:head>

<PageHeader title="Inbox" {facts} />

{#if inbox.data}
	<div class="inbox">
		<section class="card ib-list" aria-label="Enquiries">
			<div class="ch">
				<div class="seg" role="tablist" aria-label="Filter">
					{#each FILTERS as [id, label] (id)}
						<button type="button" role="tab" aria-selected={ui.inboxFilter === id} onclick={() => (ui.inboxFilter = id)}>
							{label}
						</button>
					{/each}
				</div>
			</div>
			<div class="list scroll">
				{#each items as x (x.id)}
					<button
						type="button"
						class={['li cl ib-row', sel?.id === x.id && 'sel']}
						style:--c={LINE[x.line]}
						aria-pressed={sel?.id === x.id}
						onclick={() => (ui.inboxSel = x.id)}
					>
						<span>
							<span class="ib-top"><span class="t">{x.name}</span><span class="d">{x.at_label}</span></span>
							<span class="d ib-text"><span class="srcdot" style:--c={LINE[x.line]}>{x.source}</span> · {x.text}</span>
							<span class="ib-top">
								<span class="row-chips" style:justify-content="flex-start">
									<Chip chip={x.status_chip} />
									<span class="d">{x.agent}</span>
								</span>
							</span>
						</span>
					</button>
				{:else}
					<Empty
						text={ui.inboxFilter === 'waiting'
							? 'Nothing waiting. Every enquiry has an agent.'
							: 'Nothing here. New enquiries appear as they arrive.'}
					/>
				{/each}
			</div>
		</section>

		<div class="ib-right">
			{#if sel}
				<section class="card" aria-label="Enquiry">
					<div class="ch cl" style:--c={LINE[sel.line]}>
						<div>
							<h2>{sel.name}</h2>
							<div class="small muted">{sel.source} · {sel.at_label} · {sel.ago} · {sel.agent}</div>
						</div>
						<Chip chip={sel.status_chip} />
					</div>
					<div class="cb ib-body">
						<div class="ib-conv">
							{#each sel.conversation as c, i (i)}
								<div class={['cmsg', c.from === 'agent' && 'me']}>
									<div class="d">{c.from === 'agent' ? c.who : `${sel.name} · ${sel.source}`} · {c.at}</div>
									{c.text}
								</div>
							{/each}
							{#if sel.conversation.length === 1}<div class="d cnone">No reply yet</div>{/if}
						</div>
						<div class="ib-know">
							<div class="kcard"><span class="lbl">Owner check</span><div>{sel.owner_check}</div></div>
							<div class="kcard"><span class="lbl">Wants</span><div>{sel.want_line}</div></div>
							<div class="kcard"><span class="lbl">Priority</span><div>{sel.priority}</div></div>
							<div class="kcard">
								<span class="lbl">Suggested agent</span>
								<div>{sel.suggested}</div>
								<div class="d">{sel.reason}</div>
							</div>
							<div class="kcard">
								<span class="lbl">Asked for it</span>
								{#each sel.requests as r (r.agent)}<div>{r.agent} · {r.at_label}</div>{:else}<div class="muted">None</div>{/each}
							</div>
							<div class="kcard">
								<span class="lbl">Handout</span>
								<div>{inbox.data.handout_label}</div>
								<div class="d">
									{sel.state === 'assigned'
										? `Assigned to ${sel.agent}`
										: inbox.data.handout === 'request'
											? 'Waiting for the first request'
											: 'Waiting for a manager'}
								</div>
							</div>
						</div>
						<div class="form-actions">
							{#if sel.lead_id}
								{@const leadId = sel.lead_id}
								<button type="button" class="btn" onclick={() => openLead(leadId)}>Open lead</button>
							{/if}
							{#if sel.can.ask}
								<button
									type="button"
									class={['btn', sel.lead_id && 'ghost']}
									onclick={() =>
										act(
											() => crm.inbox.ask(sel.id),
											(x) => (x.state === 'assigned' ? `${x.name} is yours · a new lead` : `You asked for ${x.name}`)
										)}
								>
									Ask for this lead
								</button>
							{/if}
							{#if sel.can.withdraw}
								<button type="button" class="btn ghost" onclick={() => act(() => crm.inbox.withdraw(sel.id), (x) => `Request for ${x.name} withdrawn`)}>
									Withdraw request
								</button>
								<span class="small muted">{sel.my_request}</span>
							{/if}
							{#if sel.can.release}
								<button type="button" class="btn ghost" onclick={() => act(() => crm.inbox.release(sel.id), (x) => `${x.name} released to the inbox`)}>
									Release
								</button>
							{/if}
							{#if sel.can.dismiss}
								<button type="button" class="btn plain danger-text" onclick={() => act(() => crm.inbox.dismiss(sel.id), (x) => `${x.name} marked not a real enquiry`)}>
									Not a real enquiry
								</button>
							{/if}
							{#if sel.can.restore}
								<button type="button" class="btn ghost" onclick={() => act(() => crm.inbox.restore(sel.id), (x) => `${x.name} is back in the inbox`)}>
									Back to the inbox
								</button>
							{/if}
						</div>
					</div>
				</section>
			{:else}
				<div class="card"><Empty text="Pick an enquiry. Its checks show here." /></div>
			{/if}

			<section class="card" aria-labelledby="sources-h">
				<div class="ch">
					<h2 id="sources-h">
						Sources
						<span class="facts">{sources.filter((c) => c.status === 'Connected').length} of {sources.length} connected</span>
					</h2>
				</div>
				<div class="cb conn">
					{#each sources as c (c.id)}
						<div class="cn">
							<b>{c.name}</b>
							<span class="d">{c.detail}</span>
							<Chip tone={c.tone}>{c.status}</Chip>
						</div>
					{/each}
				</div>
			</section>
		</div>
	</div>
	{#if inbox.isError}
		<ErrorLine bar message={errorText(inbox.error)} onretry={() => inbox.refetch()} />
	{/if}
{:else if inbox.isError}
	<div class="card"><ErrorLine message={errorText(inbox.error)} onretry={() => inbox.refetch()} /></div>
{:else}
	<div class="inbox">
		<div class="card"><Skeleton rows={6} /></div>
		<div class="card"><Skeleton rows={4} /></div>
	</div>
{/if}
