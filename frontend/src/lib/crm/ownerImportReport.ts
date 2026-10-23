/**
 * Owner import report: the counts and mapping checks a manager reads after an upload. Pure
 * functions over what /contact-imports returns (summary + sheet mappings); no fetching.
 * The checks follow the backend's own rules in backend/app/crm/import_parsing.py.
 */
import type { CrmContactImportSummary, CrmSheetMapping } from '$lib/api/generated/hey-api/types.gen'
import type { Tone } from './api/types'

export type ClaimStatus =
	| 'verified_current_owner'
	| 'probable_current_owner'
	| 'unit_linked_unverified'
	| 'former_owner'
	| 'conflicting_claim'
	| 'insufficient_property_data'
	| 'registry_not_observed'
	| 'unmatched'
	/** Report-only: mortgage rows taken out of "unverified" and "needs review". */
	| 'mortgage'

/** A DLD property was found for the row. */
export const MATCHED_STATUSES: ClaimStatus[] = [
	'verified_current_owner',
	'probable_current_owner',
	'former_owner',
	'conflicting_claim',
	'unit_linked_unverified'
]
/** No DLD property could be found for the row. */
export const FAILED_STATUSES: ClaimStatus[] = [
	'insufficient_property_data',
	'registry_not_observed',
	'unmatched'
]

export const STATUS_INFO: Record<ClaimStatus, { label: string; meaning: string; tone: Tone }> = {
	verified_current_owner: {
		label: 'Verified owner',
		meaning: 'Date and price match the latest DLD sale',
		tone: 'green'
	},
	probable_current_owner: {
		label: 'Probable owner',
		meaning: 'Ownership fits available sales evidence but still needs review',
		tone: 'green'
	},
	unit_linked_unverified: {
		label: 'Property found, owner unverified',
		meaning: 'A unit or plot is identified; ownership still needs evidence',
		tone: 'amber'
	},
	former_owner: {
		label: 'Former owner',
		meaning: 'Listed as seller, or a later sale follows the matched acquisition',
		tone: 'neutral'
	},
	conflicting_claim: {
		label: 'Conflicts with DLD',
		meaning: 'Price or date does not fit the DLD sales',
		tone: 'red'
	},
	insufficient_property_data: {
		label: 'Property needs resolution',
		meaning: 'No unique property match; the row explains the remaining gap',
		tone: 'red'
	},
	registry_not_observed: {
		label: 'Not found in available sales',
		meaning: 'The unit is absent from the sales data currently available to us',
		tone: 'red'
	},
	unmatched: {
		label: 'Needs review',
		meaning: 'Property identity or the event’s ownership effect is unverified',
		tone: 'amber'
	},
	mortgage: {
		label: 'Mortgage',
		meaning: 'A mortgage or finance registration; it does not show who bought the property',
		tone: 'neutral'
	}
}

/**
 * How the backend decides each status (backend/app/crm/ownership.py) and what to do about it, for
 * the ⓘ on the import report's status tiles. Sales are DLD sales of the matched unit.
 */
export const STATUS_HELP: Record<ClaimStatus, { how: string; todo: string }> = {
	verified_current_owner: {
		how: 'The resolved property, source date and amount match an observed sale, with no subsequent sale in available history.',
		todo: 'Strong sale-based evidence. The registry match does not independently verify the person’s identity or exclude unobserved transfers.'
	},
	probable_current_owner: {
		how: 'A unique property and amount fit within a bounded date window, price evidence is missing, or the file is newer than available sales coverage. Date shifts remain provisional.',
		todo: 'Likely the owner; check before relying on it.'
	},
	unit_linked_unverified: {
		how: 'A unit, lease or DLD plot was linked. A mortgage, gift or owner snapshot alone does not establish a verified sale acquisition.',
		todo: 'Check ownership another way.'
	},
	former_owner: {
		how: 'An explicit sale lists this person as Seller, or the sales registry shows a later sale after their acquisition.',
		todo: 'Not the current owner: a past relationship, not a lead.'
	},
	conflicting_claim: {
		how: 'A DLD sale is within ±2 days but the price differs by more than 2% (45%), or the date does not fit the unit’s sales history (40%).',
		todo: 'Review the row: the file and DLD disagree.'
	},
	insufficient_property_data: {
		how: 'The source identifiers and location/date/amount search did not identify one property. The row distinguishes missing evidence, no matching sale and multiple candidates.',
		todo: 'Review the retained land, building and property identifiers; do not replace them with a guessed unit.'
	},
	registry_not_observed: {
		how: 'The row has a unit identifier, but it was not found in the sales dataset currently available to this CRM. This does not establish that DLD has no record.',
		todo: 'Check the source identifier and dataset coverage.'
	},
	unmatched: {
		how: 'The unit number exists in several DLD buildings and the file’s building / project did not clearly pick one (needs a score of 55 and a 12-point lead). Confidence 20%.',
		todo: 'Write the building name the way DLD does, then upload again.'
	},
	mortgage: {
		how: 'Rows that would be “Property found, owner unverified” or “Needs review” and whose procedure is a mortgage or finance registration. In these rows the Seller is the owner who took the loan and the Buyer is the bank. A mortgage followed by a later sale stays “Former owner”.',
		todo: 'Treat the Seller as the owner on the mortgage date; check for a later sale before contacting.'
	}
}

/** The summary's count fields (it also holds a per-reason map and a flag). */
type SummaryCount = {
	[K in keyof CrmContactImportSummary]-?: CrmContactImportSummary[K] extends number | undefined
		? K
		: never
}[keyof CrmContactImportSummary]

const SUMMARY_KEY: Record<Exclude<ClaimStatus, 'mortgage'>, SummaryCount> = {
	verified_current_owner: 'verified_current_owners',
	probable_current_owner: 'probable_current_owners',
	unit_linked_unverified: 'unit_linked_unverified',
	former_owner: 'former_owners',
	conflicting_claim: 'conflicting_claims',
	insufficient_property_data: 'insufficient_property_data',
	registry_not_observed: 'registry_not_observed',
	unmatched: 'unmatched'
}

export interface ImportCounts {
	rows: number
	kept: number
	/** Distinct units, villas or plots among the kept rows; null for imports made before it. */
	properties: number | null
	sourceProperties: CrmContactImportSummary['source_property_counts']
	salesHistoryRows: number | null
	plotOnlyRows: number | null
	merged: number
	dropped: number
	matched: number
	failed: number
	readyToContact: number
	rentalLeads: number
	/** matched ÷ kept; null when nothing was kept. */
	matchRate: number | null
	byStatus: Record<ClaimStatus, number>
	/** Dropped rows per reason; empty for imports made before the dropped-rows log. */
	droppedByReason: Partial<Record<DropReason, number>>
	/** Whether the dropped rows can be listed (the log was saved at upload). */
	droppedLog: boolean
}

/**
 * `mortgageByStatus` is the import's mortgage rows per stored status (unverified / needs review);
 * they are reported in their own "Mortgage" tile. Matched and failed totals are unchanged.
 */
export function importCounts(
	summary: CrmContactImportSummary,
	mortgageByStatus: Partial<Record<string, number>> = {}
): ImportCounts {
	const n = (key: SummaryCount) => summary[key] ?? 0
	const byStatus = Object.fromEntries(
		(Object.keys(SUMMARY_KEY) as Exclude<ClaimStatus, 'mortgage'>[]).map((s) => [
			s,
			n(SUMMARY_KEY[s])
		])
	) as Record<ClaimStatus, number>
	const sum = (list: ClaimStatus[]) => list.reduce((t, s) => t + byStatus[s], 0)
	const rows = n('total_rows')
	const kept = n('candidate_rows')
	const merged = n('duplicate_rows_collapsed')
	const matched = sum(MATCHED_STATUSES)
	const failed = sum(FAILED_STATUSES)
	const unverifiedMortgages = mortgageByStatus.unit_linked_unverified ?? 0
	const reviewMortgages = mortgageByStatus.unmatched ?? 0
	byStatus.unit_linked_unverified -= unverifiedMortgages
	byStatus.unmatched -= reviewMortgages
	byStatus.mortgage = unverifiedMortgages + reviewMortgages
	return {
		rows,
		kept,
		properties: summary.distinct_properties ?? null,
		sourceProperties: summary.source_property_counts ?? null,
		salesHistoryRows: summary.rows_with_sales_history ?? null,
		plotOnlyRows: summary.plot_only_rows ?? null,
		merged,
		dropped: Math.max(0, rows - kept - merged),
		matched,
		failed,
		readyToContact: n('contact_ready'),
		rentalLeads: n('rental_opportunities'),
		matchRate: kept ? matched / kept : null,
		droppedByReason: summary.dropped_by_reason ?? {},
		droppedLog: summary.dropped_log ?? false,
		byStatus
	}
}

export interface SheetCheck {
	level: 'error' | 'warning'
	code: 'no_name' | 'no_role' | 'no_property' | 'no_unit' | 'no_price' | 'no_contact'
	text: string
}

type SheetLike = Pick<CrmSheetMapping, 'sheet_family' | 'field_map' | 'warnings'>

/** The backend's own warning when it takes the project name from the file name. */
const INFERRED_PROJECT = 'Property name will be inferred as'

/** What the sheet's mapping means for its rows, errors first. */
export function mappingChecks(sheet: SheetLike): SheetCheck[] {
	const has = (field: string) => Boolean(sheet.field_map?.[field])
	const checks: SheetCheck[] = []
	if (!has('contact_name'))
		checks.push({ level: 'error', code: 'no_name', text: 'No name column: every row is dropped.' })
	if (sheet.sheet_family === 'transaction_party' && !has('party_role'))
		checks.push({
			level: 'warning',
			code: 'no_role',
			text: 'No party-role column: source records are retained for ownership review.'
		})
	// Without date + price the backend has no sale to recover the property from, so it needs a
	// location (building, project, master project or a name inferred from the file) and a unit.
	const saleSignature = has('transaction_date') && has('transaction_price_aed')
	const location =
		has('building_name') ||
		has('project_name') ||
		has('master_project') ||
		(sheet.warnings ?? []).some((w) => w.startsWith(INFERRED_PROJECT))
	if (!location && !saleSignature)
		checks.push({
			level: 'error',
			code: 'no_property',
			text: "No building or project and no date + price: rows can't be tied to a property."
		})
	else if (!has('unit_number') && !saleSignature)
		checks.push({
			level: 'error',
			code: 'no_unit',
			text: "No unit column and no date + price: rows can't be matched to a unit."
		})
	if (has('transaction_date') && !has('transaction_price_aed'))
		checks.push({
			level: 'warning',
			code: 'no_price',
			text: 'No price column: sale-date matching is weaker.'
		})
	if (!has('phone_primary') && !has('phone_alternate') && !has('email_primary'))
		checks.push({ level: 'warning', code: 'no_contact', text: 'No phone or email column.' })
	return checks
}

/** An import worth a second look: it failed, or a sheet has an error-level check. */
export function isFlagged(status: string, sheets: SheetLike[]): boolean {
	return (
		status === 'failed' || sheets.some((s) => mappingChecks(s).some((c) => c.level === 'error'))
	)
}

/** The page start to show once the rows total changed: the last page that still has rows. */
export function clampOffset(offset: number, total: number, limit: number): number {
	if (!total) return 0
	return Math.min(offset, Math.floor((total - 1) / limit) * limit)
}

/** How to fix a file so its rows stop failing, per failed status. */
const FAILURE_FIX: Partial<Record<ClaimStatus, string>> = {
	insufficient_property_data:
		'Add a building (or project) and a unit column, or a sale date and price, then upload again.',
	registry_not_observed:
		'Check the unit number and building spelling; DLD may not list this unit yet.',
	unmatched:
		'Add or correct the building name so the unit can be told apart from same-numbered units elsewhere.'
}

export interface FailureReason {
	status: ClaimStatus
	label: string
	meaning: string
	fix: string
	count: number
}

/** Why rows failed to match DLD: each failed status with rows, biggest first. */
export function failureReasons(c: ImportCounts): FailureReason[] {
	return FAILED_STATUSES.filter((s) => c.byStatus[s] > 0)
		.map((s) => ({
			status: s,
			label: STATUS_INFO[s].label,
			meaning: STATUS_INFO[s].meaning,
			fix: FAILURE_FIX[s] ?? '',
			count: c.byStatus[s]
		}))
		.sort((a, b) => b.count - a.count)
}

const MISSING = new Set(['', '-', 'n/a', 'na', 'none', 'null', 'unknown'])
const present = (v: string | null | undefined) =>
	!MISSING.has(
		String(v ?? '')
			.trim()
			.toLowerCase()
	)

/**
 * Why a row is "Insufficient property data", from the row itself: which of unit and building /
 * project the file was missing, and whether it had a sale date and price to look the property up
 * by. (The backend gives one generic sentence for all of these.)
 */
export function insufficientReason(row: {
	building: string
	unit: string
	date: string | null
	price: number | null
}): string {
	const unit = present(row.unit)
	const building = present(row.building)
	const missing =
		!unit && !building
			? 'No unit number and no building or project in the file'
			: !unit
				? 'No unit number in the file'
				: !building
					? 'No building or project in the file'
					: 'The unit could not be tied to a property'
	return row.date && row.price
		? `${missing}. Its sale date and price did not match one DLD sale in this location.`
		: `${missing}, and no sale date and price to look the property up in DLD.`
}

export type DropReason = 'no_name' | 'not_buyer_seller' | 'not_a_sale' | 'no_unit'

/** Where in the import a row was dropped: while reading the file, or after checking DLD. */
export type DropStage = 'before_match' | 'after_match'

export const DROP_STAGE_LABEL: Record<DropStage, string> = {
	before_match: 'Before DLD matching',
	after_match: 'After DLD matching'
}

/** Why an import did not keep a row, in the order the import checks them. */
export const DROP_REASON_INFO: Record<
	DropReason,
	{ label: string; meaning: string; stage: DropStage }
> = {
	no_name: {
		label: 'No name',
		meaning: 'The name cell is empty, so there is no owner to keep',
		stage: 'before_match'
	},
	not_buyer_seller: {
		label: 'Not Buyer/Seller',
		meaning: 'A transaction row for another party (bank, mortgagee, developer…)',
		stage: 'before_match'
	},
	not_a_sale: {
		label: 'Not a sale',
		meaning: 'A Buyer/Seller row for a mortgage, gift, grant or other non-sale procedure',
		stage: 'before_match'
	},
	no_unit: {
		label: 'No unit',
		meaning: 'No unit in the file, and none could be recovered from DLD',
		stage: 'after_match'
	}
}

/** The values a dropped-row explanation needs (a row of the dropped-rows log). */
export interface DroppedRowFacts {
	reason: DropReason
	role: string
	procedure: string
	date: string
	price: string
}

export interface DropDetail {
	stage: DropStage
	/** What happened to this row, with the file's own values. */
	what: string
	/** The rule the import applies. */
	rule: string
	/** What would have kept the row. */
	fix: string
}

/**
 * The full reason one row was dropped, in the words of the import's rules
 * (backend import_parsing._candidate_from_row / _is_transfer_candidate and
 * identity._drop_candidates_without_unit).
 */
export function dropDetail(r: DroppedRowFacts): DropDetail {
	const stage = DROP_REASON_INFO[r.reason].stage
	switch (r.reason) {
		case 'no_name':
			return {
				stage,
				what: 'The name cell of this row is empty.',
				rule: 'Every kept row needs an owner name; a row without one has no one to contact.',
				fix: 'Fill in the owner name in the file and upload it again.'
			}
		case 'not_buyer_seller':
			return {
				stage,
				what: r.role
					? `The role is “${r.role}”, which is not Buyer or Seller.`
					: 'The role is empty: this sheet has no Buyer/Seller column mapped, or the cell is blank.',
				rule: 'On a transaction sheet only the Buyer or Seller of a sale is an owner; banks, mortgagees and other parties are not kept.',
				fix: r.role
					? 'Nothing to fix if this party is not an owner.'
					: 'If the sheet is a list of owners, map its role column (or import it as a contact list) and upload again.'
			}
		case 'not_a_sale':
			return {
				stage,
				what: `The procedure is “${r.procedure}”, which is not a sale.`,
				rule: 'A Buyer/Seller row is kept only when its procedure is empty or contains “sale”, “sell” or “delayed sell”. Mortgages, gifts and grants do not change who bought the property, and their amount is not a sale price, so they cannot be checked against DLD sales.',
				fix: 'Nothing to fix: the owner is usually kept from the row of the actual sale.'
			}
		case 'no_unit':
			return {
				stage,
				what: `The file has no unit for this row, and DLD matching could not find one${
					r.date || r.price
						? ` from its date and price (${[r.date, r.price].filter(Boolean).join(', ')})`
						: ''
				}.`,
				rule: 'A row needs a unit to identify a property; it is dropped only after DLD matching has tried to recover the unit.',
				fix: 'Add the unit number in the file and upload it again.'
			}
	}
}
