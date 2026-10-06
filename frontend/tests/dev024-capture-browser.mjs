import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

function args(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) throw new Error(`Unexpected argument: ${argv[i]}`);
    const key = argv[i].slice(2);
    if (key === "help" || key === "assert-replay-values" || key === "assert-full-turn") result[key] = true;
    else result[key] = argv[++i];
  }
  return result;
}

function loopbackUrl(value) {
  const url = new URL(value);
  if (!new Set(["localhost", "127.0.0.1", "::1", "[::1]"]).has(url.hostname)) {
    throw new Error("The capture page must be served from localhost or a loopback IP.");
  }
  return url;
}

async function sha256(file) {
  return createHash("sha256").update(await fs.readFile(file)).digest("hex");
}

const options = args(process.argv.slice(2));
if (options.help || !options.url || (!options["output-dir"] && !options["assert-full-turn"])) {
  console.log(
    "Usage: node tests/dev024-capture-browser.mjs --url http://127.0.0.1:5173/app/chat?... [--output-dir <folder>] [--storage-state <playwright-state.json>] [--citation-selector <css>] [--assert-replay-values | --assert-full-turn]",
  );
  process.exit(options.help ? 0 : 2);
}

const target = loopbackUrl(options.url);
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const outputDir = path.resolve(
  options["output-dir"] || path.join(repoRoot, "development", "runs", "DEV-024", "evidence", "browser-replay-full-turn"),
);
await fs.mkdir(outputDir, { recursive: true });
const launchOptions = { headless: true };
if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) {
  launchOptions.executablePath = path.resolve(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE);
}
const browser = await chromium.launch(launchOptions);
try {
  const context = await browser.newContext(
    options["storage-state"] ? { storageState: path.resolve(options["storage-state"]) } : {},
  );
  const blocked = [];
  await context.route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (new Set(["localhost", "127.0.0.1", "::1", "[::1]"]).has(requestUrl.hostname)) {
      await route.continue();
    } else {
      blocked.push(`${requestUrl.origin}${requestUrl.pathname}`);
      await route.abort("blockedbyclient");
    }
  });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") console.error(`[browser console] ${message.text()}`);
  });
  page.on("pageerror", (error) => console.error(`[browser page error] ${error.message}`));
  await page.goto(target.href, { waitUntil: "domcontentloaded" });

  let fullTurnResult = null;
  if (options["assert-full-turn"]) {
    const resultResponse = await page.request.get(`${target.origin}/dev024/full-turn-result.json`);
    if (!resultResponse.ok()) throw new Error(`Full-turn result endpoint returned ${resultResponse.status()}`);
    fullTurnResult = await resultResponse.json();
    if (typeof fullTurnResult.assistant_answer !== "string" ||
        !Array.isArray(fullTurnResult.citation_source_file_ids) ||
        !fullTurnResult.model_completed_sse ||
        !fullTurnResult.generation_telemetry) {
      throw new Error("Full-turn result is missing the agreed answer, citation, SSE, or generation telemetry fields");
    }
  }

  const answer = page.getByTestId("answer-content").last();
  const runtimePanel = page.getByTestId("turn-runtime-panel").last();
  await answer.waitFor({ state: "visible", timeout: 30_000 });
  await runtimePanel.waitFor({ state: "visible", timeout: 30_000 });
  const citation = page.locator(
    options["citation-selector"] || '[data-testid="citation"], [data-citation], [data-testid="answer-content"] a[href]',
  ).last();
  await citation.waitFor({ state: "visible", timeout: 5_000 });
  if (options["assert-full-turn"]) {
    const citationId = fullTurnResult.citation_source_file_ids[0];
    if (typeof citationId !== "string" || !citationId) throw new Error("Full-turn citation source ID is empty");
    const citationHref = await citation.getAttribute("href");
    if (!citationHref?.startsWith("#citation-")) throw new Error("Full-turn citation is not a local link");
  }

  const files = [
    ["answer", path.join(outputDir, "answer.png"), answer],
    ["citation", path.join(outputDir, "citation.png"), citation],
  ];
  for (const [, file, locator] of files) {
    await locator.screenshot({ path: file });
  }

  const badge = runtimePanel.locator('button[aria-haspopup="dialog"]').first();
  await badge.click();
  const details = runtimePanel.getByRole("dialog", { name: "Model Telemetry Details" });
  await details.waitFor();
  if (options["assert-replay-values"]) {
    const rendered = (await details.innerText()).toLowerCase();
    for (const expected of [
      "openrouter",
      "requested model",
      "actual model",
      "stealth/space-bunny-alpha",
      "http attempts",
      "1",
      "http status",
      "200",
      "26,708 ms",
    ]) {
      if (!rendered.includes(expected)) {
        throw new Error(`Runtime telemetry UI did not render expected value: ${expected}`);
      }
    }
  }
  if (options["assert-full-turn"]) {
    const telemetry = fullTurnResult.generation_telemetry;
    const latencyMs = telemetry.latency_ms;
    if (typeof latencyMs !== "number" || !Number.isFinite(latencyMs) || latencyMs < 0) {
      throw new Error("Full-turn result has no numeric generation_telemetry.latency_ms");
    }
    if (telemetry.requested_model !== "offline/requested-model" ||
        (telemetry.actual_model ?? telemetry.model) !== "offline/fake-openrouter" ||
        telemetry.input_tokens !== 81 || telemetry.output_tokens !== 24) {
      throw new Error("Full-turn result does not match the agreed model/token assertions");
    }
    const sseData = fullTurnResult.model_completed_sse.data ?? fullTurnResult.model_completed_sse;
    const sseMeta = sseData.meta ?? fullTurnResult.model_completed_sse.meta ?? {};
    const sseTelemetry = sseMeta.telemetry ?? sseData.telemetry ?? fullTurnResult.model_completed_sse.telemetry;
    if (!sseTelemetry ||
        sseTelemetry.requested_model !== telemetry.requested_model ||
        (sseTelemetry.actual_model ?? sseTelemetry.model) !== (telemetry.actual_model ?? telemetry.model) ||
        sseTelemetry.input_tokens !== telemetry.input_tokens ||
        sseTelemetry.output_tokens !== telemetry.output_tokens ||
        sseTelemetry.latency_ms !== latencyMs) {
      throw new Error("Persisted model_completed_sse telemetry does not match generation_telemetry");
    }
    const rendered = (await details.innerText()).toLowerCase();
    const expected = [
      String(telemetry.provider),
      String(telemetry.requested_model),
      String(telemetry.actual_model ?? telemetry.model),
      String(telemetry.input_tokens),
      String(telemetry.output_tokens),
      `${new Intl.NumberFormat("en-US").format(latencyMs)} ms`,
    ];
    for (const value of expected) {
      if (!rendered.includes(value.toLowerCase())) {
        throw new Error(`Full-turn runtime UI did not render result value: ${value}`);
      }
    }
    const answerText = (await answer.innerText()).replace(/\s+/g, " ").trim();
    const expectedText = fullTurnResult.assistant_answer
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
    if (answerText !== expectedText) throw new Error("Rendered ChatMessage text differs from the exact full-turn answer projection");
  }
  const runtimeFile = path.join(outputDir, "runtime.png");
  await runtimePanel.screenshot({ path: runtimeFile });
  files.push(["runtime", runtimeFile, runtimePanel]);

  const manifest = {
    capture_kind: "real_browser_dom_screenshot",
    browser: await browser.version(),
    page_url: `${target.origin}${target.pathname}`,
    replay_mode: options["assert-full-turn"] ? "full-turn" : "historical-telemetry",
    captured_at: new Date().toISOString(),
    replay_value_assertions: Boolean(options["assert-replay-values"]),
    full_turn_assertions: Boolean(options["assert-full-turn"]),
    blocked_non_loopback_requests: blocked,
    screenshots: await Promise.all(
      files.map(async ([name, file]) => ({ name, path: file, sha256: await sha256(file) })),
    ),
  };
  const manifestPath = path.join(outputDir, "manifest.json");
  await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(`Saved actual browser screenshots and hashes to ${manifestPath}`);
} finally {
  await browser.close();
}
