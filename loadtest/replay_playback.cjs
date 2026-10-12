// Cached real-provider audio replay; no live API calls or invented end-to-end timestamps.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, 'loadtest/results/controlled_20261012');
  const manifest = JSON.parse(fs.readFileSync(path.join(output, 'manifest.json'), 'utf8'));
  const selected = manifest.trials.filter(trial => trial.repetition === 1 && trial.pair <= 3);
  for (const trial of selected) assert(fs.existsSync(path.join(output, 'trials', trial.id + '.json')), 'Wait for all replay subset trials to complete');
  const service = spawn('.venv/Scripts/python.exe', ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8783'],
    {cwd: root, env: {...process.env, LIVE_ENABLED: 'false'}, stdio: 'ignore', windowsHide: true});
  let browser;
  const rows = [];
  const cases = [];
  try {
    for (let i = 0; i < 40; i++) {
      if (service.exitCode !== null) throw new Error('Offline replay backend exited');
      try { if ((await fetch('http://127.0.0.1:8783/health')).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({headless: true, channel: 'msedge'});
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      for (const trial of selected) {
        const record = JSON.parse(fs.readFileSync(path.join(output, 'trials', trial.id + '.json'), 'utf8'));
        const raw = fs.readFileSync(path.join(output, record.events_file), 'utf8').trim().split('\n').map(JSON.parse);
        const anchor = Math.min(...Object.values(record.send_start_ms));
        const deliveries = raw.filter(event => event.receiver === 0 && ['transcript', 'translated', 'latency', 'audio'].includes(event.type)
          && (event.type !== 'transcript' || event.kind === 'final'));
        const audios = deliveries.filter(event => event.type === 'audio');
        assert(audios.length > 0);
        const page = await browser.newPage({viewport});
        const errors = [];
        const timers = [];
        const reports = [];
        const deliveredAt = new Map();
        const pageErrors = [];
        let replayStarted;
        page.on('pageerror', error => pageErrors.push(error.message));
        await page.addInitScript(() => {
          window.replayMedia = {playing: 0, ended: 0, errors: 0};
          document.addEventListener('DOMContentLoaded', () => {
            const player = document.getElementById('player');
            player.addEventListener('playing', () => window.replayMedia.playing++);
            player.addEventListener('ended', () => window.replayMedia.ended++);
            player.addEventListener('error', () => window.replayMedia.errors++);
          });
        });
        await page.route('**/config', route => route.fulfill({json: {live_enabled: true, session_seconds: 60}}));
        await page.routeWebSocket('**/ws/**', socket => {
          socket.onMessage(rawMessage => {
            if (typeof rawMessage !== 'string') return;
            const msg = JSON.parse(rawMessage);
            if (msg.type === 'join') {
              socket.send(JSON.stringify({type: 'joined', party_id: 'A', language: trial.targets[0], role: 'listener', muted: true, resume_token: 'offline-replay'}));
              socket.send(JSON.stringify({type: 'ready'}));
              for (const party of ['D', 'E']) socket.send(JSON.stringify({type: 'peer_joined', party_id: party, language: trial.targets[0], role: 'speaker', muted: false}));
              replayStarted = performance.now();
              for (const event of deliveries) {
                timers.push(setTimeout(() => {
                  const payload = {...event};
                  delete payload.receiver;
                  delete payload.at_ms;
                  if (event.type === 'audio') {
                    const wavPath = path.join(root, 'logs/controlled-benchmark/audio', trial.id, `l0-${event.speaker}-${event.seq}.wav`);
                    payload.data = fs.readFileSync(wavPath).toString('base64');
                    payload.capture_start_ms = null;
                    deliveredAt.set(`${event.speaker}:${event.seq}`, performance.now() - replayStarted);
                  }
                  socket.send(JSON.stringify(payload));
                }, Math.max(0, event.at_ms - anchor)));
              }
            } else if (msg.type === 'ping') {
              socket.send(JSON.stringify({type: 'pong', client_ms: msg.client_ms, server_ms: Date.now()}));
            } else if (msg.type === 'playback') {
              reports.push(msg);
            } else if (msg.type === 'end') socket.close({code: 1000});
          });
        });
        try {
          await page.goto('http://127.0.0.1:8783/');
          await page.locator('#startBtn:not([disabled])').waitFor();
          await page.locator('#roomCode').fill('cached-playback-replay');
          await page.locator('#accessToken').fill('offline-replay-not-a-live-invite');
          await page.locator('#myLang').selectOption(trial.targets[0]);
          await page.locator('#listenOnly').check();
          await page.locator('#startBtn').click();
          const deadline = performance.now() + 45000;
          while (performance.now() < deadline) {
            const media = await page.evaluate(() => window.replayMedia);
            if (reports.length >= audios.length && media.ended >= audios.length) break;
            await page.waitForTimeout(100);
          }
          const media = await page.evaluate(() => window.replayMedia);
          await page.screenshot({path: path.join(root, `logs/controlled-benchmark/playback-${viewport.width}.png`), fullPage: true});
          if (reports.length !== audios.length) errors.push(`Expected ${audios.length} playing reports, received ${reports.length}`);
          if (media.ended !== audios.length || media.errors) errors.push(`Media endings/errors: ${media.ended}/${media.errors}`);
          if (reports.map(msg => `${msg.speaker}:${msg.seq}`).join(',') !== audios.map(msg => `${msg.speaker}:${msg.seq}`).join(',')) errors.push('FIFO playback order mismatch');
          const status = await page.locator('#statusText').textContent();
          if (/blocked|failed|queue is full/.test(status)) errors.push(status);
          for (const event of audios) {
            const matched = reports.filter(msg => msg.speaker === event.speaker && msg.seq === event.seq);
            rows.push({trial_id: trial.id, condition: trial.condition, pair: trial.pair, viewport_width: viewport.width,
              target_language: trial.targets[0], speaker: event.speaker, seq: event.seq,
              measured_audio_arrival_offset_ms: event.at_ms - anchor,
              replay_delivery_offset_ms: deliveredAt.get(`${event.speaker}:${event.seq}`),
              receive_to_playback_ms: matched[0]?.receive_to_playback_ms ?? null,
              playback_reports: matched.length, wav_duration_s: event.wav_duration_s,
              errors: [...errors, ...pageErrors]});
          }
          cases.push({trial_id: trial.id, viewport, expected_clips: audios.length, playing_reports: reports.length,
            media_endings: media.ended, media_errors: media.errors,
            layout_overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
            errors: [...errors, ...pageErrors], interpretation: 'cached WAV replay at measured receipt offsets; not live end-to-end latency'});
          await page.locator('#stopBtn').click();
          console.log(`${viewport.width}px ${trial.id}: ${reports.length}/${audios.length} clips started; errors=${errors.length + pageErrors.length}`);
        } finally {
          timers.forEach(clearTimeout);
          await page.close();
        }
      }
    }
    fs.writeFileSync(path.join(output, 'playback_replay.json'), JSON.stringify({browser: browser.version(), cases, rows}, null, 2));
    assert(cases.every(item => !item.errors.length && !item.layout_overflow), 'Replay cases failed; inspect recorded observations');
  } finally {
    if (browser) await browser.close();
    if (service.exitCode === null) {
      spawn('taskkill', ['/PID', String(service.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore'});
      await new Promise(resolve => service.once('exit', resolve));
    }
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
