"use client";

import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { cn, scoreColor } from "@/lib/utils";
import type { EvaluationResponse } from "@/lib/types";

interface RiskResultsProps {
  result: EvaluationResponse;
}

export function RiskResults({ result }: RiskResultsProps) {
  const { assessment, model_used, evaluation_timestamp, alert_dispatched } =
    result;
  const {
    raw_narrative_score: rawScore,
    sovereign_risk_score: score,
    primary_threat_vector,
    audit_findings,
    impact_assessment,
    grounding_strength: grounding,
    grounding_note: groundingNote,
    sources,
    action_disposition: disposition,
  } = assessment;

  const sc = scoreColor(score);
  const ts = evaluation_timestamp.slice(0, 19).replace("T", " ");
  const pct = Math.min(score / 10, 1) * 100;

  return (
    <div className="space-y-4">
      {/* Autonomy decision — confidence-gated action */}
      <DecisionBanner disposition={disposition} dispatched={alert_dispatched} />

      {/* Grounding strength */}
      {grounding && <GroundingBanner strength={grounding} note={groundingNote} />}

      {/* Audit correction delta */}
      {rawScore != null && (
        <ScoreDelta raw={rawScore} adjusted={score} />
      )}

      {/* Score + Threat */}
      <div className="grid grid-cols-3 gap-4">
        {/* Score */}
        <div className="bg-[#111111] border border-[#1e1e1e] rounded-xl p-5">
          <p className="text-[0.62rem] text-neutral-600 uppercase tracking-widest mb-3 font-mono">
            Sovereign Risk Score
          </p>
          <p className={cn("text-5xl font-bold tabular-nums leading-none", sc.text)}>
            {score.toFixed(1)}
          </p>
          <p className={cn("text-[0.65rem] font-bold uppercase tracking-wider mt-2 font-mono", sc.text)}>
            / 10 · {sc.label}
          </p>
        </div>

        {/* Threat vector */}
        <div className="col-span-2 bg-[#111111] border border-[#1e1e1e] rounded-xl p-5 flex flex-col justify-between">
          <div>
            <p className="text-[0.62rem] text-neutral-600 uppercase tracking-widest mb-3 font-mono">
              Primary Threat Vector
            </p>
            <p className="text-neutral-100 font-semibold text-lg leading-snug">
              {renderInline(primary_threat_vector)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2 mt-4">
            <Tag>{model_used}</Tag>
            <Tag>{ts} UTC</Tag>
            {alert_dispatched && (
              <Tag variant="alert">Alert dispatched</Tag>
            )}
          </div>
        </div>
      </div>

      {/* Risk bar */}
      <div>
        <div className="h-1.5 bg-[#1a1a1a] rounded-full overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all duration-700", sc.bar)}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-neutral-700 text-[0.65rem] mt-1.5 font-mono">
          Risk level: {sc.label} ({score.toFixed(1)} / 10)
        </p>
      </div>

      {/* Collapsible report sections */}
      <Accordion title="Fact Auditor Report" defaultOpen>
        <MarkdownBody text={audit_findings} />
      </Accordion>

      <Accordion title="Macro Impact Assessment" defaultOpen>
        <MarkdownBody text={impact_assessment} />
      </Accordion>

      {sources && sources.length > 0 && (
        <Accordion title={`Grounding Sources (${sources.length})`}>
          <ul className="space-y-1.5">
            {sources.map((s, i) => (
              <li key={i} className="flex gap-2.5 text-neutral-400 text-[0.8rem] leading-snug">
                <span className="text-neutral-700 font-mono select-none flex-shrink-0">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </Accordion>
      )}

      <Accordion title="Response Metadata">
        <pre className="text-neutral-500 text-xs bg-[#0a0a0a] rounded-lg p-4 overflow-auto leading-relaxed">
          {JSON.stringify({ model_used, evaluation_timestamp, alert_dispatched }, null, 2)}
        </pre>
      </Accordion>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

const DISPOSITION_STYLE: Record<
  string,
  { box: string; text: string; dot: string; label: string; detail: string }
> = {
  AUTO_ESCALATE: {
    box: "bg-red-950/40 border-red-900/60",
    text: "text-red-300",
    dot: "bg-red-400",
    label: "Auto-escalated to trading desk",
    detail: "High risk with strong evidence — the system dispatched the alert autonomously.",
  },
  ESCALATE_FLAGGED: {
    box: "bg-orange-950/40 border-orange-900/60",
    text: "text-orange-300",
    dot: "bg-orange-400",
    label: "Escalated — flagged low confidence",
    detail: "High risk but only partial grounding — escalated with a confidence caveat for review.",
  },
  STANDARD_QUEUE: {
    box: "bg-[#111111] border-[#262626]",
    text: "text-neutral-300",
    dot: "bg-blue-400",
    label: "Standard review queue",
    detail: "Routine risk profile — no immediate action required.",
  },
  AUTO_CLEAR: {
    box: "bg-emerald-950/30 border-emerald-900/40",
    text: "text-emerald-300",
    dot: "bg-emerald-400",
    label: "Auto-cleared",
    detail: "Low risk with strong evidence — cleared autonomously, no escalation.",
  },
  HUMAN_REVIEW: {
    box: "bg-amber-950/30 border-amber-900/50",
    text: "text-amber-300",
    dot: "bg-amber-400",
    label: "Routed for human review",
    detail: "Grounding is insufficient for an autonomous decision — deferred to a human analyst.",
  },
};

function DecisionBanner({
  disposition,
  dispatched,
}: {
  disposition: string;
  dispatched: boolean;
}) {
  const s = DISPOSITION_STYLE[disposition] ?? DISPOSITION_STYLE.STANDARD_QUEUE;
  return (
    <div className={cn("rounded-xl border px-5 py-3.5", s.box)}>
      <div className="flex items-center gap-2">
        <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", s.dot)} />
        <span className={cn("text-[0.7rem] uppercase tracking-widest font-mono font-semibold", s.text)}>
          {s.label}
        </span>
        <span className="ml-auto text-[0.55rem] uppercase tracking-widest font-mono text-neutral-600">
          {dispatched ? "alert dispatched" : "no auto-action"}
        </span>
      </div>
      <p className="text-neutral-400 text-[0.8rem] leading-relaxed mt-1.5">{s.detail}</p>
    </div>
  );
}

const GROUNDING_STYLE = {
  STRONG: {
    dot: "bg-emerald-400",
    text: "text-emerald-300",
    box: "bg-emerald-950/30 border-emerald-900/40",
    label: "Strong grounding",
  },
  PARTIAL: {
    dot: "bg-amber-400",
    text: "text-amber-300",
    box: "bg-amber-950/30 border-amber-900/40",
    label: "Partial grounding",
  },
  LIMITED: {
    dot: "bg-neutral-500",
    text: "text-neutral-400",
    box: "bg-[#141414] border-[#262626]",
    label: "Limited grounding — low confidence",
  },
} as const;

function GroundingBanner({
  strength,
  note,
}: {
  strength: "STRONG" | "PARTIAL" | "LIMITED";
  note: string;
}) {
  const s = GROUNDING_STYLE[strength] ?? GROUNDING_STYLE.LIMITED;
  return (
    <div className={cn("rounded-xl border px-5 py-3.5", s.box)}>
      <div className="flex items-center gap-2">
        <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", s.dot)} />
        <span className={cn("text-[0.62rem] uppercase tracking-widest font-mono font-semibold", s.text)}>
          {s.label}
        </span>
      </div>
      {note && <p className="text-neutral-400 text-[0.8rem] leading-relaxed mt-1.5">{note}</p>}
    </div>
  );
}

function ScoreDelta({ raw, adjusted }: { raw: number; adjusted: number }) {
  const delta = adjusted - raw;
  const unchanged = Math.abs(delta) < 0.1;
  const deltaLabel = unchanged
    ? "No change"
    : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}`;
  const deltaColor = unchanged
    ? "text-neutral-500"
    : delta < 0
    ? "text-emerald-400"
    : "text-orange-400";

  return (
    <div className="grid grid-cols-3 gap-px bg-[#1e1e1e] rounded-xl overflow-hidden text-center">
      <div className="bg-[#111111] px-4 py-3">
        <p className="text-[0.6rem] text-neutral-600 uppercase tracking-widest font-mono mb-1">
          Raw Narrative
        </p>
        <p className="text-2xl font-bold tabular-nums text-neutral-400">
          {raw.toFixed(1)}
        </p>
        <p className="text-[0.6rem] text-neutral-700 font-mono mt-0.5">pre-audit</p>
      </div>
      <div className="bg-[#111111] px-4 py-3 flex flex-col items-center justify-center">
        <p className="text-[0.6rem] text-neutral-600 uppercase tracking-widest font-mono mb-1">
          Audit Correction
        </p>
        <p className={cn("text-2xl font-bold tabular-nums", deltaColor)}>
          {deltaLabel}
        </p>
        <p className="text-[0.6rem] text-neutral-700 font-mono mt-0.5">
          {unchanged ? "narrative confirmed" : delta < 0 ? "score reduced" : "score raised"}
        </p>
      </div>
      <div className="bg-[#111111] px-4 py-3">
        <p className="text-[0.6rem] text-neutral-600 uppercase tracking-widest font-mono mb-1">
          Adjusted Score
        </p>
        <p className="text-2xl font-bold tabular-nums text-neutral-100">
          {adjusted.toFixed(1)}
        </p>
        <p className="text-[0.6rem] text-neutral-700 font-mono mt-0.5">final</p>
      </div>
    </div>
  );
}

function Tag({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: "default" | "alert";
}) {
  return (
    <span
      className={cn(
        "text-[0.62rem] px-2.5 py-0.5 rounded-full border font-mono",
        variant === "alert"
          ? "bg-red-950/40 border-red-900/60 text-red-400"
          : "bg-[#1a1a1a] border-[#2a2a2a] text-neutral-600"
      )}
    >
      {children}
    </span>
  );
}

function Accordion({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="bg-[#111111] border border-[#1e1e1e] rounded-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 text-neutral-500 hover:text-neutral-300 transition-colors text-sm text-left"
      >
        <span>{title}</span>
        <ChevronDownIcon
          className={cn("w-4 h-4 transition-transform flex-shrink-0 ml-4", open && "rotate-180")}
        />
      </button>
      {open && <div className="px-5 pb-5">{children}</div>}
    </div>
  );
}

// Render inline **bold** spans without pulling in a markdown dependency.
function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="text-neutral-100 font-semibold">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

function MarkdownBody({ text }: { text: string }) {
  if (!text.trim()) {
    return <p className="text-neutral-600 text-sm italic">No content returned.</p>;
  }
  return (
    <div className="text-neutral-300 text-sm leading-relaxed space-y-1.5">
      {text.split("\n").map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} className="h-1.5" />;
        const bullet = trimmed.match(/^[-*]\s+(.*)$/);
        if (bullet) {
          return (
            <div key={i} className="flex gap-2">
              <span className="text-neutral-600 select-none flex-shrink-0">•</span>
              <span>{renderInline(bullet[1])}</span>
            </div>
          );
        }
        return <p key={i}>{renderInline(line)}</p>;
      })}
    </div>
  );
}