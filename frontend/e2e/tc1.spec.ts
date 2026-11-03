import { test, expect } from '@playwright/test';

test.describe('TC1 Session Lifecycle, Run Stream, and UI Navigation E2E', () => {
  test('creates session, streams run events, and walks through approvals, run records, and metrics pages', async ({
    page,
  }) => {
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

    // 4. Send a message to start a short no-build inspection run
    const chatInput = page.getByPlaceholder('Ask the agent to build, fix, or upgrade…');
    await expect(chatInput).toBeEnabled();

    // Use a neutral prompt (no issue #5776 or defect specifics to avoid answer leaks or memories pollution)
    const promptText =
      'List the files in the repository root and describe the overall project structure without running a build.';
    await chatInput.fill(promptText);

    const sendButton = page.getByRole('button', { name: 'Send' });
    await expect(sendButton).toBeEnabled();
    await sendButton.click();

    // 5. Verify the run stream connects and updates
    // Chat stream should show the running state or streamed events
    await expect(
      page.locator('text=Agent working…').or(page.locator('aside').getByText(/running|succeeded|queued/i)),
    ).toBeVisible({
      timeout: 15000,
    });

    // 6. Verify RightPanel renders the Run Record
    const runRecordHeading = page.locator('aside').getByRole('heading', { name: 'Run Record' });
    await expect(runRecordHeading).toBeVisible();

    // Verify status indicator in the Run Record panel
    const statusBadge = page
      .locator('aside')
      .locator('span')
      .filter({ hasText: /running|succeeded|queued/i })
      .first();
    await expect(statusBadge).toBeVisible();

    // 7. ANVIL-26 Acceptance: Walk through Approvals inbox, Run records, and Metrics pages
    // 7a. Navigate to Approvals inbox
    const approvalsNavButton = page.getByRole('button', { name: /Approvals/i });
    await expect(approvalsNavButton).toBeVisible();
    await approvalsNavButton.click();

    await expect(page.getByRole('heading', { name: 'Approvals inbox' })).toBeVisible();
    await expect(page.getByText('Pending actions across all sessions.')).toBeVisible();

    // 7b. Navigate to Run records page
    const runRecordsNavButton = page.getByRole('button', { name: /Run records/i });
    await expect(runRecordsNavButton).toBeVisible();
    await runRecordsNavButton.click();

    await expect(page.getByRole('heading', { name: 'Run records' })).toBeVisible();
    // Verify run list has at least one run and reproducibility card is rendered
    await expect(page.getByRole('heading', { name: 'Reproducibility' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('heading', { name: 'Event timeline' })).toBeVisible();

    // 7c. Navigate to Metrics page
    const metricsNavButton = page.getByRole('button', { name: /Metrics/i });
    await expect(metricsNavButton).toBeVisible();
    await metricsNavButton.click();

    await expect(page.getByRole('heading', { name: 'Metrics' })).toBeVisible();
    await expect(page.getByText('Total run time')).toBeVisible();
    await expect(page.getByText('USD')).toBeVisible();
    await expect(page.getByText('Model calls')).toBeVisible();
    await expect(page.getByText('Human touches')).toBeVisible();
    await expect(page.getByText('CI rounds')).toBeVisible();

    // 7d. Navigate back to Chat view
    const chatNavButton = page.getByRole('button', { name: 'Chat' });
    await expect(chatNavButton).toBeVisible();
    await chatNavButton.click();

    // Verify chat view is restored with active session
    await expect(page.getByText(sessionTitle)).toBeVisible();
  });
});
