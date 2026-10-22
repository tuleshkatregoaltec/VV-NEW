/**
 * The "Matching" lines of an owner's unit: each thing the owner file says, next to DLD's latest
 * sale of the unit, and whether they agree. The rules are the import's own
 * (backend/app/crm/ownership.py): same sale = dates within 2 days, price within 2%, and a DLD sale
 * after the file's date means the owner has sold since. Property type is shown but not used.
 */
import type { OwnerUnitCheck } from './api/types'
import { ownerPropertyKind } from './config'
import { aed, fullDate } from './format'

export type MatchResult = 'match' | 'differs' | 'info' | 'none'

export interface MatchLine {
	label: string
	file: string
	dld: string
	result: MatchResult
	note: string
}

const DAY = 86_400_000
const SAME_SALE_DAYS = 2
const PRICE_TOLERANCE = 0.02

const unitKey = (v: string) => v.toUpperCase().replace(/\s+/g, '')
const day = (v: string | null) => (v ? Date.parse(v.slice(0, 10)) : Number.NaN)

export function matchLines(c: OwnerUnitCheck): MatchLine[] {
	const lines: MatchLine[] = []

	// Unit
	const fileUnit = c.imported.unit
	const dldUnit = c.dld.unit
	lines.push({
		label: 'Unit',
		file: fileUnit || '—',
		dld: dldUnit || '—',
		...(!fileUnit || !dldUnit
			? {
					result: 'none' as const,
					note: dldUnit ? 'No unit in the file' : 'No DLD sale found for this unit'
				}
			: unitKey(fileUnit) === unitKey(dldUnit)
				? { result: 'match' as const, note: 'Same unit' }
				: { result: 'differs' as const, note: 'Different unit' })
	})

	// Sale date: decides whether DLD's latest sale is the file's sale, a later one, or older.
	const fileDay = day(c.imported.date)
	const dldDay = day(c.dld.date)
	const days = Math.round((dldDay - fileDay) / DAY)
	const sameSale = Number.isFinite(days) && Math.abs(days) <= SAME_SALE_DAYS
	lines.push({
		label: 'Sale date',
		file: c.imported.date ? fullDate(c.imported.date) : '—',
		dld: c.dld.date ? fullDate(c.dld.date) : '—',
		...(!Number.isFinite(days)
			? {
					result: 'none' as const,
					note: c.imported.date ? 'No DLD sale to compare' : 'No date in the file'
				}
			: sameSale
				? { result: 'match' as const, note: `Same sale (within ${SAME_SALE_DAYS} days)` }
				: days > 0
					? {
							result: 'differs' as const,
							note: `DLD has a later sale, ${days.toLocaleString('en-US')} days after the file's date: sold since`
						}
					: {
							result: 'info' as const,
							note: `The file's date is ${Math.abs(days).toLocaleString('en-US')} days after DLD's latest sale`
						})
	})

	// Price: only compared when both dates are the same sale.
	const fileAed = c.imported.aed
	const dldAed = c.dld.aed
	const gap = fileAed && dldAed ? Math.abs(fileAed - dldAed) / dldAed : null
	lines.push({
		label: 'Price',
		file: fileAed ? aed(fileAed) : '—',
		dld: dldAed ? aed(dldAed) : '—',
		...(gap === null
			? {
					result: 'none' as const,
					note: fileAed ? 'No DLD price to compare' : 'No price in the file'
				}
			: !sameSale
				? { result: 'none' as const, note: 'Not compared: not the same sale' }
				: gap <= PRICE_TOLERANCE
					? { result: 'match' as const, note: `Within ${PRICE_TOLERANCE * 100}%` }
					: { result: 'differs' as const, note: `Differs by ${(gap * 100).toFixed(1)}%` })
	})

	// Property type: shown for reference; the import does not match on it.
	const fileType = c.imported.type
	const dldType = c.dld.type
	const fileKind = ownerPropertyKind(fileType)
	lines.push({
		label: 'Property type',
		file: fileType || '—',
		dld: dldType || '—',
		...(!fileType || !dldType
			? { result: 'none' as const, note: 'Not used for matching' }
			: fileKind !== 'unspecified' && fileKind === ownerPropertyKind(dldType)
				? { result: 'match' as const, note: 'Same kind · not used for matching' }
				: { result: 'info' as const, note: 'Different · not used for matching' })
	})

	// Role: a Seller in the file is a former owner whatever DLD shows.
	const role = c.imported.role
	lines.push({
		label: 'Role',
		file: role || '—',
		dld: '—',
		...(!role
			? { result: 'none' as const, note: 'No role in the file' }
			: /seller/i.test(role)
				? { result: 'info' as const, note: 'Seller in the file: a former owner' }
				: { result: 'info' as const, note: 'Buyer in the file: checked against DLD sales' })
	})

	return lines
}
