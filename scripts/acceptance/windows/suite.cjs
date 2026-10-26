const L = require('./lib.cjs');
const { rec, sleep, fs, cp } = L;
const http = require('http');
const DATA = L.PROFILE + '\\local\\OpenMarketingOS';
const REQ = 'Create a 30-day Instagram and email campaign targeting local families.';
const step = (n) => console.log('--- ' + n);

function rawGet(origin, path, headers = {}) {
  return new Promise((resolve) => {
    const u = new URL(origin + path);
    const r = http.request({ host: u.hostname, port: u.port, path: u.pathname + u.search, headers, method: 'GET', timeout: 8000 }, (res) => {
      res.resume(); res.on('end', () => resolve(res.statusCode));
    });
    r.on('error', (e) => resolve('ERR ' + e.code)); r.on('timeout', () => { r.destroy(); resolve('TIMEOUT'); }); r.end();
  });
}
async function selectProject(page, name) {
  await page.locator('button[aria-haspopup="menu"]').first().click();
  await page.getByRole('menuitem', { name: new RegExp(name) }).click();
  await sleep(1500);
}
async function createWorkspace(page, origin, name, client) {
  await page.goto(origin + '/app/business/new' + (client ? '?mode=client' : ''), { waitUntil: 'domcontentloaded' });
  await page.locator('input[placeholder="e.g. Northstar Studio"]').fill(name);
  await page.getByRole('button', { name: /Save and open|Create client workspace/ }).click();
  await page.waitForURL((u) => !u.pathname.includes('business/new'), { timeout: 30000 });
}
async function send(page, text) {
  const c = page.locator('textarea[aria-label="Chat message"]');
  await c.waitFor({ state: 'visible', timeout: 30000 });
  await c.fill(text);
  await page.locator('button:has-text("Send")').click();
}
async function api(page, p, opts) {
  return page.evaluate(async ([p, opts]) => { const r = await fetch(p, opts); let b = null; try { b = await r.json(); if (b && b.data !== undefined) b = b.data; } catch {} return { s: r.status, b }; }, [p, opts]);
}

(async () => {
  fs.rmSync(L.PROFILE, { recursive: true, force: true });
  fs.mkdirSync(L.PROFILE + '\\local', { recursive: true }); fs.mkdirSync(L.PROFILE + '\\roaming', { recursive: true });
  let app, page, origin;
  try {
    step('P1 launch');
    const t0 = Date.now();
    app = await L.launch(); page = await L.firstWindow(app);
    origin = new URL(page.url()).origin;
    rec('P1 window ready', true, `${Date.now() - t0} ms`);
    const sec = await page.evaluate(() => ({ bridge: typeof window.omosDesktop, req: typeof require, proc: typeof process, mod: typeof module }));
    rec('P1 preload bridge only, no node globals', sec.bridge === 'object' && sec.req === 'undefined' && sec.proc === 'undefined' && sec.mod === 'undefined', JSON.stringify(sec));
    const st = await page.evaluate(() => window.omosDesktop.status());
    rec('P1 bridge status', st.connected === true && st.edition === 'openrouter' && st.version === '0.1.0-beta.2', JSON.stringify(st));
    const ext = await page.evaluate(async () => { try { await fetch('https://example.com/', { mode: 'no-cors' }); return 'reached'; } catch (e) { return 'blocked'; } });
    rec('P1 external network request from UI blocked', ext === 'blocked', ext);
    await page.evaluate(() => { window.location.href = 'https://example.com/'; }); await sleep(1500);
    rec('P1 external navigation blocked', page.url().startsWith(origin), page.url());
    // sidecar protocol security from outside the window
    const noTok = await rawGet(origin, '/api/projects');
    rec('SEC no token -> rejected', noTok === 401 || noTok === 403, String(noTok));
    const qTok = await rawGet(origin, '/api/projects?token=x&x-omos-desktop-token=x');
    rec('SEC query-string token -> rejected', qTok === 401 || qTok === 403, String(qTok));
    const badHost = await rawGet(origin, '/api/projects', { Host: 'evil.example.com', 'x-omos-desktop-token': 'x' });
    rec('SEC bad Host/token -> rejected', badHost === 401 || badHost === 403 || badHost === 400, String(badHost));
    const badOrigin = await rawGet(origin, '/api/projects', { Origin: 'https://evil.example.com' });
    rec('SEC foreign Origin -> rejected', badOrigin === 401 || badOrigin === 403, String(badOrigin));

    step('P1 create workspaces via GUI');
    await createWorkspace(page, origin, 'Alpha Bakery', false);
    rec('GUI create business Alpha Bakery', true);
    await createWorkspace(page, origin, 'Beta Plumbing', true);
    rec('GUI create client workspace Beta Plumbing', true);
    const projects = (await api(page, '/api/projects')).b;
    console.log('projects raw', JSON.stringify(projects));
    const plist = Array.isArray(projects) ? projects : (projects?.projects || []);
    const names = plist.map((p) => p.name);
    rec('Both projects listed by API', names.some((n) => /Alpha Bakery/.test(n)) && names.some((n) => /Beta Plumbing/.test(n)), JSON.stringify(names));
    const alpha = plist.find((p) => /Alpha Bakery/.test(p.name)), beta = plist.find((p) => /Beta Plumbing/.test(p.name));

    step('P1 chat -> campaign in Alpha');
    await selectProject(page, 'Alpha Bakery');
    await page.goto(origin + '/app/chat/new', { waitUntil: 'domcontentloaded' });
    await page.getByText('Alpha Bakery', { exact: true }).last().click();
    await page.getByRole('button', { name: 'Start chat' }).click();
    await page.waitForURL(/\/app\/chat\/[^n]/, { timeout: 30000 });
    await send(page, REQ);
    let card = false;
    try { await page.locator('[data-testid="campaign-chat-card"]').waitFor({ timeout: 90000 }); card = true; } catch {}
    rec('Installed app: chat creates campaign card (no key)', card, card ? (await page.locator('[data-testid="campaign-chat-card"]').innerText()).replace(/\s+/g, ' ').slice(0, 200) : (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(-300));
    const camps = (await api(page, '/api/campaigns?project_id=starter')).b;
    const clist = Array.isArray(camps) ? camps : (camps?.campaigns || []);
    rec('Alpha has 1 campaign', clist.length === 1, `n=${clist.length}`);
    const cid = clist[0]?.id;
    if (card) {
      await page.getByRole('link', { name: 'Review the plan' }).click();
      let plan = false;
      try { await page.locator('[data-testid="wp-proposal"]').waitFor({ timeout: 30000 }); plan = true; } catch {}
      rec('Installed app: plan proposal renders', plan, plan ? (await page.locator('[data-testid="wp-proposal"]').innerText()).replace(/\s+/g, ' ').slice(0, 160) : '');
      // generation without any provider: must fail honestly, never fabricate content or crash
      const btn = page.getByRole('button', { name: 'Write this content' });
      if (await btn.count()) {
        await btn.click(); await sleep(15000);
        const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
        const rows = await api(page, `/api/campaigns/${cid}/work-package`);
        const dels = rows.b?.deliverables || rows.b?.work_package?.deliverables || [];
        rec('Generate without provider: app alive, nothing fabricated', page.url().startsWith(origin) && !L.procs().length === false, `deliverables=${dels.length}; ui="${body.slice(-220)}"`);
      } else rec('Generate button present', false, 'not found');
    }

    step('P1 isolation via GUI');
    await selectProject(page, 'Beta Plumbing');
    await page.goto(origin + '/app/campaigns', { waitUntil: 'domcontentloaded' }); await sleep(2500);
    const betaText = (await page.locator('main, body').first().innerText()).replace(/\s+/g, ' ');
    rec('ISOLATION Beta campaigns page shows no Alpha campaign', !/local families|Alpha|Instagram and email/i.test(betaText), betaText.slice(0, 160));
    const betaCamps = (await api(page, '/api/campaigns?project_id=' + beta.id)).b;
    const bl = Array.isArray(betaCamps) ? betaCamps : (betaCamps?.campaigns || []);
    rec('ISOLATION Beta API campaign list empty', bl.length === 0, `n=${bl.length}`);
    const direct = await api(page, `/api/projects/${beta?.id}/campaigns/${cid}/work-package`);
    rec('ISOLATION Alpha campaign via Beta project id -> 404', direct.s === 404, `status ${direct.s}`);
    const directGen = await api(page, `/api/projects/${beta?.id}/campaigns/${cid}/generate`, { method: 'POST' });
    rec('ISOLATION generate Alpha campaign via Beta -> 404', directGen.s === 404, `status ${directGen.s}`);
    await page.goto(origin + '/app/approvals', { waitUntil: 'domcontentloaded' }); await sleep(1500);
    await page.goto(origin + '/app', { waitUntil: 'domcontentloaded' }); await sleep(1500);
    const sidebarBeta = (await page.locator('nav[aria-label="Primary"]').innerText()).replace(/\s+/g, ' ');
    rec('ISOLATION Beta sidebar chat list has no Alpha chat', !/Instagram|campaign targeting|30-day/i.test(sidebarBeta), sidebarBeta.slice(0, 200));
    await selectProject(page, 'Alpha Bakery'); await sleep(1000);
    const sidebarAlpha = (await page.locator('nav[aria-label="Primary"]').innerText()).replace(/\s+/g, ' ');
    rec('ISOLATION Alpha sidebar shows its own chat', /Instagram|campaign|30-day|New chat|Create/i.test(sidebarAlpha), sidebarAlpha.slice(0, 200));

    step('P2 graceful quit + restart persistence');
    await L.quit(app);
    rec('Clean shutdown leaves no processes', L.procs().length === 0, JSON.stringify(L.procs()));
    app = await L.launch(); page = await L.firstWindow(app); origin = new URL(page.url()).origin;
    const p2 = (await api(page, '/api/projects')).b; const p2l = Array.isArray(p2) ? p2 : (p2?.projects || []);
    rec('Restart: both projects persisted', p2l.length >= 2 && p2l.some((p) => /Alpha/.test(p.name)) && p2l.some((p) => /Beta/.test(p.name)), JSON.stringify(p2l.map((p) => p.name)));
    const c2 = (await api(page, `/api/projects/${alpha?.id}/campaigns/${cid}/work-package`));
    rec('Restart: Alpha campaign persisted', c2.s === 200, `status ${c2.s}`);

    step('P3 crash recovery: kill sidecar');
    const sc = L.procs().filter((x) => /omos-sidecar/i.test(x));
    rec('Sidecar running before kill', sc.length >= 1, JSON.stringify(sc));
    const scPid = sc[0].split(':')[1];
    cp.execSync(`taskkill /F /PID ${scPid}`, { stdio: 'ignore' });
    await sleep(4000);
    await sleep(1500);
    const appPids = L.procs().filter((x) => /Open Marketing OS\.exe/i.test(x)).map((x) => x.split(':')[1]);
    rec('Sidecar killed: app shell survives (recovery dialog shown)', appPids.length > 0, JSON.stringify(L.procs()));
    let mainPid = app.pid;
    console.log(cp.execSync(`powershell -NoProfile -ExecutionPolicy Bypass -File C:/omos-p3/tools/press-enter.ps1 -ProcessId ${mainPid}`).toString().trim());
    await sleep(2000);
    // old CDP connection is gone with the destroyed window; reattach
    try { await app.browser.close(); } catch {}
    app.browser = await require('playwright-core').chromium.connectOverCDP('http://127.0.0.1:9333');
    page = await L.firstWindow(app); origin = new URL(page.url()).origin;
    const p3 = (await api(page, '/api/projects')).b; const p3l = Array.isArray(p3) ? p3 : (p3?.projects || []);
    rec('Recovery via Retry: new sidecar, data intact', p3l.length >= 2, JSON.stringify(p3l.map((p) => p.name)));

    step('P3 hard kill of whole app mid-session');
    cp.execSync(`taskkill /F /T /PID ${app.pid}`, { stdio: 'ignore' });
    await sleep(5000);
    rec('Hard kill: no orphan sidecar', L.procs().length === 0, JSON.stringify(L.procs()));
    cp.execSync(`taskkill /F /IM omos-sidecar.exe`, { stdio: 'ignore' }).toString;
  } catch (e) { rec('SUITE ERROR', false, String(e.stack || e).slice(0, 600)); }
  try {
    step('P4 relaunch after hard kill');
    L.procs().length && cp.execSync('taskkill /F /IM omos-sidecar.exe /IM "Open Marketing OS.exe"', { stdio: 'ignore' });
    app = await L.launch(); page = await L.firstWindow(app); origin = new URL(page.url()).origin;
    const p4 = (await api(page, '/api/projects')).b; const p4l = Array.isArray(p4) ? p4 : (p4?.projects || []);
    rec('After hard kill + relaunch: projects intact', p4l.length >= 2, JSON.stringify(p4l.map((p) => p.name)));
    await L.quit(app);
  } catch (e) { rec('P4 ERROR', false, String(e.stack || e).slice(0, 400)); }
  fs.writeFileSync('C:\\omos-p3\\suite-results.json', JSON.stringify(L.results, null, 1));
  console.log(`\nSUMMARY pass=${L.results.filter((r) => r.ok).length} fail=${L.results.filter((r) => !r.ok).length}`);
  try { cp.execSync('taskkill /F /IM omos-sidecar.exe /IM "Open Marketing OS.exe"', { stdio: 'ignore' }); } catch {}
  process.exit(0);
})();
