<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Check from 'lucide-svelte/icons/check'
import { crm } from '../../api'
import type { ChecklistItem, DealDetail, DealType } from '../../api/types'
import { fmt, price, short } from '../../format'
import { crmKeys, crmQueries } from '../../queries'
import { openDeal, panel } from '../../stores/panel.svelte'
import { toast } from '../../stores/toast.svelte'
import { ui } from '../../stores/ui.svelte'
import { errorText, patchCache, write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Empty from '../ui/Empty.svelte'
import ErrorLine from '../ui/ErrorLine.svelte'
import Seg from '../ui/Seg.svelte'
import Skeleton from '../ui/Skeleton.svelte'

/**
 * Deals → Conveyancing (the default view, 1 Oct): open deals as cards on the left, soonest due
 * first, All / Sale / Rent; the selected deal in full on the right — both sides, value,
 * commission, the stage steps, then one block per stage: who is involved, its checklist, that
 * stage's documents, and Move on / Back a stage on the current one.
 */
const list = createQuery(() => crmQueries.deals('conveyancing', ui.cvType))
const items = $derived(list.data?.items ?? [])
const selected = $derived(items.some((d) => d.id === ui.cvSel) ? ui.cvSel : (items[0]?.id ?? ''))
const deal = createQuery(() => ({ ...crmQueries.deal(selected), enabled: Boolean(selected) }))

let input: HTMLInputElement | undefined = $state()
let docTarget = ''
let detail: HTMLElement | undefined = $state()

function pick(id: string) {
	ui.cvSel = id
	if (typeof window !== 'undefined' && window.innerWidth <= 900)
		requestAnimationFrame(() => detail?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
}

function tick(d: DealDetail, item: ChecklistItem, done: boolean) {
	write({
		optimistic: () =>
			patchCache<DealDetail>(crmKeys.deal(d.id), (x) => ({
				...x,
				checklist: x.checklist.map((s) => ({
					...s,
					items: s.items.map((t) => (t.key === item.key ? { ...t, done } : t))
				}))
			})),
		call: () => crm.deals.check(d.id, item.key, done),
		success: `${done ? 'Done' : 'Not done'}: ${item.label}`
	})
}

function upload(doc: string) {
	docTarget = doc
	if (!input) return
	input.value = ''
	input.click()
}

function onPicked() {
	const f = input?.files?.[0]
	const id = selected
	if (!f || !docTarget || !id) return
	const doc = docTarget
	write({
		call: () => crm.deals.upload(id, f, doc),
		success: `${doc} filed on ${id}`,
		event: 'document_uploaded'
	})
}

function move(d: DealDetail, direction: 'next' | 'back') {
	const index = d.stage_index + (direction === 'next' ? 1 : -1)
	write({
		call: () => crm.deals.move(d.id, direction),
		success:
			direction === 'next' ? `${d.id} → ${d.stages[index]}` : `${d.id} back to ${d.stages[index]}`,
		event: 'deal_stage_changed'
	})
}

async function requestMissing(d: DealDetail) {
	try {
		await crm.deals.requestDocuments(d.id, d.missing)
		toast.show(
			`Request sent to the client for ${d.missing.length} document${d.missing.length > 1 ? 's' : ''}`
		)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}

const typeItems = $derived(
	(
		[
			['', 'All', list.data ? list.data.counts.open : null],
			['sale', 'Sale', list.data?.counts.sale ?? null],
			['rent', 'Rent', list.data?.counts.rent ?? null]
		] as [DealType | '', string, number | null][]
	).map(([id, label, n]) => ({ id, label: n === null ? label : `${label} · ${n}` }))
)
</script>

<input bind:this={input} type="file" hidden aria-hidden="true" tabindex="-1" onchange={onPicked} />

<div class="cvfilter">
	<Seg label="Deal type" items={typeItems} value={ui.cvType} onselect={(t) => (ui.cvType = t)} />
	<span class="small muted">Sorted by due date</span>
</div>

{#if list.isError && !list.data}
	<div class="card"><ErrorLine message={errorText(list.error)} onretry={() => list.refetch()} /></div>
{:else if !list.data}
	<div class="card"><Skeleton rows={5} /></div>
{:else if !items.length}
	<div class="card">
		<Empty
			text="No open {ui.cvType ? `${ui.cvType} ` : ''}deals. A deal starts when a buyer or tenant lead is moved to deal."
		>
			<button type="button" class="btn sm" onclick={() => panel.open({ kind: 'new-deal' })}>＋ Deal</button>
		</Empty>
	</div>
{:else}
	{#if list.isError}
		<ErrorLine bar message={errorText(list.error)} onretry={() => list.refetch()} />
	{/if}
	<div class="cvwrap">
		<div class="cvlist" role="group" aria-label="Open deals">
			{#each items as x (x.id)}
				<button
					type="button"
					class={['cvcard', x.id === selected && 'on']}
					aria-pressed={x.id === selected}
					onclick={() => pick(x.id)}
				>
					<span class="t">{x.unit}</span>
					<span class="d">
						{x.client} · {x.deal_type === 'sale' ? 'Sale' : 'Rent'} · {x.deal_type === 'rent'
							? `${fmt(x.value_aed)}/yr`
							: short(x.value_aed)}
					</span>
					<span class="cvbar" aria-hidden="true">
						<i style:width="{Math.round(((x.stage_index + 1) / x.stages.length) * 100)}%"></i>
					</span>
					<span class="f">
						<span class="d">{x.stage_index + 1}/{x.stages.length} · {x.stage}</span>
						<Chip chip={x.due_chip} />
					</span>
				</button>
			{/each}
		</div>

		<section class="cvdet" bind:this={detail} aria-label="Selected deal">
			{#if deal.data}
				{@const d = deal.data}
				<div class="card">
					<div class="cvhead">
						<div class="min-w-0">
							<div class="lbl">{d.deal_type === 'sale' ? 'Sale' : 'Rent'} · {d.id} · {d.agent}</div>
							<h2>{d.unit}</h2>
							<div class="small muted">
								{d.deal_type === 'sale' ? 'Buyer' : 'Tenant'}
								{d.client} · {d.deal_type === 'sale' ? 'Seller' : 'Landlord'}
								{d.counterparty} · {price(d.deal_type, d.value_aed)} · commission {fmt(d.commission.amounts.gross)}
							</div>
						</div>
						<div class="form-actions">
							<button type="button" class="btn ghost sm" onclick={() => openDeal(d.id)}>Open deal</button>
							{#if d.missing.length}
								<button type="button" class="btn ghost sm" onclick={() => requestMissing(d)}>
									Request {d.missing.length} missing
								</button>
							{/if}
						</div>
					</div>

					<ol class="cvsteps" aria-label="Stages">
						{#each d.checklist as s (s.index)}
							<li class={s.state} aria-current={s.state === 'now' ? 'step' : undefined}>
								{#if s.state === 'done'}<Check size={11} strokeWidth={3} />{/if}{s.name}
							</li>
						{/each}
					</ol>

					<div class="cvstages">
						{#each d.checklist as s (s.index)}
							{@const now = s.state === 'now'}
							<div class={['cvst', s.state]}>
								<div class="h">
									<span class="n" aria-hidden="true">
										{#if s.state === 'done'}<Check size={12} strokeWidth={3} />{:else}{s.index + 1}{/if}
									</span>
									<span>
										<b>{s.name}</b>
										<div class="w">{s.who}</div>
									</span>
									<Chip chip={s.chip} />
								</div>
								<div class="b">
									{#if now && !d.closed && d.next_step}
										<div class="small"><b>Next:</b> {d.next_step}</div>
									{/if}
									{#each s.items as t (t.key)}
										{#if t.doc}
											<div class="ck-row">
												<span class={['ck-box', t.done && 'on']} aria-hidden="true">
													{#if t.done}<Check size={10} strokeWidth={3} />{/if}
												</span>
												<span>
													<div>{t.label}</div>
													{#if t.file}<div class="fn">{t.file.name} · {t.file.uploaded}</div>{/if}
												</span>
												{#if t.done}
													<Chip tone="green">On file</Chip>
												{:else}
													<button
														type="button"
														class={['btn sm', !now && 'ghost']}
														onclick={() => t.doc && upload(t.doc)}
													>
														Upload
													</button>
												{/if}
											</div>
										{:else if now && !d.closed}
											<label class="ck-row">
												<input
													type="checkbox"
													checked={t.done}
													onchange={(e) => tick(d, t, e.currentTarget.checked)}
												/>
												<span>{t.label}</span>
												<span></span>
											</label>
										{:else}
											<div class="ck-row">
												<span class={['ck-box', t.done && 'on']} aria-hidden="true">
													{#if t.done}<Check size={10} strokeWidth={3} />{/if}
												</span>
												<span>{t.label}<span class="sr-only">{t.done ? ' (done)' : ' (to do)'}</span></span>
												<span></span>
											</div>
										{/if}
									{/each}
									{#if now && d.next_stage}
										<div class="form-actions">
											<button type="button" class="btn sm" onclick={() => move(d, 'next')}>
												Move to {d.next_stage}
											</button>
											{#if d.stage_index > 0}
												<button type="button" class="btn ghost sm" onclick={() => move(d, 'back')}>
													Back a stage
												</button>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</div>
			{:else if deal.isError}
				<div class="card"><ErrorLine message={errorText(deal.error)} onretry={() => deal.refetch()} /></div>
			{:else}
				<div class="card"><Skeleton rows={6} /></div>
			{/if}
		</section>
	</div>
{/if}
