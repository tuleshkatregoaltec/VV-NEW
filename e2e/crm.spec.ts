import { test, expect } from '@playwright/test';

/**
 * The CRM at /crm is the v2 agent CRM (sidebar shell). It shows backend data only, so these
 * checks guard the route and shell, not the records.
 */

const SECTIONS = ['Home', 'Leads', 'Inbox', 'Matches', 'Owners', 'Deals', 'Calendar', 'Assets', 'Settings'];

// A section link's name gains its count once counts load ("Leads 3 new"), so match the label only.
const sectionLink = (name: string) => new RegExp(`^${name}\\b`);

test('signed-in agent gets the CRM shell at /crm inside the app navigation', async ({ page }) => {
	await page.goto('/crm');
	await expect(page).not.toHaveURL(/\/auth/);
	// The main app's nav stays, so the agent can leave the CRM.
	await expect(page.getByRole('link', { name: 'CRM', exact: true }).first()).toBeVisible();
	const crmNav = page.getByRole('navigation', { name: 'CRM sections' });
	for (const section of SECTIONS) {
		await expect(crmNav.getByRole('link', { name: sectionLink(section) })).toBeVisible();
	}
	// AppFrame owns the one <main> landmark; the CRM shell must not add a second.
	await expect(page.locator('main')).toHaveCount(1);
});

test('CRM sections navigate within /crm', async ({ page }) => {
	await page.goto('/crm');
	await page.getByRole('navigation', { name: 'CRM sections' }).getByRole('link', { name: sectionLink('Leads') }).click();
	await expect(page).toHaveURL(/\/crm\/leads$/);
	await expect(
		page.getByRole('navigation', { name: 'CRM sections' }).getByRole('link', { name: sectionLink('Leads') })
	).toHaveAttribute('aria-current', 'page');
});

test('the retired Connections page sends agents to the Inbox', async ({ page }) => {
	await page.goto('/crm/connections');
	await expect(page).toHaveURL(/\/crm\/inbox$/);
});

test('the CRM shows no example-data screens: no Market mode, Assistant, Manager view or Ask chat', async ({ page }) => {
	await page.goto('/crm');
	await expect(page.getByRole('navigation', { name: 'CRM sections' })).toBeVisible();
	await expect(page.getByRole('radiogroup', { name: 'Mode' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Ask Vitevue' })).toHaveCount(0);
	for (const href of ['/crm/market', '/crm/assistant', '/crm/manager']) {
		// SSR is off, so the client router renders the app's error page for an unknown route.
		await page.goto(href);
		await expect(page.getByRole('heading', { level: 1, name: '404' })).toBeVisible();
	}
});
