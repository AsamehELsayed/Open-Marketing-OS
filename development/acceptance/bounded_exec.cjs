/**
 * Bounded process execution for installer/uninstaller acceptance steps.
 *
 * Why this exists: NSIS shows a modal message box ("NSIS Error", "Retry/Cancel", ...) even
 * under /S when something is wrong. With an unbounded child_process.execSync that dialog
 * blocks an automated run forever, and nothing says why. This helper
 *   - always enforces a timeout,
 *   - samples the process tree's top-level windows while waiting and keeps any dialog text,
 *   - kills the whole tree on timeout,
 *   - returns a structured result (exit code, timing, dialogs) instead of throwing.
 */
'use strict';

const cp = require('child_process');
const path = require('path');

const SNAPSHOT_SCRIPT = path.resolve(__dirname, 'window_snapshot.ps1');

function snapshotWindows(pid) {
  try {
    const out = cp.execFileSync(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SNAPSHOT_SCRIPT, '-RootPid', String(pid)],
      { encoding: 'utf8', timeout: 15000, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] },
    ).trim();
    return out ? JSON.parse(out) : [];
  } catch {
    return [];
  }
}

function killTree(pid) {
  try {
    cp.execFileSync('taskkill.exe', ['/F', '/T', '/PID', String(pid)], { stdio: 'ignore', timeout: 15000 });
  } catch {
    /* already gone */
  }
}

/**
 * @param {string} exe
 * @param {string[]} args
 * @param {{timeoutMs?: number, label?: string, env?: object, pollMs?: number}} opts
 * @returns {Promise<{label:string, exitCode:(number|null), timedOut:boolean, elapsedMs:number, dialogs:object[]}>}
 */
function runBounded(exe, args = [], opts = {}) {
  const { timeoutMs = 120000, label = path.basename(exe), env = process.env, pollMs = 3000 } = opts;
  return new Promise((resolve) => {
    const started = Date.now();
    const dialogs = [];
    const seen = new Set();
    let finished = false;
    const child = cp.spawn(exe, args, { env, stdio: 'ignore', windowsHide: false });

    const finish = (result) => {
      if (finished) return;
      finished = true;
      clearInterval(poll);
      clearTimeout(deadline);
      resolve({ label, elapsedMs: Date.now() - started, dialogs, ...result });
    };

    const sample = () => {
      if (finished || !child.pid) return;
      for (const w of snapshotWindows(child.pid)) {
        if (!w.visible || !/^#32770$/.test(w.class)) continue; // dialog boxes only
        const key = `${w.pid}|${w.title}|${w.text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        dialogs.push({ title: w.title, text: w.text, atMs: Date.now() - started });
      }
    };
    const poll = setInterval(sample, pollMs);
    const deadline = setTimeout(() => {
      sample();
      killTree(child.pid);
      finish({ exitCode: null, timedOut: true });
    }, timeoutMs);

    child.on('error', (err) => finish({ exitCode: null, timedOut: false, error: String(err) }));
    child.on('exit', (code) => finish({ exitCode: code, timedOut: false }));
  });
}

/** One-line human summary used in acceptance output and failure messages. */
function describe(result) {
  const parts = [`exit=${result.exitCode}`, `${result.elapsedMs}ms`];
  if (result.timedOut) parts.push('TIMED OUT (process tree killed)');
  if (result.error) parts.push(`error=${result.error}`);
  for (const d of result.dialogs) {
    parts.push(`DIALOG[${d.title}]: ${String(d.text).replace(/\s+/g, ' ').slice(0, 240)}`);
  }
  return `${result.label}: ${parts.join(', ')}`;
}

module.exports = { runBounded, describe, killTree, snapshotWindows };
