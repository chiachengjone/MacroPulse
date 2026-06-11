"use client";

import { useState, useCallback, useRef } from "react";
import { scoreLabel } from "./utils";
import { addSessionAssessment } from "./session-history";
import type { PipelineStep, EvaluationResponse, AppState } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://macropulse-270431042772.us-central1.run.app";

const DISPOSITION_FEED_LABEL: Record<string, string> = {
  AUTO_ESCALATE: "Auto-escalated to desk",
  ESCALATE_FLAGGED: "Escalated — low confidence",
  STANDARD_QUEUE: "Standard review queue",
  AUTO_CLEAR: "Auto-cleared",
  HUMAN_REVIEW: "Routed for human review",
};

export function useAnalysis() {
  const [state, setState] = useState<AppState>("idle");
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [result, setResult] = useState<EvaluationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const analyse = useCallback(async (narrative: string, context: string) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setState("running");
    setSteps([]);
    setResult(null);
    setError(null);

    const add = (step: PipelineStep) =>
      setSteps((prev) => [...prev, step]);

    try {
      const res = await fetch(`${API_URL}/api/v1/evaluate/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ narrative, context: context || null }),
        signal: ctrl.signal,
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(
          `HTTP ${res.status}${text ? `: ${text.slice(0, 200)}` : ""}`
        );
      }

      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // Normalize CRLF -> LF: sse-starlette emits "\r\n\r\n" between events,
        // which contains no "\n\n", so splitting on "\n\n" would never match.
        buf = (buf + dec.decode(value, { stream: true })).replace(/\r\n/g, "\n");

        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);

          let evtType: string | null = null;
          let evtData: Record<string, unknown> | null = null;

          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) {
              evtType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              try {
                evtData = JSON.parse(line.slice(6));
              } catch {
                // malformed JSON — skip
              }
            }
          }

          if (!evtType || !evtData) continue;

          switch (evtType) {
            case "agent1_start":
              add({ agent: "agent1", text: "Audit engine initializing…", kind: "normal" });
              break;

            case "agent1_thinking":
              add({
                agent: "agent1",
                text: String(evtData.message ?? "Analyzing narrative…"),
                kind: "normal",
              });
              break;

            case "agent1_search": {
              add({
                agent: "agent1",
                text: String(evtData.query ?? ""),
                kind: "search",
              });
              const srcs = Array.isArray(evtData.sources)
                ? (evtData.sources as string[])
                : [];
              for (const s of srcs.slice(0, 4)) {
                add({ agent: "agent1", text: `retrieved: ${s}`, kind: "source" });
              }
              break;
            }

            case "agent1_complete":
              add({
                agent: "agent1",
                text: `Audit complete — ${Number(
                  evtData.chars ?? 0
                ).toLocaleString()} chars`,
                kind: "complete",
              });
              break;

            case "agent2_start":
              add({
                agent: "crosscheck",
                text: "Forwarding audit + grounding docs → Risk Specialist…",
                kind: "normal",
              });
              add({
                agent: "agent2",
                text: String(evtData.message ?? "Synthesizing findings…"),
                kind: "normal",
              });
              break;

            case "cross_check":
              add({
                agent: "agent2",
                text: String(evtData.message ?? "Cross-checking…"),
                kind: "normal",
              });
              break;

            case "agent2_complete": {
              const score = Number(evtData.score ?? 0);
              const rawScore = evtData.raw_score != null ? Number(evtData.raw_score) : null;
              const threat = String(evtData.threat ?? "");
              if (rawScore != null && Math.abs(rawScore - score) >= 0.1) {
                const delta = score - rawScore;
                add({
                  agent: "agent2",
                  text: `Raw: ${rawScore.toFixed(1)} → Adjusted: ${score.toFixed(1)} (${delta > 0 ? "+" : ""}${delta.toFixed(1)} audit correction)`,
                  kind: "normal",
                });
              }
              add({
                agent: "agent2",
                text: `Score: ${score.toFixed(1)} / 10 — ${scoreLabel(score)}`,
                kind: "score",
                score,
              });
              if (threat) {
                add({
                  agent: "agent2",
                  text: `Threat: ${threat}`,
                  kind: "normal",
                });
              }
              const grounding = evtData.grounding_strength
                ? String(evtData.grounding_strength)
                : null;
              if (grounding) {
                add({
                  agent: "agent2",
                  text: `Grounding: ${grounding}`,
                  kind: "normal",
                });
              }
              break;
            }

            case "decision": {
              const disp = String(evtData.disposition ?? "");
              add({
                agent: "agent2",
                text: `Decision: ${DISPOSITION_FEED_LABEL[disp] ?? disp}`,
                kind: "normal",
              });
              break;
            }

            case "complete": {
              const final = evtData as unknown as EvaluationResponse;
              setResult(final);
              setState("done");
              addSessionAssessment({
                timestamp: final.evaluation_timestamp,
                narrative,
                sovereign_risk_score: final.assessment.sovereign_risk_score,
              });
              break outer;
            }

            case "error":
              throw new Error(String(evtData.message ?? "Unknown pipeline error"));
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      const msg = err instanceof Error ? err.message : "Unknown error";
      setError(msg);
      setState("error");
    }
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState("idle");
    setSteps([]);
    setResult(null);
    setError(null);
  }, []);

  return { state, steps, result, error, analyse, reset };
}