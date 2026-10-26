const { chromium } = require('playwright-core');
const fs = require('fs'), path = require('path'), cp = require('child_process');
const EXE = 'C:\\omos-p3\\install\\Open Marketing OS.exe';
const PROFILE = 'C:\\omos-p3\\profile';
const PORT = 9333;
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch(extra = {}) {
  const env = { ...process.env, LOCALAPPDATA: PROFILE + '\\local', APPDATA: PROFILE + '\\roaming', ...extra };
  const child = cp.spawn(EXE, ['--remote-debugging-port=' + PORT], { env, stdio: 'ignore' });
  child.on('error', () => {});
  let browser;
  for (let i = 0; i < 240 && !browser; i++) {
    try { browser = await chromium.connectOverCDP('http://127.0.0.1:' + PORT); } catch { await sleep(500); }
  }
  if (!browser) { child.kill(); throw new Error('CDP attach failed'); }
  return { child, browser, pid: child.pid };
}
async function firstWindow(app) {
  const t = Date.now();
  while (Date.now() - t < 120000) {
    for (const ctx of app.browser.contexts()) for (const p of ctx.pages()) if (p.url().includes('127.0.0.1')) {
      await p.waitForSelector('nav[aria-label="Primary"]', { state: 'attached', timeout: 120000 }); return p; }
    await sleep(500);
  }
  throw new Error('no window');
}
function procs() {
  const out = cp.execSync('tasklist /FO CSV /NH').toString();
  return out.split('\n').filter(l => /Open Marketing OS\.exe|omos-sidecar\.exe/i.test(l)).map(l => l.split('","').slice(0, 2).join(':').replace(/"/g, ''));
}
async function quit(app) {
  try { await app.browser.close(); } catch {}
  try { cp.execSync('taskkill /PID ' + app.pid, { stdio: 'ignore' }); } catch {}
  for (let i = 0; i < 40 && procs().length; i++) await sleep(500);
}
const results = [];
function rec(name, ok, detail = '') { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`); }
module.exports = { launch, firstWindow, procs, quit, rec, results, sleep, PROFILE, EXE, fs, path, cp };
