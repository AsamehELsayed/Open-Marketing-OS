import React, { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import TurnRuntimePanel from "./components/runtime/TurnRuntimePanel";
import "./styles/index.css";

const turnId = "dev023-synthetic-hybrid-turn";

class LocalSyntheticEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, EventListener[]>();
  private closed = false;

  constructor(url: string) {
    const expected = `/chat/turns/${encodeURIComponent(turnId)}/events?after=0`;
    if (url !== expected) throw new Error(`Blocked unexpected EventSource: ${url}`);
    window.setTimeout(() => this.emitFixture(), 0);
  }
  addEventListener(type: string, callback: EventListenerOrEventListenerObject): void {
    const listener: EventListener = (event) => {
      if (typeof callback === "function") callback(event);
      else callback.handleEvent(event);
    };
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close(): void { this.closed = true; }
  private emit(type: string, id: number, meta: Record<string, unknown>): void {
    if (this.closed) return;
    const event = new MessageEvent<string>(type, {
      data: JSON.stringify({ label: type, detail: "Synthetic DEV-023 SSE fixture", meta }),
      lastEventId: String(id),
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
  private emitFixture(): void {
    this.emit("model_completed", 1, {
      turn_id: turnId,
      call_id: "dev023-fake-call-001",
      provider: "local-fake",
      model: "fixture/model-v1",
      requested_model: "fixture/model-v1",
      route_mode: "LOCAL",
      route_reason: "offline synthetic pipeline fixture",
      generation_source: "deterministic",
      generation_status: "completed",
      network_attempted: false,
      http_attempt_count: 0,
      http_response_count: 0,
      latency_ms: 12,
      input_tokens: 9,
      output_tokens: 7,
      total_tokens: 16,
      retrieval_telemetry: {
        retrieval_mode: "HYBRID",
        lexical_hits: 1,
        vector_hits: 1,
        fused_hits: 1,
        selected_chunk_ids: ["c000"],
        source_file_ids: ["fake.md"],
      },
    });
    this.emit("turn_completed", 2, {});
  }
}

declare global { interface Window { EventSource: typeof EventSource } }
window.EventSource = LocalSyntheticEventSource as unknown as typeof EventSource;
window.fetch = async (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === "string" ? input : input.toString();
  const expected = `/api/turns/${encodeURIComponent(turnId)}/model-calls`;
  if (url !== expected) throw new Error(`Blocked unexpected fetch: ${url}`);
  return new Response(JSON.stringify({ ok: true, data: [] }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
};

function Harness() {
  useEffect(() => {
    document.documentElement.style.zoom = "65%";
    const expected = [
      "fixture/model-v1", "HYBRID", "lexical 1", "vector 1", "fused 1",
      "Selected chunks: c000", "Sources: fake.md", "Citation:",
      "A focused checkout form",
    ];
    const verify = () => {
      const rendered = document.body.innerText;
      const complete = expected.every((value) => rendered.includes(value));
      document.documentElement.dataset.dev023ReactRenderVerification = complete ? "PASS" : "FAIL";
      const status = document.getElementById("dev023-render-status");
      const label = complete
        ? "Mounted React render verified against the synthetic SSE fixture."
        : "Waiting for all synthetic SSE values to render.";
      if (status && status.textContent !== label) status.textContent = label;
    };
    const observer = new MutationObserver(verify);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    window.setTimeout(verify, 80);
    return () => observer.disconnect();
  }, []);
  return <main className="min-h-screen bg-base p-8 text-ink">
    <div className="mx-auto grid max-w-3xl gap-4">
      <header className="rounded-md border border-accent/40 bg-raised p-4">
        <p className="text-meta font-bold uppercase tracking-wide text-accent">DEV-023 — OFFLINE SYNTHETIC FIXTURE</p>
        <h1 className="mt-1 text-xl font-semibold">React answer, citation, and runtime evidence</h1>
        <p className="mt-2 text-bodysm text-inksecondary">In-memory synthetic SSE fixture; REST and EventSource are locally stubbed. External network and provider access are not used.</p>
        <p id="dev023-render-status" className="mt-2 text-meta text-accent">Checking mounted React render…</p>
      </header>
      <article className="rounded-md border border-linesubtle bg-raised p-4" aria-label="Synthetic answer with citation">
        <p className="text-meta font-bold uppercase tracking-wide text-accent">Synthetic answer</p>
        <p className="mt-2 text-bodysm text-inksecondary">A focused checkout form and a reminder for abandoned carts are reasonable tests for this fictional shop.</p>
        <p className="mt-3 text-meta text-inkmuted">Citation: <a href="#source-c000">[c000] fake.md</a></p>
        <p id="source-c000" className="mt-1 text-meta text-inkmuted">Synthetic source reference only; no retrieved body is displayed.</p>
      </article>
      <TurnRuntimePanel turnId={turnId} />
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><MemoryRouter><Harness /></MemoryRouter></React.StrictMode>);
