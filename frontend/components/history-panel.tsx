"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { API_URL } from "@/lib/use-analysis";
import type { AuditRecord } from "@/lib/types";

// Compact disposition chips for the audit-trail rows
const DISP_CHIP: Record<string, string> = {
  AUTO_ESCALATE: "bg-red-950/50 border-red-900/60 text-red-400",
  ESCALATE_FLAGGED: "bg-orange-950/50 border-orange-900/60 text-orange-400",
  STANDARD_QUEUE: "bg-[#1a1a1a] border-[#2a2a2a] text-neutral-500",
  AUTO_CLEAR: "bg-emerald-950/40 border-emerald-900/50 text-emerald-400",
  HUMAN_REVIEW: "bg-amber-950/40 border-amber-900/50 text-amber-400",
};
const DISP_LABEL: Record<string, string> = {
  AUTO_ESCALATE: "Escalated",
  ESCALATE_FLAGGED: "Escalated*",
  STANDARD_QUEUE: "Queued",
  AUTO_CLEAR: "Cleared",
  HUMAN_REVIEW: "Human review",
};

function scoreText(s: number): string {
  return s >= 7.5 ? "text-red-400" : s >= 5 ? "text-amber-400" : "text-green-400";
}

export function HistoryPanel() {
  const [records, setRecords] = useState<AuditRecord[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/history?size=8`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        if (!cancelled) setRecords((d.assessments ?? []) as AuditRecord[]);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Stay invisible until there's something worth showing
  if (error || records === null || records.length === 0) return null;

  return (
    <div className="mt-10">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[0.65rem] text-neutral-700 uppercase tracking-widest font-mono">
          Recent Assessments
        </p>
        <span className="text-[0.55rem] text-neutral-700 font-mono">
          Elasticsearch audit trail
        </span>
      </div>
      <div className="space-y-1.5">
        {records.map((r, i) => (
          <div
            key={i}
            className="flex items-center gap-3 bg-[#0f0f0f] border border-[#1a1a1a] rounded-lg px-4 py-2.5"
          >
            <span
              className={cn(
                "text-sm font-bold tabular-nums w-8 text-center flex-shrink-0",
                scoreText(r.sovereign_risk_score)
              )}
            >
              {r.sovereign_risk_score.toFixed(1)}
            </span>
            <span className="flex-1 text-neutral-400 text-xs truncate">
              {r.narrative}
            </span>
            <span
              className={cn(
                "text-[0.55rem] px-2 py-0.5 rounded-full border font-mono whitespace-nowrap flex-shrink-0",
                DISP_CHIP[r.action_disposition] ?? DISP_CHIP.STANDARD_QUEUE
              )}
            >
              {DISP_LABEL[r.action_disposition] ?? r.action_disposition}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
