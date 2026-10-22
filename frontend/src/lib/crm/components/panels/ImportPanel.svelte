<script lang="ts">
import { createQuery } from '@tanstack/svelte-query'
import Pencil from 'lucide-svelte/icons/pencil'
import Trash from 'lucide-svelte/icons/trash-2'
import { goto } from '$app/navigation'
import { page } from '$app/state'
import { crm } from '../../api'
import type { ImportPreview, OwnerFilePreview, OwnerImportResult } from '../../api/types'
import { fmt } from '../../format'
import { crmQueries } from '../../queries'
import { panel } from '../../stores/panel.svelte'
import { errorText, write } from '../../write'
import Chip from '../ui/Chip.svelte'
import Kv from '../ui/Kv.svelte'
import Panel from '../ui/Panel.svelte'
import QueryView from '../ui/QueryView.svelte'
import Seg from '../ui/Seg.svelte'

/**
 * Import a file: Owners or Leads, CSV or XLSX → preview → import. A manager imports directly
 * (owners are checked against DLD; leads land in the Inbox); an agent is told to send the file
 * to their manager (approval requests are open question 9). The backend reads the file and
 * picks the columns; this screen only shows what it found. From the Owners page it imports owners only.
 *
 * An owners file is read in the browser first (header row, row count, five rows); the backend
 * then matches it against DLD before it answers, and the panel shows its counts. Below sits the
 * import history: rename a file (its saved list follows) or delete it, which removes its saved
 * list and claims (no undo), not owner notes.
 */
let { ownersOnly = false }: { ownersOnly?: boolean } = $props()
let kind = $state<'Owners' | 'Leads'>('Owners')
let areas = $state('')
/** Owners: the name of the import and its saved list; empty uses the file name. */
let listName = $state('')
let file = $state<File | null>(null)
let preview = $state<ImportPreview | null>(null)
let ownerPreview = $state<OwnerFilePreview | null>(null)
let result = $state<OwnerImportResult | null>(null)
let problem = $state('')
let reading = $state(false)
let sending = $state(false)
let drag = $state(false)
let input: HTMLInputElement | undefined = $state()

const owners = $derived(ownersOnly || kind === 'Owners')
const history = createQuery(() => crmQueries.ownerImports())
let confirming = $state<number | null>(null)
let deleting = $state<number | null>(null)
let editing = $state<number | null>(null)
let draft = $state('')
let renaming = $state(false)

async function openReport(id: number) {
	panel.closeAll()
	await goto(`/crm/owners/imports/${id}`)
}

function startRename(id: number, name: string) {
	confirming = null
	editing = id
	draft = name
}

async function rename(id: number, before: string) {
	const name = draft.trim()
	if (!name || name === before) {
		editing = null
		return
	}
	renaming = true
	const r = await write({
		call: () => crm.imports.renameOwners(id, name),
		success: `Renamed · ${name}`,
		event: 'import_renamed'
	})
	renaming = false
	if (r) editing = null
}

const STATUS = {
	completed: { label: 'Completed', tone: 'green' },
	processing: { label: 'Processing', tone: 'amber' },
	failed: { label: 'Failed', tone: 'red' }
} as const

async function remove(id: number, name: string) {
	deleting = id
	await write({
		call: () => crm.imports.removeOwners(id),
		success: `Deleted · ${name}`,
		event: 'import_deleted'
	})
	deleting = null
	confirming = null
}
const rows = $derived(owners ? (ownerPreview?.rows ?? 0) : (preview?.rows ?? 0))
const ready = $derived(owners ? ownerPreview && !ownerPreview.problem : preview && !preview.problem)

function reset() {
	file = null
	preview = null
	ownerPreview = null
	result = null
	problem = ''
	if (input) input.value = ''
}

function size(bytes: number): string {
	return bytes < 1024 * 1024
		? `${Math.max(1, Math.round(bytes / 1024))} KB`
		: `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function read(f: File | undefined) {
	if (!f) return
	reset()
	file = f
	reading = true
	try {
		if (owners) {
			ownerPreview = await crm.imports.previewOwners(f)
			problem = ownerPreview.problem ?? ''
		} else {
			preview = await crm.imports.preview(f)
			problem = preview.problem ?? ''
		}
	} catch (e) {
		problem = errorText(e)
	} finally {
		reading = false
	}
}

async function send() {
	if (!file || !ready || sending) return
	const f = file
	if (!owners) {
		const r = await write({
			call: () => crm.imports.create(f, areas.trim()),
			success: `Imported · ${fmt(rows)} rows`,
			event: 'import_requested'
		})
		if (r) {
			panel.closeAll()
			await goto('/crm/inbox')
		}
		return
	}
	sending = true
	const r = await write({
		call: () => crm.imports.importOwners(f, listName),
		success: ({ data }) =>
			data.alreadyImported
				? null
				: data.status === 'completed'
					? `Imported · ${fmt(data.summary.candidateRows)} owners`
					: null,
		event: 'import_requested'
	})
	sending = false
	if (r) {
		result = r.data
		if (page.url.pathname !== '/crm/owners') await goto('/crm/owners')
	}
}
</script>

<Panel
	kicker="New"
	title="Import a file"
	sub={ownersOnly
		? 'Managers import · owners are checked against DLD'
		: 'Managers import · owners are checked against DLD, leads go to the Inbox'}
	footer={result ? done : undefined}
>
	<div class="form">
		{#if result}
			{@const s = result.summary}
			{#if result.alreadyImported}
				<div class="notice" role="status">
					This file was already imported as “{result.name}”. Nothing new was added.
				</div>
			{/if}
			{#if result.status === 'failed'}
				<div class="notice" role="alert">
					The import of this file failed (import #{result.id}). Fix the file and upload it again.
				</div>
			{:else if result.status === 'processing'}
				<div class="notice" role="status">This file is still being imported. Check back in a few minutes.</div>
			{:else}
				<section class="sec">
					<h3>{result.name}</h3>
					<div class="kv">
						<Kv k="Rows in the file" v={fmt(s.totalRows)} />
						<Kv k="Owners kept" v={fmt(s.candidateRows)} o={s.duplicatesCollapsed ? `${fmt(s.duplicatesCollapsed)} duplicates merged` : ''} />
						{#if s.distinctProperties !== null}<Kv k="Properties" v={fmt(s.distinctProperties)} o="Distinct units, villas or plots" />{/if}
						<Kv k="With a phone or email" v={fmt(s.validContacts)} />
						<Kv k="Ready to contact" v={fmt(s.contactReady)} />
						<Kv k="Verified owners" v={fmt(s.verified)} o="Matches a DLD sale" />
						<Kv k="Probable owners" v={fmt(s.probable)} />
						<Kv k="Unit found, owner not verified" v={fmt(s.unitLinked)} />
						<Kv k="Former owners" v={fmt(s.former)} o="Sold since" />
						<Kv k="Conflicts with DLD" v={fmt(s.conflicting)} />
						<Kv k="Not matched" v={fmt(s.notMatched)} />
						<Kv k="Rental opportunities" v={fmt(s.rentalOpportunities)} />
					</div>
				</section>
			{/if}
		{:else}
			{#if !ownersOnly}
				<div class="fld">
					<span class="fl">What is in the file</span>
					<Seg
						label="What is in the file"
						items={[
							{ id: 'Owners', label: 'Owners' },
							{ id: 'Leads', label: 'Leads' }
						]}
						value={kind}
						onselect={(k) => {
							kind = k
							reset()
						}}
					/>
				</div>
			{/if}
			{#if !owners}
				<div class="fld">
					<label for="imp-areas">Areas</label>
					<input id="imp-areas" placeholder="JVC, Dubai Marina" bind:value={areas} />
				</div>
			{/if}
			<input
				bind:this={input}
				type="file"
				accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
				hidden
				aria-hidden="true"
				tabindex="-1"
				onchange={() => read(input?.files?.[0])}
			/>
			<button
				type="button"
				class={['drop', drag && 'drag']}
				disabled={sending}
				onclick={() => input?.click()}
				ondragover={(e) => {
					e.preventDefault()
					drag = true
				}}
				ondragleave={() => (drag = false)}
				ondrop={(e) => {
					e.preventDefault()
					drag = false
					read(e.dataTransfer?.files[0])
				}}
			>
				<b>{file ? file.name : 'Drop a CSV or XLSX file here'}</b>
				<span>{owners ? 'name, mobile, community, building, unit number' : 'name, phone or mobile, area, budget'}</span>
				<span class="btn ghost sm">{file ? 'Choose another file' : 'Choose a file'}</span>
			</button>

			{#if owners}
				<div class="fld">
					<label for="imp-list">List name</label>
					<input
						id="imp-list"
						maxlength="200"
						placeholder={file ? file.name.replace(/\.[^.]+$/, '') : 'e.g. Downtown owners · July'}
						bind:value={listName}
						disabled={sending}
					/>
				</div>
			{/if}
			{#if reading}<div class="small muted">Reading the file…</div>{/if}
			{#if problem}<div class="notice" role="alert">{problem}</div>{/if}

			{#if owners && ownerPreview && !ownerPreview.problem}
				<section class="sec">
					<h3>
						{fmt(ownerPreview.rows)} rows · {ownerPreview.file} · {size(ownerPreview.size)}{ownerPreview.sheets > 1
							? ` · ${ownerPreview.sheets} sheets`
							: ''}
					</h3>
					<div class="small muted">
						Columns found: {ownerPreview.headers.join(', ')}. Vitevue maps them when you import.
					</div>
					<div class="tblwrap boxed">
						<table class="tbl">
							<thead>
								<tr>
									{#each ownerPreview.headers.slice(0, 6) as h, i (i)}<th>{h}</th>{/each}
								</tr>
							</thead>
							<tbody>
								{#each ownerPreview.sample as row, i (i)}
									<tr>
										{#each row.slice(0, 6) as cell, j (j)}
											<td data-label={ownerPreview.headers[j]}>{cell || '—'}</td>
										{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#if ownerPreview.rows > 5}<div class="small muted">and {fmt(ownerPreview.rows - 5)} more</div>{/if}
				</section>
			{:else if !owners && preview}
				<section class="sec">
					<h3>{fmt(preview.rows)} rows · {preview.file}</h3>
					<div class="tblwrap boxed">
						<table class="tbl">
							<thead>
								<tr>
									<th>Name{preview.columns.name ? ` (${preview.columns.name})` : ''}</th>
									<th>Phone{preview.columns.phone ? ` (${preview.columns.phone})` : ''}</th>
									<th>Budget</th>
									<th>Area</th>
								</tr>
							</thead>
							<tbody>
								{#each preview.sample as row, i (i)}
									<tr>
										<td>{row.name || '—'}</td>
										<td class="mono small" data-label="Phone">{row.phone || '—'}</td>
										<td data-label="Budget">{row.budget || '—'}</td>
										<td data-label="Area">{row.area || '—'}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#if preview.rows > 5}<div class="small muted">and {fmt(preview.rows - 5)} more</div>{/if}
				</section>
			{/if}

			{#if ready}
				<div>
					<button type="button" class="btn" disabled={sending} onclick={send}>
						{sending ? 'Importing…' : `Import ${fmt(rows)} rows`}
					</button>
				</div>
				{#if sending && owners}
					<div class="small muted">Checking each owner against DLD. A large file can take a few minutes; keep this panel open.</div>
				{/if}
			{/if}
		{/if}
	</div>

	{#if owners}
		<section class="sec">
			<h3>Import history</h3>
			<QueryView query={history} rows={3}>
				{#snippet children(list)}
					{#if !list.length}
						<div class="small muted">No owner files imported yet.</div>
					{:else}
						<ul class="hist">
							{#each list as imp (imp.id)}
								<li>
									{#if editing === imp.id}
										<form
											class="hi edit"
											onsubmit={(e) => {
												e.preventDefault()
												rename(imp.id, imp.name)
											}}
										>
											<!-- svelte-ignore a11y_autofocus -->
											<input
												aria-label="Import name"
												maxlength="200"
												bind:value={draft}
												disabled={renaming}
												autofocus
												onkeydown={(e) => e.key === 'Escape' && (editing = null)}
											/>
											<button type="submit" class="btn sm" disabled={renaming || !draft.trim()}>
												{renaming ? 'Saving…' : 'Save'}
											</button>
											<button type="button" class="btn ghost sm" onclick={() => (editing = null)}>Cancel</button>
										</form>
									{:else}
										<div class="hi">
											<b title={imp.file}>{imp.name}</b>
											<button type="button" class="link small" onclick={() => openReport(imp.id)}>View report</button>
											<span class="small muted">
												{fmt(imp.claims)} unique claims · {fmt(imp.rentalLeads)} rental leads · {imp.at}
											</span>
										</div>
										<Chip tone={STATUS[imp.status].tone}>{STATUS[imp.status].label}</Chip>
									{/if}
									{#if editing === imp.id}{:else if confirming === imp.id}
										<span class="small">Delete its list and claims?</span>
										<button
											type="button"
											class="btn danger sm"
											disabled={deleting === imp.id}
											onclick={() => remove(imp.id, imp.name)}
										>
											{deleting === imp.id ? 'Deleting…' : 'Delete'}
										</button>
										<button type="button" class="btn ghost sm" onclick={() => (confirming = null)}>Cancel</button>
									{:else}
										<button
											type="button"
											class="btn ghost sm"
											aria-label="Rename {imp.name}"
											title="Rename"
											onclick={() => startRename(imp.id, imp.name)}
										>
											<Pencil size={15} />
										</button>
										<button
											type="button"
											class="btn ghost sm"
											aria-label="Delete {imp.name}"
											title="Delete"
											onclick={() => {
												editing = null
												confirming = imp.id
											}}
										>
											<Trash size={15} />
										</button>
									{/if}
								</li>
							{/each}
						</ul>
					{/if}
				{/snippet}
			</QueryView>
		</section>
	{/if}
</Panel>

{#snippet done()}
	<button type="button" class="btn" onclick={() => panel.closeAll()}>Done</button>
	{#if result}
		{@const id = result.id}
		<button type="button" class="btn ghost" onclick={() => openReport(id)}>Open report</button>
	{/if}
	<button
		type="button"
		class="btn ghost"
		onclick={() => {
			reset()
			listName = ''
		}}
	>
		Import another file
	</button>
{/snippet}

<style>
	.hist {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.4rem;
	}
	.hist li {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding: 0.5rem 0.6rem;
		border: 1px solid var(--line);
		border-radius: 8px;
	}
	.hi {
		flex: 1;
		min-width: 0;
		display: grid;
	}
	.hi.edit {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.hi.edit input {
		flex: 1;
		min-width: 0;
	}
	.link {
		justify-self: start;
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		cursor: pointer;
	}
	.hi b {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
