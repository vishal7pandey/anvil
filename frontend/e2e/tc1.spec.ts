import { test, expect } from '@playwright/test';

test.describe('TC1 Session Lifecycle and Run Stream E2E', () => {
  test('creates session, streams run events, and renders run record', async ({ page }) => {
    // 1. Navigate to Anvil Web UI
    await page.goto('http://localhost:5173/');
    await expect(page.getByText('Anvil', { exact: true })).toBeVisible();

    // 2. Open New Session dialog
    const newSessionButton = page.getByRole('button', { name: 'New Session' });
    await expect(newSessionButton).toBeVisible();
    await newSessionButton.click();

    // Verify dialog elements
    await expect(page.getByRole('heading', { name: 'New Session' })).toBeVisible();

    // Fill form fields
    const titleInput = page.getByPlaceholder('e.g. Fix pthread_mutex_init defect');
    const repoInput = page.getByPlaceholder('rtems');

    const sessionTitle = `TC1 E2E Run ${Date.now()}`;
    await titleInput.fill(sessionTitle);
    await repoInput.fill('vishal7pandey/rtems');

    // Submit new session
    const createButton = page.getByRole('button', { name: 'Create' });
    await expect(createButton).toBeEnabled();
    await createButton.click();

    // 3. Verify session was created and selected in sidebar
    await expect(page.getByText(sessionTitle)).toBeVisible({ timeout: 10000 });

    // 4. Send a message to start a short no-build run
    const chatInput = page.getByPlaceholder('Ask the agent to build, fix, or upgrade…');
    await expect(chatInput).toBeEnabled();

    const promptText = 'Review RTEMS issue #5776 defect report and summarize the parameter check requirement without running a build.';
    await chatInput.fill(promptText);

    const sendButton = page.getByRole('button', { name: 'Send' });
    await expect(sendButton).toBeEnabled();
    await sendButton.click();

    // 5. Verify the run stream connects and updates
    // Chat stream should show the running state or streamed events
    await expect(page.locator('text=Agent working…').or(page.locator('aside').getByText(/running|succeeded|queued/i))).toBeVisible({
      timeout: 15000,
    });

    // 6. Verify RightPanel renders the Run Record
    const runRecordHeading = page.locator('aside').getByRole('heading', { name: 'Run Record' });
    await expect(runRecordHeading).toBeVisible();

    // Verify status indicator in the Run Record panel
    const statusBadge = page.locator('aside').locator('span').filter({ hasText: /running|succeeded|queued/i }).first();
    await expect(statusBadge).toBeVisible();
  });
});

