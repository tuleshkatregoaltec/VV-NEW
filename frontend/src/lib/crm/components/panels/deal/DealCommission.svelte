<script lang="ts">
import { untrack } from 'svelte'
import { crm } from '../../../api'
import type {
	CommissionAmounts,
	CommissionPatch,
	DealDetail,
	InvoiceStatus
} from '../../../api/types'
import { INVOICE } from '../../../config'
import { fmt } from '../../../format'
import { toast } from '../../../stores/toast.svelte'
import { errorText, write } from '../../../write'
import Kv from '../../ui/Kv.svelte'

/**
 * Inputs (rate, co-broker %, referral %, agent %) → live result. The backend does the maths: while
 * typing, a preview call returns the amounts (nothing saved); leaving a box saves it, only when
 * every box holds a number from 0 to 100. An empty box is never saved as 0%.
 */
let { deal }: { deal: DealDetail } = $props()

type Inputs = {
	rate_pct: number | null
	cobroker_pct: number | null
	referral_pct: number | null
	agent_pct: number | null
}
const pick = (d: DealDetail): Inputs => ({
	rate_pct: d.commission.rate_pct,
	cobroker_pct: d.commission.cobroker_pct,
	referral_pct: d.commission.referral_pct,
	agent_pct: d.commission.agent_pct
})

let values = $state<Inputs>(untrack(() => pick(deal)))
let amounts = $state<CommissionAmounts>(untrack(() => deal.commission.amounts))
let saved = untrack(() => pick(deal))
let timer: ReturnType<typeof setTimeout> | undefined
let seq = 0
let error = $state('')

const invoice = $derived(deal.commission.invoice_status)

// A pending preview must not fire after the panel closes.
$effect(() => () => clearTimeout(timer))

const valid = (v: Inputs) =>
	Object.values(v).every((n) => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 100)

function oninput() {
	clearTimeout(timer)
	if (!valid(values)) {
		error = 'Each percentage must be between 0 and 100.'
		return
	}
	error = ''
	timer = setTimeout(preview, 200)
}

async function preview() {
	const mine = ++seq
	try {
		const r = await crm.deals.commissionPreview(deal.id, { ...values } as CommissionPatch)
		if (mine === seq) {
			amounts = r
			error = ''
		}
	} catch (e) {
		if (mine === seq) error = errorText(e)
	}
}

async function onchange() {
	clearTimeout(timer)
	if (!valid(values)) return
	const snapshot = saved
	const next = { ...values }
	if (JSON.stringify(next) === JSON.stringify(snapshot)) return
	const r = await write({
		call: () => crm.deals.commission(deal.id, next as CommissionPatch),
		success: null,
		event: 'commission_edited'
	})
	if (!r) return
	amounts = r.data.commission.amounts
	saved = next
	toast.show(`Commission updated on ${deal.id}`, {
		undo: async () => {
			values = { ...snapshot }
			const back = await write({
				call: () => crm.deals.commission(deal.id, snapshot as CommissionPatch),
				success: 'Undone',
				undoable: false
			})
			if (back) {
				amounts = back.data.commission.amounts
				saved = snapshot
			}
		}
	})
}

function setInvoice(e: Event) {
	const status = (e.currentTarget as HTMLSelectElement).value as InvoiceStatus
	write({
		call: () => crm.deals.commission(deal.id, { invoice_status: status }),
		success: `Invoice → ${INVOICE[status].label.toLowerCase()}`
	})
}

function sendInvoice() {
	write({
		call: () => crm.deals.invoice(deal.id),
		success: (r) => `Invoice for AED ${fmt(r.invoice.amount_aed)} + VAT sent`
	})
}

const fieldsDef = [
	{ key: 'rate_pct', label: 'Commission rate %', step: 0.1 },
	{ key: 'cobroker_pct', label: 'Co-broker share % (Form I)', step: 1 },
	{ key: 'referral_pct', label: 'Referral share %', step: 1 },
	{ key: 'agent_pct', label: 'Agent split %', step: 1 }
] as const
</script>

<form class="form" onsubmit={(e) => e.preventDefault()}>
	<div class="frow">
		{#each fieldsDef as f (f.key)}
			<div class="fld">
				<label for="comm-{f.key}">{f.label}</label>
				<input
					id="comm-{f.key}"
					type="number"
					min="0"
					max="100"
					step={f.step}
					bind:value={values[f.key]}
					{oninput}
					{onchange}
				/>
			</div>
		{/each}
	</div>
</form>

{#if error}<div class="errline" role="alert">{error}</div>{/if}

<div class="kv" aria-live="polite">
	<Kv k="Gross" v={fmt(amounts.gross)} />
	<Kv k="Co-broker" v={amounts.cobroker ? `−${fmt(amounts.cobroker)}` : '—'} />
	<Kv
		k="Referral"
		v={amounts.referral
			? `−${fmt(amounts.referral)}${deal.commission.referral_name ? ` · ${deal.commission.referral_name}` : ''}`
			: '—'}
	/>
	<Kv k="Net to the agency" v={fmt(amounts.net)} />
	<Kv k="Agent" v={fmt(amounts.agent)} />
	<Kv k="Agency keeps" v={fmt(amounts.agency)} />
	<Kv k="VAT on invoice" v={fmt(amounts.vat)} o="5%" />
</div>

<div class="frow">
	<div class="fld">
		<label for="invoice-{deal.id}">Invoice</label>
		<select id="invoice-{deal.id}" value={invoice} onchange={setInvoice}>
			{#each Object.entries(INVOICE) as [id, s] (id)}
				<option value={id}>{s.label}</option>
			{/each}
		</select>
	</div>
	<div class="fld">
		<span class="fl" aria-hidden="true">&nbsp;</span>
		<button type="button" class="btn ghost" onclick={sendInvoice}>
			{invoice === 'not_invoiced' ? 'Create invoice' : 'Resend invoice'}
		</button>
	</div>
</div>
