import React from "react";
import { createRoot } from "react-dom/client";
import type { ModelCallInfo } from "../src/api/runtime";
import ModelBadge from "../src/components/runtime/ModelBadge";
import "../src/styles/index.css";

const scenario = new URLSearchParams(window.location.search).get("scenario") || "failed";
const failed = scenario !== "success";

const call: ModelCallInfo = failed
  ? {
      callId: "synthetic-failed-attempt",
      turnId: "dev018-fixture-turn",
      provider: "openrouter",
      model: "unknown",
      requestedModel: "stealth/space-bunny-alpha",
      routeMode: "OPENROUTER",
      routeReason: "explicit user selection",
      generationSource: "none",
      generationStatus: "failed",
      invocationStarted: true,
      errorType: "ModelCallFailure",
      failureReason: "Original lower-level provider cause unavailable.",
      attemptLatencyMs: 10827,
      retrievalMode: "HYBRID",
      lexicalHits: 2,
      vectorHits: 3,
      fusedHits: 4,
      selectedChunkCount: 1,
      sourceFileCount: 1,
    }
  : {
      callId: "synthetic-success-call",
      turnId: "dev018-fixture-turn",
      provider: "openrouter",
      model: "stealth/space-bunny-alpha",
      requestedModel: "stealth/space-bunny-alpha",
      routeMode: "OPENROUTER",
      routeReason: "explicit user selection",
      inputTokens: 92,
      outputTokens: 38,
      totalTokens: 130,
      latencyMs: 684,
      generationSource: "provider",
      generationStatus: "completed",
    };

function App() {
  return (
    <main className="mx-auto min-h-screen max-w-5xl space-y-6 bg-canvas p-10 text-ink">
      <header className="space-y-2 border-b border-linesubtle pb-5">
        <p className="text-meta font-semibold uppercase tracking-[0.14em] text-accent">
          DEV-018 · Synthetic UI fixture · {failed ? "Failure telemetry" : "Fake server plumbing only"}
        </p>
        {!failed ? <p className="inline-flex rounded border border-accent/40 bg-accent/10 px-2 py-1 text-meta font-semibold">PLUMBING ONLY — fake server</p> : null}
        <h1 className="text-heading font-semibold">Open Marketing OS</h1>
        <p className="max-w-2xl text-bodysm text-inksecondary">
          {failed
            ? "The provider attempt failed. Retrieved evidence remains visible as source material and is not presented as a generated answer."
            : "Synthetic Arabic answer used only to verify provider transport, citation display, and runtime telemetry."}
        </p>
      </header>

      <section className="max-w-3xl space-y-4 rounded-lg border border-linesubtle bg-elevated p-6">
        <div className="rounded-md bg-raised p-4 text-bodysm" dir="rtl">
          إيه اللي ممكن نعمله عشان عدد أكبر من العملاء يكملوا عملية الشراء؟
        </div>
        <div className={`rounded-md p-4 text-bodysm ${failed ? "border border-danger/30 bg-danger/5" : "bg-raised"}`} dir="rtl">
          {failed
            ? "تعذر إنشاء إجابة لهذا الدور."
            : "بناءً على معرفة المشروع، يمكن تقليل خطوات الدفع غير الضرورية وتحسين إكمال نموذج الدفع على الموبايل لتقليل ترك السلة."}
        </div>
        <div aria-label="Evidence citations" className="rounded border border-linesubtle bg-raised p-3 text-meta text-inksecondary">
          <p className="font-semibold">{failed ? "Evidence citations · retrieved, not generated" : "Synthetic evidence citation"}</p>
          <p className="mt-1 font-mono">synthetic-business-knowledge.txt · Project scoped · Chunk c000 · HYBRID</p>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-linesubtle bg-elevated p-6">
        <h2 className="text-bodysm font-semibold">Turn runtime</h2>
        <ModelBadge calls={[call]} live={false} />
        {!failed ? <p className="text-meta text-inkmuted">Provider answer, citation, and usage values are synthetic fixture data.</p> : null}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
