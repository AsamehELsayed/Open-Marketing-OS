import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import { chromium } from "playwright";
import ts from "typescript";

const copySource = fs.readFileSync(new URL("../src/credentialProtectionCopy.ts", import.meta.url), "utf8");
const copyJs = ts.transpileModule(copySource, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { credentialProtectionCopy } = await import(`data:text/javascript;base64,${Buffer.from(copyJs).toString("base64")}`);
for (const invalid of [null, undefined, "", "test", "<script>secret</script>"]) {
  assert.equal(credentialProtectionCopy(invalid).configured, false);
  assert.doesNotMatch(credentialProtectionCopy(invalid).text, /<script>|test/);
}
assert.match(credentialProtectionCopy("dpapi_file").text, /Windows DPAPI/);
assert.match(credentialProtectionCopy("linux_secret_service").text, /Linux Secret Service/);

// Real browser: run the sanitizer as a self-hosted script under production-style
// script policy. No external requests, provider calls, or browser downloads.
const purify = fs.readFileSync(new URL("../node_modules/dompurify/dist/purify.min.js", import.meta.url));
const fixture = `window.xss = 0;
  const html = DOMPurify.sanitize('<h2>Plan</h2><table><tr><td>Proof</td></tr></table><img src=x onerror="window.xss++"><a href="javascript:window.xss++">bad</a><script>window.xss++<\\/script>');
  document.querySelector('#content').innerHTML = html;
  window.fixture = { html, style: getComputedStyle(document.querySelector('#content')).width };
`;
const server = http.createServer((req, res) => {
  res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self'");
  if (req.url === "/purify.js" || req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(req.url === "/purify.js" ? purify : fixture);
  } else if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end('<!doctype html><div id="content" style="width: 123px"></div><script src="/purify.js"></script><script src="/fixture.js"></script>');
  } else { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch(process.env.OMOS_TEST_BROWSER ? { executablePath: process.env.OMOS_TEST_BROWSER } : {});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const result = await page.evaluate(() => ({ ...window.fixture, xss: window.xss }));
  assert.deepEqual(errors, []);
  assert.equal(result.xss, 0);
  assert.equal(result.style, "123px");
  assert.match(result.html, /<h2>Plan<\/h2>/);
  assert.match(result.html, /<td>Proof<\/td>/);
  assert.doesNotMatch(result.html, /onerror|javascript:|<script/i);
  console.log("DEV-040 renderer security: vault copy, DOMPurify and CSP browser checks PASS");
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
