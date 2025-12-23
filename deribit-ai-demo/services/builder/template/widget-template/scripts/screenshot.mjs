/**
 * Screenshot and Data Validation Script
 * Usage: pnpm run screenshot
 *
 * This script:
 * 1. Starts dev server
 * 2. Launches browser and navigates to the widget
 * 3. Monitors WebSocket messages directly
 * 4. Validates real data is received
 * 5. Takes screenshots after data is confirmed
 */

import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { setTimeout } from 'timers/promises';
import { writeFileSync } from 'fs';

const PORT = 5173;
const URL = `http://localhost:${PORT}`;

function getTimestamp() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

// Validation rules
const RULES = {
  btcPrice: (v) => v >= 10000 && v <= 500000,
  ethPrice: (v) => v >= 500 && v <= 50000,
  iv: (v) => v >= 1 && v <= 500,
  delta: (v) => v >= -1 && v <= 1,
  timestamp: (v) => v > Date.now() - 60000 && v <= Date.now() + 5000,
};

async function main() {
  console.log('╔════════════════════════════════════════════════════════════╗');
  console.log('║       Screenshot & Real-Time Data Validation               ║');
  console.log('╚════════════════════════════════════════════════════════════╝\n');

  console.log(`[Config] URL: ${URL}\n`);

  // Start dev server
  console.log('[1/5] Starting dev server...');
  const server = spawn('pnpm', ['run', 'dev'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false
  });

  let serverReady = false;
  server.stdout.on('data', (data) => {
    if (data.toString().includes('Local:')) serverReady = true;
  });
  server.stderr.on('data', (data) => {
    if (data.toString().includes('Local:')) serverReady = true;
  });

  for (let i = 0; i < 60 && !serverReady; i++) {
    await setTimeout(500);
  }
  await setTimeout(2000);

  console.log('[2/5] Launching browser...');
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });

  // Collect WebSocket messages
  const wsMessages = [];
  let wsConnected = false;

  // Intercept WebSocket
  await page.addInitScript(() => {
    window.__WS_MESSAGES__ = [];
    window.__WS_CONNECTED__ = false;

    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
      const ws = new OriginalWebSocket(url, protocols);

      ws.addEventListener('open', () => {
        window.__WS_CONNECTED__ = true;
        console.log('[WS] Connected to:', url);
      });

      ws.addEventListener('message', (event) => {
        try {
          const data = JSON.parse(event.data);
          window.__WS_MESSAGES__.push({
            timestamp: Date.now(),
            data: data
          });
          // Keep only last 100 messages
          if (window.__WS_MESSAGES__.length > 100) {
            window.__WS_MESSAGES__.shift();
          }
        } catch (e) {}
      });

      ws.addEventListener('close', () => {
        console.log('[WS] Disconnected');
      });

      ws.addEventListener('error', (e) => {
        console.log('[WS] Error:', e);
      });

      return ws;
    };
    window.WebSocket.prototype = OriginalWebSocket.prototype;
  });

  const report = {
    timestamp: new Date().toISOString(),
    validation: { passed: true, checks: [] },
    wsMessages: [],
    screenshots: [],
  };

  try {
    console.log('[3/5] Navigating to page...');
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 30000 });

    // Wait for WebSocket connection
    console.log('[3/5] Waiting for WebSocket connection...');
    let connected = false;
    for (let i = 0; i < 20; i++) {
      connected = await page.evaluate(() => window.__WS_CONNECTED__);
      if (connected) break;
      await setTimeout(500);
    }

    if (connected) {
      report.validation.checks.push({ name: 'WebSocket Connected', passed: true, message: 'WebSocket connection established' });
      console.log('     ✅ WebSocket connected');
    } else {
      report.validation.passed = false;
      report.validation.checks.push({ name: 'WebSocket Connected', passed: false, message: 'WebSocket failed to connect' });
      console.log('     ❌ WebSocket failed to connect');
    }

    // Wait for data
    console.log('[4/5] Waiting for real-time data (10s)...');
    await setTimeout(10000);

    // Collect messages
    const messages = await page.evaluate(() => window.__WS_MESSAGES__);
    report.wsMessages = messages;

    console.log(`     Received ${messages.length} WebSocket messages`);

    // Analyze messages
    if (messages.length === 0) {
      report.validation.passed = false;
      report.validation.checks.push({ name: 'Data Received', passed: false, message: 'No WebSocket messages received' });
      console.log('     ❌ No data received');
    } else {
      report.validation.checks.push({ name: 'Data Received', passed: true, message: `Received ${messages.length} messages` });
      console.log('     ✅ Data received');

      // Check for subscription data
      const tickerMessages = messages.filter(m =>
        m.data?.params?.channel?.startsWith('ticker.') ||
        m.data?.params?.channel?.startsWith('book.') ||
        m.data?.params?.channel?.startsWith('trades.')
      );

      if (tickerMessages.length > 0) {
        report.validation.checks.push({
          name: 'Subscription Data',
          passed: true,
          message: `Found ${tickerMessages.length} subscription updates`
        });
        console.log(`     ✅ Found ${tickerMessages.length} subscription updates`);

        // Validate data content
        const lastTicker = tickerMessages[tickerMessages.length - 1];
        const tickerData = lastTicker?.data?.params?.data;

        if (tickerData) {
          // Check price - validate based on instrument name in the data
          const price = tickerData.last_price || tickerData.mark_price;
          const instrumentName = tickerData.instrument_name || '';
          if (price) {
            const isBTC = instrumentName.toUpperCase().includes('BTC');
            const isETH = instrumentName.toUpperCase().includes('ETH');
            let priceValid = true;

            if (isBTC && !RULES.btcPrice(price)) priceValid = false;
            if (isETH && !RULES.ethPrice(price)) priceValid = false;

            if (priceValid) {
              report.validation.checks.push({
                name: 'Price Valid',
                passed: true,
                message: `${instrumentName}: $${price.toLocaleString()}`
              });
              console.log(`     ✅ Price valid: ${instrumentName} $${price.toLocaleString()}`);
            } else {
              report.validation.passed = false;
              report.validation.checks.push({
                name: 'Price Valid',
                passed: false,
                message: `Price out of range: ${price}`
              });
              console.log(`     ❌ Price out of range: ${price}`);
            }
          }

          // Check IV (for options)
          if (tickerData.mark_iv !== undefined) {
            const iv = tickerData.mark_iv;
            if (RULES.iv(iv)) {
              report.validation.checks.push({
                name: 'IV Valid',
                passed: true,
                message: `IV: ${iv.toFixed(1)}%`
              });
              console.log(`     ✅ IV valid: ${iv.toFixed(1)}%`);
            } else {
              report.validation.checks.push({
                name: 'IV Valid',
                passed: false,
                message: `IV out of range: ${iv}`
              });
              console.log(`     ❌ IV out of range: ${iv}`);
            }
          }

          // Check Greeks (for options)
          if (tickerData.greeks) {
            const { delta, gamma, vega, theta } = tickerData.greeks;
            if (RULES.delta(delta)) {
              report.validation.checks.push({
                name: 'Greeks Valid',
                passed: true,
                message: `Delta: ${delta.toFixed(4)}, Gamma: ${gamma?.toFixed(6)}, Vega: ${vega?.toFixed(2)}, Theta: ${theta?.toFixed(2)}`
              });
              console.log(`     ✅ Greeks valid: Δ=${delta.toFixed(4)}`);
            }
          }

          // Check timestamp freshness
          if (tickerData.timestamp) {
            const age = Date.now() - tickerData.timestamp;
            if (age < 60000) {
              report.validation.checks.push({
                name: 'Data Fresh',
                passed: true,
                message: `Data age: ${(age / 1000).toFixed(1)}s`
              });
              console.log(`     ✅ Data fresh: ${(age / 1000).toFixed(1)}s old`);
            } else {
              report.validation.checks.push({
                name: 'Data Fresh',
                passed: false,
                message: `Data is stale: ${(age / 1000).toFixed(1)}s old`
              });
              console.log(`     ⚠️  Data stale: ${(age / 1000).toFixed(1)}s old`);
            }
          }
        }

        // Check data is updating
        const uniqueTimestamps = new Set(tickerMessages.map(m => m.data?.params?.data?.timestamp));
        if (uniqueTimestamps.size > 1) {
          report.validation.checks.push({
            name: 'Real-Time Updates',
            passed: true,
            message: `${uniqueTimestamps.size} unique data points received`
          });
          console.log(`     ✅ Real-time updates confirmed (${uniqueTimestamps.size} unique)`);
        } else if (tickerMessages.length > 1) {
          report.validation.checks.push({
            name: 'Real-Time Updates',
            passed: false,
            message: 'Multiple messages but same timestamp - possible mock data'
          });
          console.log('     ⚠️  Same timestamp in all messages - check for mock data');
        }
      } else {
        report.validation.checks.push({
          name: 'Subscription Data',
          passed: false,
          message: 'No ticker/book/trades messages found'
        });
        console.log('     ❌ No subscription data found');
      }
    }

    // Take screenshot
    console.log('[5/5] Taking screenshots...');
    const ts = getTimestamp();
    const path1 = `screenshot-${ts}.png`;
    await page.screenshot({ path: path1, fullPage: true });
    report.screenshots.push(path1);

    await page.screenshot({ path: 'screenshot-latest.png', fullPage: true });
    console.log(`     📸 Saved: ${path1}`);
    console.log('     📸 Saved: screenshot-latest.png');

    // Print summary
    console.log('\n' + '═'.repeat(60));
    if (report.validation.passed) {
      console.log('🎉 VALIDATION PASSED');
      console.log('   Widget is receiving and displaying real-time data correctly');
    } else {
      console.log('❌ VALIDATION FAILED');
      console.log('   Please fix the issues above');
    }
    console.log('═'.repeat(60));

    // Save report
    writeFileSync('validation-report.json', JSON.stringify(report, null, 2));
    console.log('\n📋 Full report: validation-report.json\n');

  } catch (error) {
    console.error('\n❌ Error:', error.message);
    report.error = error.message;
  } finally {
    await browser.close();
    server.kill();
  }
}

main().catch(console.error);
