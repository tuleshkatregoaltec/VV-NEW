import { describe, expect, it } from 'vitest'

import { previewOwnerFile } from '$lib/crm/api/backend'

/* An empty owners file is stopped in the preview with one clear line, so nothing is uploaded;
   a file with rows is still ready to import. */
describe('owner file preview of an empty file', () => {
	const EMPTY = 'File is empty, so nothing will be imported.'
	const csv = (text: string) => new File([text], 'owners.csv', { type: 'text/csv' })

	it('stops a 0-byte file', async () => {
		expect((await previewOwnerFile(csv(''))).problem).toBe(EMPTY)
	})

	it('stops a file with headers only', async () => {
		const preview = await previewOwnerFile(csv('NameEn,Mobile,UnitNumber\n'))
		expect(preview.problem).toBe(EMPTY)
		expect(preview.rows).toBe(0)
	})

	it('lets a file with rows through', async () => {
		const preview = await previewOwnerFile(csv('NameEn,Mobile,UnitNumber\nAli,0501234567,101\n'))
		expect(preview.problem).toBeNull()
		expect(preview.rows).toBe(1)
	})
})
