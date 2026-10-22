import { test, expect } from '@playwright/test';

for (const kind of ['sales', 'rentals']) {
    test(`${kind} CSV export uses the existing endpoint and download format`, async ({ page }) => {
        let exported = false;
        await page.route(`**/api/v1/transactions/${kind}/export?*`, async route => {
            expect(new URL(route.request().url()).searchParams.get('format')).toBe('csv');
            exported = true;
            await route.fulfill({ contentType: 'text/csv', body: '\uFEFFID,Value\r\nfixture,100\r\n' });
        });
        await page.goto(`/transactions/${kind}`);
        await expect(page.getByRole('button', { name: 'CSV', exact: true })).toBeEnabled();
        const downloadPromise = page.waitForEvent('download');
        await page.getByRole('button', { name: 'CSV', exact: true }).click();
        const download = await downloadPromise;
        expect(download.suggestedFilename()).toMatch(new RegExp(`^${kind}_export_\\d+\\.csv$`));
        expect(exported).toBe(true);
        expect(await download.failure()).toBeNull();
    });
}
