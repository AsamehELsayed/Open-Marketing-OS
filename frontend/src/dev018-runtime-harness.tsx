import React from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import TurnRuntimePanel from "./components/runtime/TurnRuntimePanel";
import "./styles/index.css";

const scenario = new URLSearchParams(window.location.search).get("scenario") === "success"
  ? "success"
  : "failure";
const turnId = scenario === "success" ? "dev018-synthetic-success-turn" : "dev018-synthetic-failed-turn";

type Listener = (event: MessageEvent<string>) => void;
class LocalSyntheticEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Listener[]>();
  private closed = false;

  constructor(url: string) {
    const expected = `/chat/turns/${encodeURIComponent(turnId)}/events?after=0`;
    if (url !== expected) throw new Error(`Synthetic harness blocked unexpected EventSource: ${url}`);
    window.setTimeout(() => this.emitScenario(), 30);
  }

  addEventListener(type: string, callback: EventListenerOrEventListenerObject): void {
    const listener: Listener = (event) => {
      if (typeof callback === "function") callback(event);
      else callback.handleEvent(event);
    };
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  private emit(type: string, id: number, meta: Record<string, unknown>): void {
    if (this.closed) return;
    const event = new MessageEvent<string>(type, {
      data: JSON.stringify({ label: type, detail: "Synthetic DEV-018 fixture", meta }),
      lastEventId: String(id),
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  private emitScenario(): void {
    if (scenario === "failure") {
      this.emit("synthesis_completed", 8, {
        model_visibility: {
          generation_attempt: {
            generation_source: "none",
            generation_status: "failed",
            call_id: "dev018-synthetic-failed-call",
            invocation_started: true,
            provider: "openrouter",
            requested_model: "stealth/space-bunny-alpha",
            route_mode: "OPENROUTER",
            route_reason: "explicit user selection",
            error_type: "ModelCallFailure",
            attempt_latency_ms: 10827,
          },
          retrieval_telemetry: {
            mode: "HYBRID",
            lexical_hits: 2,
            vector_hits: 3,
            fused_hits: 4,
            selected_chunks: [{ chunk_id: "synthetic-c000", file_id: "synthetic-knowledge-file" }],
          },
        },
      });
      this.emit("turn_failed", 9, {});
      return;
    }

    this.emit("model_completed", 8, {
      call_id: "dev018-synthetic-success-call",
      provider: "openrouter",
      model: "synthetic/fake-openrouter-model",
      requested_model: "synthetic/fake-openrouter-model",
      route_mode: "OPENROUTER",
      route_reason: "DEV-018 loopback fake server fixture",
      latency_ms: 381,
      input_tokens: 91,
      output_tokens: 48,
      total_tokens: 139,
      estimated_cost_usd: 0,
      cost_note: "Synthetic plumbing value; not provider usage",
    });
    this.emit("turn_completed", 9, {});
  }
}

declare global {
  interface Window {
    EventSource: typeof EventSource;
  }
}

window.EventSource = LocalSyntheticEventSource as unknown as typeof EventSource;
window.fetch = async (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === "string" ? input : input.toString();
  const expected = `/api/turns/${encodeURIComponent(turnId)}/model-calls`;
  if (url !== expected) throw new Error(`Synthetic harness blocked unexpected fetch: ${url}`);
  return new Response(JSON.stringify({ ok: true, data: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

function Harness() {
  return (
    <main className="min-h-screen bg-base p-8 text-ink">
      <div className="mx-auto grid max-w-3xl gap-4">
        <header className="rounded-md border border-accent/40 bg-raised p-4">
          <p className="text-meta font-bold uppercase tracking-wide text-accent">DEV-018 — local plumbing fixture</p>
          <h1 className="mt-1 text-xl font-semibold">Runtime telemetry display harness</h1>
          <p className="mt-2 text-bodysm text-inksecondary">
            Synthetic SSE is injected in memory. REST is stubbed in memory. All other fetches are blocked. No provider or backend requests occur.
          </p>
          <nav className="mt-3 flex gap-3 text-meta">
            <a className="text-accent hover:underline" href="?scenario=failure">Failed attempt</a>
            <a className="text-accent hover:underline" href="?scenario=success">Fake success plumbing</a>
          </nav>
        </header>
        <TurnRuntimePanel turnId={turnId} />
        {scenario === "failure" ? (
          <section className="rounded-md border border-linesubtle bg-raised p-4" aria-label="Synthetic retrieved evidence">
            <h2 className="font-semibold text-ink">Retrieved evidence · synthetic citation</h2>
            <p className="mt-2 text-bodysm text-inksecondary">[synthetic-c000] Business knowledge fixture: fictional campaign planning notes.</p>
            <p className="mt-2 text-meta text-inkmuted">The evidence citation is shown separately; no generated answer is available for this failed attempt.</p>
          </section>
        ) : (
          <section className="rounded-md border border-warn/40 bg-raised p-4" aria-label="Synthetic answer plumbing only">
            <p className="text-meta font-bold uppercase tracking-wide text-warn">PLUMBING ONLY — synthetic fake-server response</p>
            <h2 className="mt-1 font-semibold text-ink">Synthetic answer</h2>
            <p className="mt-2 text-bodysm text-inksecondary">This fictional answer verifies display wiring only and is not a real provider result.</p>
            <p className="mt-2 text-meta text-inkmuted">Citation: [synthetic-c000] fictional campaign planning notes.</p>
          </section>
        )}
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MemoryRouter><Harness /></MemoryRouter>
  </React.StrictMode>,
);
