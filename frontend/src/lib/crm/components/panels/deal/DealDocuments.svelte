<script lang="ts">
import { track } from '../../../analytics'
import { crm } from '../../../api'
import type { DealDetail } from '../../../api/types'
import { fileSize } from '../../../format'
import { panel } from '../../../stores/panel.svelte'
import { toast } from '../../../stores/toast.svelte'
import { errorText, refreshCrm, undoAll, write } from '../../../write'
import Chip from '../../ui/Chip.svelte'

/**
 * One row per required document: upload, drop a file on the row, replace, remove (Undo),
 * request from the client. Below, a multi-file drop zone; the backend matches file names to
 * documents and anything unmatched asks "Which document is this?".
 */
let { deal }: { deal: DealDetail } = $props()

let input: HTMLInputElement | undefined = $state()
let target = $state<string | null>(null)
let dragRow = $state<string | null>(null)
let dragAll = $state(false)

const received = $derived(new Map(deal.documents.received.map((d) => [d.type, d])))

function pick(docType: string | null) {
	target = docType
	if (!input) return
	input.multiple = docType === null
	input.value = ''
	input.click()
}

function fileInto(docType: string, file: File) {
	write({
		call: () => crm.deals.upload(deal.id, file, docType),
		success: `${docType} filed on ${deal.id}`,
		event: 'document_uploaded'
	})
}

/**
 * Try every file, then one toast: "3 filed, 1 failed" with Undo for the ones that were filed.
 * Files the backend can't match go to "Which document is this?".
 */
async function fileMany(files: File[]) {
	if (!files.length) return
	const filed: string[] = []
	const tokens: string[] = []
	const unknown: File[] = []
	let failed = 0
	let firstError = ''
	for (const f of files) {
		try {
			const r = await crm.deals.upload(deal.id, f)
			if (r.data.matched) {
				filed.push(r.data.matched)
				if (r.undo_token) tokens.push(r.undo_token)
			} else unknown.push(f)
		} catch (e) {
			failed += 1
			firstError ||= errorText(e)
		}
	}
	await refreshCrm()
	if (filed.length) track('document_uploaded', { count: filed.length })
	if (filed.length || failed) {
		const what = filed.length
			? `${filed.length} filed on ${deal.id}${failed ? `, ${failed} failed` : `: ${filed.join(', ')}`}`
			: `${failed} file${failed > 1 ? 's' : ''} failed — ${firstError}`
		toast.show(what, {
			tone: filed.length ? 'default' : 'error',
			undo: tokens.length ? () => void undoAll(tokens) : undefined
		})
	}
	if (unknown.length) panel.open({ kind: 'which-doc', dealId: deal.id, files: unknown, index: 0 })
}

function onPicked() {
	const files = Array.from(input?.files ?? [])
	if (!files.length) return
	if (target) fileInto(target, files[0])
	else void fileMany(files)
}

function remove(docType: string) {
	const doc = received.get(docType)
	if (!doc) return
	write({ call: () => crm.deals.removeDocument(deal.id, doc.id), success: `${docType} removed` })
}

async function request(docType: string) {
	try {
		await crm.deals.requestDocuments(deal.id, [docType])
		track('document_requested', { doc_type: docType })
		toast.show(`Request sent to the client for the ${docType.toLowerCase()}`)
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
	}
}

function dropOnRow(e: DragEvent, docType: string) {
	e.preventDefault()
	dragRow = null
	const f = e.dataTransfer?.files[0]
	if (f) fileInto(docType, f)
}

function dropAll(e: DragEvent) {
	e.preventDefault()
	dragAll = false
	void fileMany(Array.from(e.dataTransfer?.files ?? []))
}
</script>

<input bind:this={input} type="file" hidden onchange={onPicked} aria-hidden="true" tabindex="-1" />

<section class="sec">
	<h3>{deal.docs_received} of {deal.docs_required} · {deal.deal_type} set</h3>
	<div class="docs">
		{#each deal.documents.required as docType (docType)}
			{@const doc = received.get(docType)}
			<div
				class={['doc', dragRow === docType && 'drag']}
				role="group"
				aria-label={docType}
				ondragover={(e) => {
					e.preventDefault()
					dragRow = docType
				}}
				ondragleave={() => (dragRow = null)}
				ondrop={(e) => dropOnRow(e, docType)}
			>
				<div>
					<div class="nm">{docType}</div>
					{#if doc}
						<div class="fn">{doc.file} · {fileSize(doc.size_bytes)} · {doc.uploaded}</div>
					{:else}
						<div class="fn missing">Missing — drop a file here</div>
					{/if}
				</div>
				<div class="acts">
					{#if doc}
						<Chip tone="green">Received</Chip>
						<button type="button" class="btn ghost sm" onclick={() => pick(docType)}>Replace</button>
						<button type="button" class="btn danger sm" onclick={() => remove(docType)}>Remove</button>
					{:else}
						<button type="button" class="btn sm" onclick={() => pick(docType)}>Upload</button>
						<button type="button" class="btn ghost sm" onclick={() => request(docType)}>
							Request from client
						</button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
</section>

<button
	type="button"
	class={['drop', dragAll && 'drag']}
	onclick={() => pick(null)}
	ondragover={(e) => {
		e.preventDefault()
		dragAll = true
	}}
	ondragleave={() => (dragAll = false)}
	ondrop={dropAll}
>
	<b>Drop several files at once</b>
	<span>Named files are matched automatically — passport, emirates-id, form-f, title-deed, noc, cheque, tenancy</span>
	<span class="btn ghost sm">Choose files</span>
</button>
