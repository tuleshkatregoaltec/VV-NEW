import { test, expect } from '@playwright/test';

test('conversation creation, rename, reload and deletion use persisted state', async ({ page }) => {
    await page.goto('/chat');
    const created = page.waitForResponse(response => response.url().endsWith('/api/v1/chat/conversations') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    const response = await created;
    expect(response.ok()).toBeTruthy();
    const conversation = await response.json();
    const title = `Migration chat ${conversation.id}`;
    await page.getByRole('button', { name: 'Select New conversation. Double-click to rename.' }).dblclick();
    // The search box has a placeholder; the inline rename field does not.
    const input = page.locator('aside input[type="text"]:not([placeholder])');
    await input.fill(title);
    const updated = page.waitForResponse(response => response.url().endsWith(`/conversations/${conversation.id}`) && response.request().method() === 'PATCH');
    await input.press('Enter');
    expect((await updated).ok()).toBeTruthy();
    await page.reload();
    const select = page.getByRole('button', { name: `Select ${title}. Double-click to rename.` });
    await expect(select).toBeVisible();
    await select.click();
    await select.locator('..').getByRole('button', { name: 'Conversation options' }).click();
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click();
    const deleted = page.waitForResponse(response => response.url().endsWith(`/conversations/${conversation.id}`) && response.request().method() === 'DELETE');
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    expect((await deleted).status()).toBe(204);
    await page.reload();
    await expect(select).toHaveCount(0);
});

test('chat renders existing SSE artifacts and keeps them after message refresh', async ({ page }) => {
    const now = '2026-05-05T00:00:00+00:00';
    const conversation = { id: 987654, user_id: 'browser-fixture', title: 'SSE contract', created_at: now, updated_at: now };
    const artifact = {
        id: 'show_chart:call-1', type: 'custom_chart', status: 'ready', title: 'Migration market chart', created_at: now,
        query: { dataset: 'sales', group_by: 'area' },
        data: { chart_type: 'horizontal_bar', metric: 'transaction_count', metric_label: 'Transactions', dimension: 'area', dimension_label: 'Area', points: [{ label: 'Dubai Marina', value: 42, record_count: 42 }], series: [] }
    };
    let sent = false;
    await page.route('**/api/v1/chat/conversations?*', route => route.fulfill({ json: { conversations: [conversation], total: 1 } }));
    await page.route('**/api/v1/chat/conversations/987654/messages?*', route => route.fulfill({ json: sent ? [
        { id: 1, conversation_id: conversation.id, role: 'user', content: 'Show market activity', created_at: now, meta_data: {} },
        { id: 2, conversation_id: conversation.id, role: 'assistant', content: 'Existing streaming contract preserved.', created_at: now, meta_data: { artifacts: [artifact], total_tokens: 17 } }
    ] : [] }));
    await page.route('**/api/v1/chat/conversations/987654/messages/stream', route => {
        expect(route.request().postDataJSON()).toEqual({ message: 'Show market activity' });
        sent = true;
        const events = [
            { type: 'thinking' }, { type: 'tool_call_start', tool: 'show_chart', tool_call_id: 'call-1' },
            { type: 'artifact_opened', artifact: { ...artifact, status: 'loading' } },
            { type: 'artifact_updated', artifact },
            { type: 'tool_call_result', tool: 'show_chart', tool_call_id: 'call-1' },
            { type: 'content', content: 'Existing streaming contract preserved.' }, { type: 'done' }
        ];
        return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') });
    });
    await page.goto('/chat');
    await page.getByRole('button', { name: 'Select SSE contract. Double-click to rename.' }).click();
    await page.getByPlaceholder('Ask anything about Dubai real estate...').fill('Show market activity');
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect(page.getByText('Existing streaming contract preserved.', { exact: true })).toBeVisible();
    await expect(page.getByText('Migration market chart', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Stop streaming' })).toHaveCount(0);
    await page.reload();
    await page.getByRole('button', { name: 'Select SSE contract. Double-click to rename.' }).click();
    await expect(page.getByText('Migration market chart', { exact: true })).toBeVisible();
});
