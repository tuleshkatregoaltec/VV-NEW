/**
 * Display formatting only. The API sends whole AED and YYYY-MM-DD dates (Dubai); these turn them
 * into "AED 3,400,000", "3.4m", "Tue 29 Sep".
 */
import type { DealType } from './api/types'

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US')
export const aed = (n: number) => `AED ${fmt(n)}`
/** Tight spots: 3.4m, or 95,000 under a million. */
export const short = (n: number) => (n >= 1e6 ? `${+(n / 1e6).toFixed(2)}m` : fmt(n))

/** Sale → AED 3,400,000 · rent → 95,000 a year */
export const price = (type: DealType, n: number) => (type === 'rent' ? `${fmt(n)} a year` : aed(n))
/** Sale → AED 3.4m · rent → 95,000 / yr */
export const priceShort = (type: DealType, n: number) =>
	type === 'rent' ? `${fmt(n)} / yr` : `AED ${short(n)}`

export const fileSize = (b: number) =>
	b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`

const utc = (iso: string) => new Date(`${iso}T00:00:00Z`)

export function dayLabel(iso: string): string {
	const d = utc(iso)
	return `${DOW[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`
}

export function shortDate(iso: string): string {
	const d = utc(iso)
	return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`
}

/** 10 Nov 2025 */
export function fullDate(iso: string): string {
	const d = utc(iso)
	return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

export function weekdayShort(iso: string): string {
	return DOW[utc(iso).getUTCDay()]
}

export function dayOfMonth(iso: string): number {
	return utc(iso).getUTCDate()
}

export function addDays(iso: string, n: number): string {
	return new Date(utc(iso).getTime() + n * 864e5).toISOString().slice(0, 10)
}

export function monthOf(iso: string): string {
	return iso.slice(0, 7)
}

export function addMonths(ym: string, n: number): string {
	const [y, m] = ym.split('-').map(Number)
	const d = new Date(Date.UTC(y, m - 1 + n, 1))
	return d.toISOString().slice(0, 7)
}

export function monthTitle(ym: string): string {
	const [y, m] = ym.split('-').map(Number)
	return `${MON[m - 1]} ${y}`
}

/** 42 days (6 weeks, Monday first) covering the month. */
export function monthGrid(ym: string): string[] {
	const first = `${ym}-01`
	const dow = utc(first).getUTCDay()
	const start = addDays(first, -((dow + 6) % 7))
	return Array.from({ length: 42 }, (_, i) => addDays(start, i))
}

/** Pick "1 lead" / "2 leads". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/**
 * Why a typed phone number cannot be saved, or '' when it can. The backend keeps only numbers of
 * 7 or more digits and silently stores anything else as no phone, so the form must refuse it.
 */
export function phoneProblem(raw: string): string {
	const value = raw.trim()
	if (!value) return 'Enter a phone number.'
	if (/[/,;|\n]/.test(value)) return 'Enter one phone number only.'
	if (/[^\d\s+().-]/.test(value)) return 'Use only digits, spaces and + ( ) - in a phone number.'
	const digits = value.replace(/\D/g, '').length
	if (digits < 7) return 'This phone number is too short.'
	if (digits > 15) return 'This phone number is too long.'
	return ''
}
