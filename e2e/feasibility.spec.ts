import { test, expect } from '@playwright/test';

/**
 * Tests for the feasibility wizard. Runs with authenticated session.
 */

test('feasibility page loads without redirecting', async ({ page }) => {
	await page.goto('/feasibility');
	await expect(page).not.toHaveURL(/\/auth/);
});

test('feasibility page shows the saved studies workspace on load', async ({ page }) => {
	await page.goto('/feasibility');
	await expect(page.getByRole('heading', { name: 'Saved studies workspace' })).toBeVisible({
		timeout: 10_000
	});
	await expect(page.getByRole('button', { name: 'New study' }).first()).toBeVisible();
});

test('feasibility new-study flow exposes plot search', async ({ page }) => {
	await page.goto('/feasibility');
	await page.getByRole('button', { name: 'New study' }).first().click();
	await expect(page.getByRole('heading', { name: 'Set up the underwriting workspace' })).toBeVisible({
		timeout: 10_000
	});
	await page.getByRole('button', { name: 'Select plot' }).click();
	await expect(page.getByPlaceholder('Plot, project, community')).toBeVisible({ timeout: 10_000 });
});

test('feasibility new-study setup shows core workflow controls', async ({ page }) => {
	await page.goto('/feasibility');
	await page.getByRole('button', { name: 'New study' }).first().click();
	await expect(page.getByLabel('Study name')).toBeVisible({
		timeout: 10_000
	});
	await expect(page.getByRole('button', { name: 'Build to sell' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Select plot' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Create study' })).toBeDisabled();
});

test('plot search preserves its identifier when selecting a study site', async ({ page }) => {
    const plot = {
        plot_number: '001', community_name: 'Migration Community', project_name: 'Migration Plot',
        plot_area_sqm: 1000, max_gfa_sqm: 5000, max_height: 'G+10', max_coverage: '60%',
        gfa_type: 'Residential', is_verified: true, land_use_summary: ['Residential: Apartment']
    };
    let searched = false;
    await page.route('**/api/v1/feasibility/plots?*', route => {
        const query = new URL(route.request().url()).searchParams;
        if (query.get('q') === '001') searched = true;
        return route.fulfill({ json: [plot] });
    });
    await page.route('**/api/v1/projects/dda-plots?*', route => route.fulfill({ json: [] }));
    await page.goto('/feasibility');
    await page.getByRole('button', { name: 'New study' }).first().click();
    await page.getByLabel('Study name').fill('Plot migration contract');
    await page.getByRole('button', { name: 'Select plot', exact: true }).click();
    await page.getByPlaceholder('Plot, project, community').fill('001');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect.poll(() => searched).toBe(true);
    await page.getByRole('button').filter({ has: page.getByText('001', { exact: true }) }).click();
    await expect(page.getByRole('paragraph').filter({ hasText: /^Plot 001$/ })).toBeVisible();
    await page.getByRole('button', { name: 'Use plot', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Create study', exact: true })).toBeEnabled();
    await expect(page.getByText('Migration Plot', { exact: true }).first()).toBeVisible();
});
