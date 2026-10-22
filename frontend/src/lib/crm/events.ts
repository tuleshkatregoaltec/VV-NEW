import type { CalendarEvent } from './api/types'
import { panel } from './stores/panel.svelte'

/** v2 (7 Oct): a click on an event opens its detail (when, kind, record, outcome) first. */
export function openEvent(e: CalendarEvent): void {
	panel.open({ kind: 'event', id: e.id })
}
