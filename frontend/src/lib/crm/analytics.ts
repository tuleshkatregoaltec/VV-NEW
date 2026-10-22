/**
 * PostHog events from docs/crm/build-plan.md. The root layout initialises PostHog; until it has
 * (no key configured, or not loaded yet) this sends nothing.
 */
import posthog from 'posthog-js'

export type CrmEvent =
	| 'lead_opened'
	| 'lead_stage_changed'
	| 'lead_type_changed'
	| 'lead_created'
	| 'viewing_booked'
	| 'deal_created'
	| 'deal_stage_changed'
	| 'document_uploaded'
	| 'document_requested'
	| 'commission_edited'
	| 'listing_published'
	| 'feed_lead_added'
	| 'import_requested'
	| 'search_used'

export function track(event: CrmEvent | string, properties?: Record<string, unknown>): void {
	if (typeof window === 'undefined' || !posthog.__loaded) return
	try {
		posthog.capture(event, properties)
	} catch {
		// analytics must never break the CRM
	}
}
