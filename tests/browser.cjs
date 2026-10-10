const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

function wavAudio(durationMs, tone = false) {
  const count = durationMs * 16;
  const wav = Buffer.alloc(44 + count * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36);
  wav.writeUInt32LE(count * 2, 40);
  if (tone) for (let i = 0; i < count; i++) wav.writeInt16LE(Math.round(6000 * Math.sin(i * Math.PI / 20)), 44 + i * 2);
  return wav;
}

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
    fs.mkdirSync(path.join(root, 'logs'), { recursive: true });
    const fakeMicrophone = path.join(root, 'logs/fake-microphone.wav');
    fs.writeFileSync(fakeMicrophone, wavAudio(2000, true));
    browser = await chromium.launch({ headless: true, channel: 'msedge',
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${fakeMicrophone}`] });
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      const pcmPackets = [];
      const playbackReports = [];
      let activeSocket;
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/config', route => route.fulfill({json: {live_enabled: false, session_seconds: 120}}));
      await page.goto(baseUrl);
      await page.waitForFunction(() => document.getElementById('statusText').textContent === 'live demo offline');
      assert(await page.locator('#startBtn').isDisabled());
      await page.route('**/config', route => route.fulfill({ json: { live_enabled: true, session_seconds: 120 } }));
      // Reproduce the public tunnel's failed secondary asset requests.
      await page.route('**/static/**', route => route.abort());
      await page.routeWebSocket('**/ws/**', socket => {
        activeSocket = socket;
        socket.onMessage(raw => {
          if (typeof raw !== 'string') { pcmPackets.push(Buffer.from(raw)); return; }
          const msg = JSON.parse(raw);
          if (msg.type === 'join') {
            socket.send(JSON.stringify({ type: 'joined', party_id: 'A', language: 'ta-IN', resume_token: 'fake', resumed: false,
              role: msg.role, muted: msg.role === 'listener' }));
            socket.send(JSON.stringify({ type: 'ready' }));
            socket.send(JSON.stringify({ type: 'peer_joined', party_id: 'B', language: 'en-IN' }));
            socket.send(JSON.stringify({ type: 'transcript', party: 'B', kind: 'partial', text: '<img src=x onerror="window.injected=true">' }));
            socket.send(JSON.stringify({ type: 'translated', speaker: 'B', text: 'Translated text' }));
          } else if (msg.type === 'ping') {
            socket.send(JSON.stringify({ type: 'pong', client_ms: msg.client_ms, server_ms: Date.now() }));
            const wav = wavAudio(100);
            socket.send(JSON.stringify({ type: 'audio', speaker: 'B', seq: 1, format: 'wav',
              capture_start_ms: Date.now() - 500, data: wav.toString('base64') }));
          } else if (msg.type === 'playback') {
            playbackReports.push(msg);
          } else if (msg.type === 'microphone') {
            socket.send(JSON.stringify({type: 'microphone', party_id: 'A', role: 'speaker', muted: msg.muted, accepted: true}));
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
      assert(await page.locator('#micBtn').isDisabled());
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
      const nonzero = packet => packet.subarray(8).some(byte => byte !== 0);
      async function waitForAudio(expectedNonzero) {
        const start = pcmPackets.length;
        for (let i = 0; i < 60; i++) {
          if (pcmPackets.slice(start).some(packet => nonzero(packet) === expectedNonzero)) return;
          await page.waitForTimeout(100);
        }
        throw new Error(`Expected ${expectedNonzero ? 'live' : 'silent'} microphone packets`);
      }
      await waitForAudio(true);
      assert.equal(await page.locator('#micIcon svg').count(), 2);
      assert(await page.locator('#micIcon .lucide-mic').isVisible());
      await page.locator('#micBtn').click();
      await page.getByRole('button', {name: 'Unmute microphone', exact: true}).waitFor();
      await waitForAudio(false);
      await page.locator('#micBtn').click();
      await page.getByRole('button', {name: 'Mute microphone', exact: true}).waitFor();
      await waitForAudio(true);
      const sendAudio = (speaker, seq) => activeSocket.send(JSON.stringify({type: 'audio', speaker, seq,
        capture_start_ms: Date.now() - 500, data: wavAudio(1500).toString('base64')}));
      await page.locator('#echoGuard').uncheck();
      sendAudio('B', 2);
      await waitForAudio(true);
      await page.locator('#echoGuard').check();
      assert.equal(await page.evaluate(() => mediaStream.getAudioTracks()[0].enabled), false);
      assert.equal(await page.locator('#micBtn').getAttribute('data-paused'), 'true');
      assert(await page.locator('#micIcon .lucide-mic-off').isVisible());
      await waitForAudio(false);
      await page.screenshot({path: path.join(root, `logs/playback-muted-${viewport.width}.png`), fullPage: true});
      await page.locator('#echoGuard').uncheck();
      assert.equal(await page.evaluate(() => mediaStream.getAudioTracks()[0].enabled), true);
      assert.equal(await page.locator('#micBtn').getAttribute('data-paused'), 'false');
      await waitForAudio(true);
      sendAudio('C', 3);
      for (let i = 0; i < 60 && !playbackReports.some(m => m.speaker === 'C' && m.seq === 3); i++) await page.waitForTimeout(100);
      assert(playbackReports.some(m => m.speaker === 'B' && m.seq === 2));
      assert(playbackReports.some(m => m.speaker === 'C' && m.seq === 3));
      assert.equal(await page.locator('#micStatus').textContent(), 'Microphone live');
      await page.locator('#echoGuard').check();
      sendAudio('B', 4);
      await page.waitForFunction(() => document.getElementById('micBtn').dataset.paused === 'true');
      await page.waitForFunction(() => !playbackActive);
      assert.equal(await page.evaluate(() => mediaStream.getAudioTracks()[0].enabled), true);
      assert.equal(await page.locator('#micBtn').getAttribute('data-paused'), 'false');
      sendAudio('B', 5);
      await page.waitForFunction(() => document.getElementById('micBtn').dataset.paused === 'true');
      await page.locator('#micBtn').click();
      await page.waitForFunction(() => micMuted && !micPending);
      await page.waitForFunction(() => !playbackActive);
      assert.equal(await page.evaluate(() => mediaStream.getAudioTracks()[0].enabled), false);
      assert.equal(await page.locator('#micStatus').textContent(), 'Microphone muted');
      await page.locator('#micBtn').click();
      await page.waitForFunction(() => !micMuted && !micPending);
      assert.equal(await page.evaluate(() => mediaStream.getAudioTracks()[0].enabled), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({path: path.join(root, `logs/microphone-${viewport.width}.png`), fullPage: true});
      await page.locator('#stopBtn').click();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Desktop/mobile layout, manual mute/unmute, live PCM during playback, echo-guard recovery, queued two-speaker audio and playback timing passed.');
  } finally {
    if (browser) await browser.close();
    if (service) {
      service.kill();
      if (service.exitCode === null) await new Promise(resolve => service.once('exit', resolve));
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
