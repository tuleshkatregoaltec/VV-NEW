import { test, expect } from '@playwright/test';

/**
 * Tests for the main dashboard. Runs with authenticated session.
 */

test('dashboard loads and shows navigation', async ({ page }) => {
	await page.goto('/dashboard');
	await expect(page).toHaveURL(/\/dashboard/);
	// Market sidebar should be present
	await expect(page.getByRole('navigation', { name: 'Market sections' })).toBeVisible();
});

test('dashboard does not redirect to /auth', async ({ page }) => {
	await page.goto('/dashboard');
	await expect(page).not.toHaveURL(/\/auth/);
});
