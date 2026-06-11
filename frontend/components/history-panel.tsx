"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { getSessionAssessments, type SessionAssessment } from "@/lib/session-history";

function scoreText(s: number): string {
  return s >= 7.5 ? "text-red-400" : s >= 5 ? "text-amber-400" : "text-green-400";
}

export function HistoryPanel() {
  const [records, setRecords] = useState<SessionAssessment[] | null>(null);

  useEffect(() => {
    setRecords(getSessionAssessments());
  }, []);

  // Stay invisible until this session has produced something
  if (records === null || records.length === 0) return null;

  return (
    <div className="mt-10">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[0.65rem] text-neutral-700 uppercase tracking-widest font-mono">
          Recent Assessments
        </p>
        <span className="text-[0.55rem] text-neutral-700 font-mono">
          This session
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
          </div>
        ))}
      </div>
    </div>
  );
}
