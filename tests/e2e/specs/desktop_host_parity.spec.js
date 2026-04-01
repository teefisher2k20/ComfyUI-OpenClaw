import { expect, test } from '@playwright/test';
import {
  clickTab,
  mockCompatApprovalsList,
  mockComfyUiCore,
  mockRemoteAdminBaseline,
  waitForOpenClawReady,
} from '../utils/helpers.js';

const pendingApproval = {
  approval_id: 'apr-r166-001',
  template_id: 'desktop_host_smoke',
  status: 'pending',
  requested_at: '2026-04-01T12:00:00Z',
  source: 'desktop-host-harness',
  inputs: { prompt: 'desktop host parity' },
};

test.describe('Desktop host parity lane', () => {
  test('keeps standalone sidebar evidence separate from desktop host evidence', async ({ page }) => {
    await mockComfyUiCore(page, { hostSurface: 'standalone_frontend' });
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);

    const host = page.locator('#sidebar-tab-comfyui-openclaw');
    await expect(host).toHaveAttribute('data-openclaw-host-surface', 'standalone_frontend');
    await expect(host).toHaveAttribute('data-openclaw-desktop-host', 'false');
  });

  test('boots the sidebar under desktop host signals and keeps approvals interactive', async ({ page }) => {
    await mockComfyUiCore(page, { hostSurface: 'desktop' });
    await mockCompatApprovalsList(page, [pendingApproval]);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);

    const host = page.locator('#sidebar-tab-comfyui-openclaw');
    await expect(host).toHaveAttribute('data-openclaw-host-surface', 'desktop');
    await expect(host).toHaveAttribute('data-openclaw-desktop-host', 'true');

    await clickTab(page, 'Approvals');
    await expect(page.locator('#apr-list')).toContainText('apr-r166-001');
    await expect(page.locator('#apr-list')).toContainText('desktop_host_smoke');
  });

  test('stamps desktop host metadata on the admin console and refreshes approvals', async ({ page, baseURL }) => {
    await mockRemoteAdminBaseline(page, {
      hostSurface: 'desktop',
      approvals: [pendingApproval],
    });
    await page.goto(new URL('/web/admin_console.html', baseURL).toString());

    await expect(page.locator('body')).toHaveAttribute('data-openclaw-host-surface', 'desktop');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-host', 'true');

    await page.locator('#refreshApprovals').click();
    await expect(page.locator('#approvalsList')).toContainText('apr-r166-001');
    await expect(page.locator('#approvalsList')).toContainText('desktop_host_smoke');
  });
});
