import { describe, expect, it } from 'vitest'

import { phoneProblem } from '$lib/crm/format'

// The backend silently saves an unusable number as "no phone", so the New Lead form must refuse
// it with a reason instead of creating a lead nobody can call.
describe('phoneProblem: what the New Lead form accepts as a phone number', () => {
	it.each([
		'050 123 4567',
		'0501234567',
		'+971 50 123 4567',
		'00971501234567',
		'+44 7700 900123',
		'(050) 123-4567'
	])('accepts %s', (phone) => expect(phoneProblem(phone)).toBe(''))

	it('refuses an empty or blank phone', () => {
		expect(phoneProblem('')).toBe('Enter a phone number.')
		expect(phoneProblem('   ')).toBe('Enter a phone number.')
	})

	it('refuses letters, which the backend would turn into no phone at all', () => {
		expect(phoneProblem('abc')).toMatch(/only digits/)
		expect(phoneProblem('050 ABC 4567')).toMatch(/only digits/)
	})

	it('refuses two numbers in one box, which the backend would join into one invalid number', () => {
		expect(phoneProblem('0501234567 / 0559876543')).toBe('Enter one phone number only.')
		expect(phoneProblem('0501234567, 0559876543')).toBe('Enter one phone number only.')
		expect(phoneProblem('0501234567; 0559876543')).toBe('Enter one phone number only.')
	})

	it('refuses numbers too short or too long to be real', () => {
		expect(phoneProblem('12')).toBe('This phone number is too short.')
		expect(phoneProblem('12345678901234567')).toBe('This phone number is too long.')
	})
})
