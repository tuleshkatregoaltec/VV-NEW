/**
 * Every write follows one path: optimistic update → API call → on failure roll back and show
 * one line of error; on success show a toast with Undo for 5 seconds and refresh the CRM data.
 */
import type { QueryKey } from '@tanstack/svelte-query'
import { ApiError } from '$lib/api/client'
import { getQueryClient } from '$lib/queries/client'
import { track } from './analytics'
import { crm } from './api'
import { crmKeys } from './queries'
import { closeRecord, type RecordKind } from './stores/panel.svelte'
import { toast } from './stores/toast.svelte'

/** Known error codes get their own line, whatever the server wrote. */
const CODE_TEXT: Record<string, string> = {
	network_error: 'Could not reach the server. Check your connection and try again.',
	unavailable: 'Could not load this. Check your connection and retry.',
	permit_required: 'Add a valid Trakheesi permit first.',
	undo_expired: 'That can no longer be undone.',
	not_found: 'That isn’t there any more.',
	not_yours: 'Only the listing agent can change this listing.'
}

/** One readable line for any failure; never a raw error. */
export function errorText(e: unknown): string {
	if (e instanceof ApiError) {
		if (e.code && CODE_TEXT[e.code]) return CODE_TEXT[e.code]
		// A part of the CRM not connected to live data yet: the API says what to do instead.
		if (e.code === 'not_connected') return e.message || 'This part of the CRM is not connected yet.'
		// stage_blocked, invalid, exists…: the API's own message names what to do.
		if (e.message && e.status > 0 && e.status < 500) return e.message
		if (e.status >= 500) return 'The server had a problem. Try again in a moment.'
		if (e.message) return e.message
	}
	return 'Something went wrong. Try again.'
}

/** Refetch every CRM Beta query that is on screen. A failure is one line with Retry. */
export function refreshCrm(): Promise<void> {
	return getQueryClient()
		.invalidateQueries({ queryKey: crmKeys.all })
		.catch(() => {
			toast.show('Could not refresh the CRM.', {
				tone: 'error',
				action: { label: 'Retry', run: () => void refreshCrm() }
			})
		})
}

/**
 * Apply a change to cached data now; returns the rollback. When the query is not cached yet,
 * mark it stale instead so the next read fetches it.
 */
export function patchCache<T>(key: QueryKey, change: (current: T) => T): () => void {
	const qc = getQueryClient()
	const before = qc.getQueryData<T>(key)
	if (before === undefined) {
		void qc.invalidateQueries({ queryKey: key, exact: true, refetchType: 'none' })
		return () => {}
	}
	void qc.cancelQueries({ queryKey: key })
	qc.setQueryData<T>(key, change(before))
	return () => qc.setQueryData<T>(key, before)
}

export async function undoWrite(token: string, after?: () => void): Promise<boolean> {
	try {
		await crm.undo(token)
		after?.()
		toast.show('Undone')
		return true
	} catch (e) {
		toast.show(errorText(e), { tone: 'error' })
		return false
	} finally {
		await refreshCrm()
	}
}

/** Undo a batch (latest first); one line at the end with the count. */
export async function undoAll(tokens: string[]): Promise<void> {
	let failed = 0
	for (const t of [...tokens].reverse()) {
		try {
			await crm.undo(t)
		} catch {
			failed += 1
		}
	}
	await refreshCrm()
	const done = tokens.length - failed
	if (!failed) toast.show('Undone')
	else
		toast.show(`${done} undone, ${failed} could not be undone`, {
			tone: done ? 'default' : 'error'
		})
}

interface WriteOptions<R> {
	call: () => Promise<R>
	/** Change cached data before the call; return the rollback. */
	optimistic?: () => () => void
	/** Toast text on success; null for no toast. */
	success?: string | ((result: R) => string | null) | null
	/** Offer Undo when the response carries an undo_token (default true). */
	undoable?: boolean
	/** A record this write created: undoing it closes any panel showing that record. */
	created?: (result: R) => { kind: RecordKind; id: string | number }
	after?: (result: R) => void | Promise<void>
	event?: string
}

/** Returns the result, or null when the call failed (already rolled back and shown). */
export async function write<R>(opts: WriteOptions<R>): Promise<R | null> {
	const rollback = opts.optimistic?.()
	let result: R
	try {
		result = await opts.call()
	} catch (e) {
		rollback?.()
		toast.show(errorText(e), { tone: 'error' })
		return null
	}
	void refreshCrm()
	const token =
		result && typeof result === 'object' && 'undo_token' in result
			? ((result as { undo_token: string | null }).undo_token ?? null)
			: null
	const message = typeof opts.success === 'function' ? opts.success(result) : opts.success
	if (message) {
		const made = opts.created?.(result)
		toast.show(message, {
			undo:
				token && opts.undoable !== false
					? () => void undoWrite(token, made ? () => closeRecord(made.kind, made.id) : undefined)
					: undefined
		})
	}
	if (opts.event) track(opts.event)
	await opts.after?.(result)
	return result
}
