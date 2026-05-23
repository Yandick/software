#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, '..');
const projectRoot = path.resolve(frontendRoot, '..');
const logsDir = path.join(projectRoot, 'logs');

const DEFAULT_FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5666';
const DEFAULT_BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8010';

const accounts = {
  admin: { password: 'admin123', path: '/ops/demo', username: 'admin' },
  auditor: { password: 'audit123', path: '/ops/audit', username: 'auditor' },
  ops: { password: 'ops123', path: '/ops/issues', username: 'ops' },
  user: { password: 'user123', path: '/portal', username: 'user' },
};

const windowLayout = {
  admin: { height: 540, width: 960, x: 0, y: 540 },
  auditor: { height: 540, width: 960, x: 960, y: 540 },
  ops: { height: 540, width: 960, x: 960, y: 0 },
  user: { height: 540, width: 960, x: 0, y: 0 },
};

function parseArgs(argv) {
  const options = {
    backendUrl: DEFAULT_BACKEND_URL,
    close: false,
    frontendUrl: DEFAULT_FRONTEND_URL,
    headless: false,
    keepOpen: false,
    mode: 'multi',
    record: false,
    screenshot: true,
    slowMo: 80,
    stepDelay: 1800,
    timeout: 180_000,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = () => argv[++index];
    if (arg === '--') continue;
    else if (arg === '--backend-url') options.backendUrl = next();
    else if (arg === '--close') options.close = true;
    else if (arg === '--frontend-url') options.frontendUrl = next();
    else if (arg === '--headless') options.headless = true;
    else if (arg === '--keep-open') options.keepOpen = true;
    else if (arg === '--single-window') options.mode = 'single';
    else if (arg === '--multi-window') options.mode = 'multi';
    else if (arg === '--no-screenshot') options.screenshot = false;
    else if (arg === '--record') options.record = true;
    else if (arg === '--slow-mo') options.slowMo = Number(next() || 0);
    else if (arg === '--step-delay') options.stepDelay = Number(next() || 0);
    else if (arg === '--timeout') options.timeout = Number(next() || options.timeout);
    else if (arg === '-h' || arg === '--help') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }

  if (options.close) options.keepOpen = false;
  if (options.headless) options.keepOpen = false;
  return options;
}

function printHelp() {
  console.log(`Usage: pnpm demo:playwright [options]

Options:
  --multi-window            Open admin, user, ops and auditor windows. Default.
  --single-window           Open only /ops/demo and drive it step by step.
  --frontend-url URL        Frontend URL. Default: ${DEFAULT_FRONTEND_URL}
  --backend-url URL         Backend URL. Default: ${DEFAULT_BACKEND_URL}
  --headless                Run without visible browser windows.
  --keep-open               Keep browser windows open after the demo finishes.
  --record                  Record per-window videos under logs/playwright-videos.
  --no-screenshot           Do not write final screenshots under logs/.
  --step-delay MS           Delay between demo steps. Default: 1800.
  --slow-mo MS              Playwright action delay. Default: 80.
  --timeout MS              Overall wait timeout. Default: 180000.
`);
}

function urlFor(baseUrl, route) {
  return new URL(route, `${baseUrl.replace(/\/$/, '')}/`).toString();
}

async function waitForHttp(url, label, timeoutMs = 30_000) {
  const started = Date.now();
  let lastError = '';
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error.message;
    }
    await sleep(1000);
  }
  throw new Error(`${label} is not ready at ${url}: ${lastError}`);
}

async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch (error) {
    throw new Error(
      [
        'Playwright is not installed for the frontend workspace.',
        'Run: cd frontend && npm exec --yes pnpm@10.33.0 -- install',
        'Then, if Chromium is missing, run: cd frontend && npm exec --yes pnpm@10.33.0 -- exec playwright install chromium',
        `Original error: ${error.message}`,
      ].join('\n'),
    );
  }
}

async function launchWindow(chromium, role, options) {
  const layout = windowLayout[role] || windowLayout.admin;
  const browser = await chromium.launch({
    args: options.headless
      ? []
      : [
          `--window-position=${layout.x},${layout.y}`,
          `--window-size=${layout.width},${layout.height}`,
        ],
    headless: options.headless,
    slowMo: options.slowMo,
  });
  const context = await browser.newContext({
    recordVideo: options.record
      ? { dir: path.join(logsDir, 'playwright-videos'), size: { height: layout.height, width: layout.width } }
      : undefined,
    viewport: { height: layout.height, width: layout.width },
  });
  const page = await context.newPage();
  return { browser, context, page, role };
}

async function login(page, role, options) {
  const account = accounts[role];
  const targetUrl = urlFor(options.frontendUrl, account.path);
  const loginUrl = urlFor(options.frontendUrl, `/auth/login?redirect=${encodeURIComponent(account.path)}`);
  await page.goto(loginUrl, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder(/请输入用户名|Please enter username/i).fill(account.username);
  await page.getByPlaceholder(/请输入密码|密码|Please enter password|Password/i).fill(account.password);

  const submit = page.getByRole('button', { name: /登录运维数字员工系统|Login/i });
  if (await submit.count()) {
    await submit.first().click();
  } else {
    await page.locator('button[type="submit"]').first().click();
  }

  await page.waitForLoadState('domcontentloaded').catch(() => {});
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
}

async function readDemoState(page) {
  const root = page.getByTestId('demo-page');
  return {
    status: (await root.getAttribute('data-status')) || 'initializing',
    stepIndex: Number((await root.getAttribute('data-step-index')) || 0),
    stepTotal: Number((await root.getAttribute('data-step-total')) || 0),
  };
}

async function waitForDemoReady(page, timeout) {
  await page.getByTestId('demo-page').waitFor({ state: 'visible', timeout });
  await page.waitForFunction(
    () => {
      const root = document.querySelector('[data-testid="demo-page"]');
      return root && root.getAttribute('data-step-total') !== '0';
    },
    undefined,
    { timeout },
  );
}

async function runStep(page, timeout) {
  const before = await readDemoState(page);
  if (before.status === 'finished') return before;
  await page.getByTestId('demo-run-step').click();
  await page.waitForFunction(
    (previous) => {
      const root = document.querySelector('[data-testid="demo-page"]');
      if (!root) return false;
      const status = root.getAttribute('data-status');
      const nextIndex = Number(root.getAttribute('data-step-index') || 0);
      return status === 'finished' || nextIndex > previous;
    },
    before.stepIndex,
    { timeout },
  );
  return readDemoState(page);
}

async function refreshRolePages(windows) {
  await Promise.allSettled(
    windows
      .filter((item) => item.role !== 'admin')
      .map(async (item) => {
        await item.page.reload({ waitUntil: 'domcontentloaded', timeout: 30_000 });
      }),
  );
}

async function takeScreenshots(windows) {
  await fs.mkdir(logsDir, { recursive: true });
  const stamp = new Date().toISOString().replaceAll(':', '').replaceAll('.', '-');
  for (const item of windows) {
    await item.page.screenshot({
      fullPage: false,
      path: path.join(logsDir, `demo-playwright-${item.role}-${stamp}.png`),
    });
  }
}

async function closeWindows(windows) {
  await Promise.allSettled(windows.map((item) => item.context.close()));
  await Promise.allSettled(windows.map((item) => item.browser.close()));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  await waitForHttp(urlFor(options.backendUrl, '/api/health'), 'backend');
  await waitForHttp(options.frontendUrl, 'frontend');

  const { chromium } = await loadPlaywright();
  const roles = options.mode === 'multi' ? ['user', 'ops', 'admin', 'auditor'] : ['admin'];
  const windows = [];

  try {
    for (const role of roles) {
      const item = await launchWindow(chromium, role, options);
      windows.push(item);
      await login(item.page, role, options);
    }

    const adminWindow = windows.find((item) => item.role === 'admin');
    await waitForDemoReady(adminWindow.page, options.timeout);

    let state = await readDemoState(adminWindow.page);
    console.log(`Demo ready: ${state.stepIndex}/${state.stepTotal}, status=${state.status}`);
    while (state.status !== 'finished') {
      state = await runStep(adminWindow.page, options.timeout);
      console.log(`Demo step: ${state.stepIndex}/${state.stepTotal}, status=${state.status}`);
      if (options.mode === 'multi') await refreshRolePages(windows);
      await sleep(options.stepDelay);
    }

    if (options.screenshot) await takeScreenshots(windows);
    console.log('Demo finished.');

    if (options.keepOpen) {
      console.log('Browser windows are kept open. Press Ctrl+C to stop this script.');
      await new Promise(() => {});
    }
  } catch (error) {
    if (String(error.message || error).includes('Executable doesn')) {
      console.error('Chromium is not installed for Playwright.');
      console.error('Run: cd frontend && npm exec --yes pnpm@10.33.0 -- exec playwright install chromium');
    }
    throw error;
  } finally {
    if (!options.keepOpen) await closeWindows(windows);
  }
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
