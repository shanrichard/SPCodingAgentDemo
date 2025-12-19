/**
 * Screenshot script for visual validation
 * Usage: pnpm run screenshot [instrument]
 *
 * This starts dev server, waits for page to load, takes a screenshot,
 * and saves it with timestamp for easy identification.
 */

import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { setTimeout } from 'timers/promises';

const instrument = process.argv[2] || '';
const PORT = 5173;
const URL = `http://localhost:${PORT}?instrument=${instrument}`;

// Generate timestamp for unique screenshot names
function getTimestamp() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

async function main() {
  console.log('[Screenshot] Starting dev server...');

  // Start dev server in background
  const server = spawn('pnpm', ['run', 'dev'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false
  });

  let serverReady = false;

  server.stdout.on('data', (data) => {
    const output = data.toString();
    if (output.includes('Local:') || output.includes('localhost')) {
      serverReady = true;
    }
  });

  server.stderr.on('data', (data) => {
    // Vite outputs to stderr sometimes
    const output = data.toString();
    if (output.includes('Local:') || output.includes('localhost')) {
      serverReady = true;
    }
  });

  // Wait for server to be ready (max 30s)
  console.log('[Screenshot] Waiting for dev server...');
  for (let i = 0; i < 60; i++) {
    if (serverReady) break;
    await setTimeout(500);
  }

  if (!serverReady) {
    // Try anyway after timeout
    console.log('[Screenshot] Server may not be ready, trying anyway...');
  }

  // Give server a moment to fully start
  await setTimeout(2000);

  console.log('[Screenshot] Launching browser...');
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const page = await browser.newPage({
    viewport: { width: 800, height: 600 }
  });

  try {
    console.log(`[Screenshot] Navigating to ${URL}`);
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 30000 });

    // Wait for content to render (WebSocket data might take a moment)
    console.log('[Screenshot] Waiting for content to load...');
    await setTimeout(3000);

    // Take screenshot with timestamp
    const timestamp = getTimestamp();
    const screenshotPath = `screenshot-${timestamp}.png`;
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`[Screenshot] Saved to ${screenshotPath}`);

    // Also save as latest.png for easy reference
    await page.screenshot({ path: 'screenshot-latest.png', fullPage: true });
    console.log('[Screenshot] Also saved as screenshot-latest.png');

  } catch (error) {
    console.error('[Screenshot] Error:', error.message);
  } finally {
    await browser.close();
    server.kill();
    console.log('[Screenshot] Done');
  }
}

main().catch(console.error);
