import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import ChatMessage from "../src/components/chat/ChatMessage";
import TurnRuntimePanel from "../src/components/runtime/TurnRuntimePanel";

const historicalAnswer =
  "NJM builds conversion-focused marketing websites and lifecycle programs.\n\n" +
  "نجم تقدم مواقع تسويقية مركزة على التحويل.\n\n" +
  "Source: [Synthetic Business Knowledge](http://127.0.0.1:8765/evidence/synthetic-business-knowledge.txt)";

interface FullTurnReplayAnswer {
  assistant_answer?: unknown;
  citation_source_file_ids?: unknown;
}

function ReplayPage() {
  const fullTurn = new URLSearchParams(window.location.search).get("mode") === "full-turn";
  const [answer, setAnswer] = useState(fullTurn ? "" : historicalAnswer);
  const [citationSourceFileIds, setCitationSourceFileIds] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    if (!fullTurn) return;
    let cancelled = false;
    fetch("/dev024/full-turn-answer.json", { headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) throw new Error(`answer replay returned ${response.status}`);
        return response.json() as Promise<FullTurnReplayAnswer>;
      })
      .then((payload) => {
        if (typeof payload.assistant_answer !== "string") {
          throw new Error("Full-turn replay has no exact assistant_answer string");
        }
        if (!Array.isArray(payload.citation_source_file_ids) ||
            payload.citation_source_file_ids.some((id) => typeof id !== "string" || !id)) {
          throw new Error("Full-turn replay has no persisted citation source IDs");
        }
        if (!cancelled) setAnswer(payload.assistant_answer);
        if (!cancelled) setCitationSourceFileIds(payload.citation_source_file_ids);
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "Replay answer could not be loaded");
      });
    return () => { cancelled = true; };
  }, [fullTurn]);

  const turnId = fullTurn ? "dev024-offline-full-turn" : "dev024-sanitized-replay";
  return (
    <BrowserRouter>
      <main className="page">
        <header><span>OFFLINE ACCEPTANCE REPLAY</span><span>DEV-024</span></header>
        <section className="conversation" aria-label="Synthetic replay conversation">
          <p className="label">{fullTurn ? "ASSISTANT ANSWER · OFFLINE FULL-TURN REPLAY" : "ASSISTANT ANSWER · HISTORICAL TELEMETRY REPLAY"}</p>
          {loadError ? <p role="alert">{loadError}</p> : null}
          {answer || loadError ? <ChatMessage role="assistant" bodyMd={answer} turnId={turnId} /> : <p>Loading exact offline answer…</p>}
          {fullTurn && citationSourceFileIds.length > 0 ? (
            <aside data-testid="citation-list" aria-label="Persisted retrieval citations">
              {citationSourceFileIds.map((sourceId) => (
                <p key={sourceId}>
                  Source: <a data-testid="citation" href={`#citation-${encodeURIComponent(sourceId)}`}>{sourceId}</a>
                </p>
              ))}
            </aside>
          ) : null}
          {fullTurn && citationSourceFileIds.map((sourceId) => (
            <span key={sourceId} id={`citation-${encodeURIComponent(sourceId)}`} data-testid="citation-target" hidden>
              Persisted synthetic retrieval source ID: {sourceId}
            </span>
          ))}
          <TurnRuntimePanel turnId={turnId} />
        </section>
        <footer>Loopback replay only · No provider traffic</footer>
      </main>
    </BrowserRouter>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Replay page root element is missing");
createRoot(root).render(<ReplayPage />);
