"use client";

import { cn } from "@/lib/utils";
import { useAnalysis } from "@/lib/use-analysis";
import { ChatInput } from "@/components/chat-input";
import { PipelineFeed } from "@/components/pipeline-feed";
import { RiskResults } from "@/components/risk-results";
import { HistoryPanel } from "@/components/history-panel";

export default function Home() {
  const { state, steps, result, error, analyse, reset } = useAnalysis();

  const isRunning = state === "running";
  const isDone = state === "done";
  const isError = state === "error";
  const hasActivity = isRunning || isDone || isError;

  return (
    <main className="min-h-screen bg-[#0a0a0a] text-neutral-100">
      <div className="max-w-3xl mx-auto px-4 py-12">
        {/* Header */}
        <div
          className={cn(
            "text-center transition-all duration-500",
            hasActivity ? "mb-8" : "mt-[12vh] mb-10"
          )}
        >
          <h1
            className={cn(
              "font-bold tracking-tight text-white transition-all duration-500",
              hasActivity ? "text-3xl" : "text-5xl"
            )}
          >
            MacroPulse
          </h1>
          {!hasActivity && (
            <p className="text-neutral-500 text-sm mt-2.5 leading-relaxed">
              Multi-agent sovereign risk intelligence
              <br />
              <span className="text-neutral-700">
                Gemini 2.5 Flash · Elasticsearch Serverless · Two-agent correction loop
              </span>
            </p>
          )}
        </div>

        {/* Chat input */}
        <ChatInput onSubmit={analyse} disabled={isRunning} />

        {/* Recent assessments from the Elasticsearch audit trail (idle only) */}
        {!hasActivity && <HistoryPanel />}

        {/* Error */}
        {isError && error && (
          <div className="mt-4 bg-red-950/40 border border-red-900/50 rounded-xl px-5 py-4">
            <p className="text-red-300 text-sm">
              <strong>Error:</strong> {error}
            </p>
            <button
              type="button"
              onClick={reset}
              className="mt-2 text-red-400 hover:text-red-200 text-xs underline transition-colors"
            >
              Clear and try again
            </button>
          </div>
        )}

        {/* Live pipeline feed */}
        {(isRunning || isDone) && (
          <div className="mt-6">
            <p className="text-[0.65rem] text-neutral-700 uppercase tracking-widest font-mono mb-2">
              Pipeline
            </p>
            <PipelineFeed steps={steps} running={isRunning} />
          </div>
        )}

        {/* Results */}
        {isDone && result && (
          <div className="mt-8 pb-16">
            <p className="text-[0.65rem] text-neutral-700 uppercase tracking-widest font-mono mb-3">
              Assessment
            </p>
            <RiskResults result={result} />
          </div>
        )}
      </div>
    </main>
  );
}