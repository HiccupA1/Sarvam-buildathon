const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
  const root = path.resolve(__dirname, '..');
  const source = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
  const inline = source.match(/<script>([\s\S]*?)<\/script>/)[1];
  new Function(inline);
  const executable = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  const baseUrl = process.env.PREVIEW_URL || 'http://127.0.0.1:8770';
  const service = process.env.PREVIEW_URL ? null : spawn(executable, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8770'],
    { cwd: root, env: { ...process.env, LIVE_ENABLED: 'false' }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    for (let i = 0; i < 40; i++) {
      if (service && service.exitCode !== null) throw new Error('Preview server exited');
      try { if ((await fetch(`${baseUrl}/health`)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ headless: true, channel: 'msedge',
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
    fs.mkdirSync(path.join(root, 'logs'), { recursive: true });
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      const pcmPackets = [];
      const playbackReports = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(baseUrl);
      await page.waitForFunction(() => document.getElementById('statusText').textContent === 'live demo offline');
      assert(await page.locator('#startBtn').isDisabled());
      await page.route('**/config', route => route.fulfill({ json: { live_enabled: true, session_seconds: 120 } }));
      await page.routeWebSocket('**/ws/**', socket => {
        socket.onMessage(raw => {
          if (typeof raw !== 'string') { pcmPackets.push(Buffer.from(raw)); return; }
          const msg = JSON.parse(raw);
          if (msg.type === 'join') {
            socket.send(JSON.stringify({ type: 'joined', party_id: 'A', language: 'ta-IN', resume_token: 'fake', resumed: false }));
            socket.send(JSON.stringify({ type: 'ready' }));
            socket.send(JSON.stringify({ type: 'peer_joined', party_id: 'B', language: 'en-IN' }));
            socket.send(JSON.stringify({ type: 'transcript', party: 'B', kind: 'partial', text: '<img src=x onerror="window.injected=true">' }));
            socket.send(JSON.stringify({ type: 'translated', speaker: 'B', text: 'Translated text' }));
          } else if (msg.type === 'ping') {
            socket.send(JSON.stringify({ type: 'pong', client_ms: msg.client_ms, server_ms: Date.now() }));
            const wav = Buffer.alloc(44 + 3200);
            wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
            wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
            wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
            wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36);
            wav.writeUInt32LE(3200, 40);
            socket.send(JSON.stringify({ type: 'audio', speaker: 'B', seq: 1, format: 'wav',
              capture_start_ms: Date.now() - 500, data: wav.toString('base64') }));
          } else if (msg.type === 'playback') {
            playbackReports.push(msg);
          } else if (msg.type === 'end') socket.close({ code: 1000 });
        });
      });
      await page.reload();
      await page.locator('#startBtn:not([disabled])').waitFor();
      await page.locator('#roomCode').fill('browser-test');
      await page.locator('#accessToken').fill('fake-test-invite');
      await page.locator('#listenOnly').check();
      await page.locator('#startBtn').click();
      await page.locator('#incomingText').filter({ hasText: 'Translated text' }).waitFor();
      assert.equal(await page.evaluate(() => window.injected), undefined);
      assert(await page.locator('#myLang').isDisabled());
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(root, `logs/browser-${viewport.width}.png`), fullPage: true });
      await page.locator('#stopBtn').click();
      assert.equal(await page.locator('#myLang').isDisabled(), false);
      assert.deepEqual(errors, []);
      assert(playbackReports.length > 0, 'playback-start report missing');
      assert(playbackReports[0].capture_to_playback_ms >= 400);
      await page.context().grantPermissions(['microphone'], { origin: new URL(baseUrl).origin });
      await page.locator('#listenOnly').uncheck();
      await page.locator('#startBtn').click();
      for (let i = 0; i < 100 && pcmPackets.length === 0; i++) {
        await page.waitForTimeout(100);
      }
      assert(pcmPackets.length > 0, 'mic worklet emitted no PCM');
      assert.equal(pcmPackets[0].length, 3208);
      assert(Math.abs(pcmPackets[0].readDoubleLE(0) - Date.now()) < 3000);
      await page.locator('#stopBtn').click();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Desktop/mobile layout, join/stop, transcript escaping, PCM capture and playback timing passed.');
  } finally {
    if (browser) await browser.close();
    if (service) {
      service.kill();
      if (service.exitCode === null) await new Promise(resolve => service.once('exit', resolve));
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
