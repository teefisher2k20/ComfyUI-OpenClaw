import { expect, test } from '@playwright/test';
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from '../utils/helpers.js';

const pendingApproval = {
  approval_id: 'apr-001',
  template_id: 'render_portrait',
  status: 'pending',
  requested_at: '2026-03-05T10:00:00Z',
  source: 'telegram',
  inputs: { prompt: 'portrait', style: 'studio' },
};

function normalizeApiPath(pathname) {
  const stripped = pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, '');
}

function isApprovalsListPath(pathname) {
  const path = normalizeApiPath(pathname);
  return path === '/openclaw/approvals' || path === '/moltbot/approvals';
}

function isApprovalDetailPath(pathname) {
  const path = normalizeApiPath(pathname);
  return /^\/(openclaw|moltbot)\/approvals\/[^/]+$/.test(path);
}

function isApprovalActionPath(pathname, action) {
  const path = normalizeApiPath(pathname);
  return new RegExp(`^\\/(openclaw|moltbot)\\/approvals\\/[^/]+\\/${action}$`).test(path);
}

function approvalIdFromPath(pathname) {
  const parts = normalizeApiPath(pathname).split('/').filter(Boolean);
  if (parts.length < 3) return '';
  return decodeURIComponent(parts[2] || '');
}

async function mockApprovalApis(page, { listStatus = 200, listData = [pendingApproval], approveStatus = 200 } = {}) {
  let approvals = [...listData];

  const handler = async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (request.method() === 'GET' && isApprovalDetailPath(url.pathname)) {
      const id = approvalIdFromPath(url.pathname);
      const match = approvals.find((item) => item.approval_id === id);
      await route.fulfill({
        status: match ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(match ? { approval: match } : { error: 'not_found' }),
      });
      return;
    }

    if (request.method() === 'GET' && isApprovalsListPath(url.pathname)) {
      const statusFilter = String(url.searchParams.get('status') || '').trim().toLowerCase();
      const filtered = statusFilter
        ? approvals.filter((item) => String(item.status || '').toLowerCase() === statusFilter)
        : approvals;
      await route.fulfill({
        status: listStatus,
        contentType: 'application/json',
        body: JSON.stringify(
          listStatus === 200 ? { approvals: filtered } : { error: 'approval_list_failed' },
        ),
      });
      return;
    }

    if (request.method() === 'POST' && isApprovalActionPath(url.pathname, 'approve')) {
      const id = approvalIdFromPath(url.pathname);
      if (approveStatus === 200) {
        approvals = approvals.map((item) =>
          item.approval_id === id
            ? { ...item, status: 'approved' }
            : item,
        );
      }
      await route.fulfill({
        status: approveStatus,
        contentType: 'application/json',
        body: JSON.stringify(
          approveStatus === 200
            ? { executed: true, prompt_id: 'prompt-42' }
            : { error: 'approve_failed' },
        ),
      });
      return;
    }

    if (request.method() === 'POST' && isApprovalActionPath(url.pathname, 'reject')) {
      const id = approvalIdFromPath(url.pathname);
      approvals = approvals.map((item) =>
        item.approval_id === id
          ? { ...item, status: 'rejected' }
          : item,
      );
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
      return;
    }

    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not_found' }) });
  };

  const patterns = [
    '**/openclaw/approvals**',
    '**/moltbot/approvals**',
    '**/api/openclaw/approvals**',
    '**/api/moltbot/approvals**',
  ];

  for (const pattern of patterns) {
    await page.route(pattern, handler);
  }
}

test.describe('Approvals surfaces', () => {
  test.beforeEach(async ({ page }) => {
    await mockComfyUiCore(page);
    await page.addInitScript(() => {
      window.confirm = () => true;
      window.alert = () => {};
    });
  });

  test('loads pending approvals and allows approve action', async ({ page }) => {
    await mockApprovalApis(page);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'Approvals');

    await expect(page.locator('#apr-list .openclaw-list-item')).toHaveCount(1);
    await expect(page.locator('#apr-list')).toContainText('render_portrait');

    const approveButton = page.locator('#apr-list').getByRole('button', { name: 'Approve' }).first();
    await expect(approveButton).toBeVisible();

    const approveRequest = page.waitForRequest((req) => {
      const url = new URL(req.url());
      return req.method() === 'POST' && isApprovalActionPath(url.pathname, 'approve');
    });

    await approveButton.click();
    await approveRequest;

    await expect
      .poll(async () => {
        const text = (await page.locator('#apr-list').innerText()).trim();
        if (text.includes('Loading...')) return 'loading';
        if (text.includes('APPROVED') || text.includes('No requests found.')) return 'done';
        return 'pending';
      }, { timeout: 15000 })
      .toBe('done');
  });

  test('shows approval list fetch failures inside the sidebar', async ({ page }) => {
    await mockApprovalApis(page, { listStatus: 500, listData: [] });
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'Approvals');

    await expect(page.locator('.openclaw-error-box')).toContainText('approval_list_failed');
  });

  test('keeps admin console pending approvals aligned with sidebar data', async ({ page, baseURL }) => {
    await mockApprovalApis(page);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'Approvals');
    await expect(page.locator('#apr-list')).toContainText('apr-001');

    await page.goto(new URL('/web/admin_console.html', baseURL).toString());
    await page.locator('#refreshApprovals').click();

    await expect(page.locator('#approvalsList')).toContainText('apr-001');
    await expect(page.locator('#approvalsList')).toContainText('render_portrait');
  });
});
