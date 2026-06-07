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
    sovereign_risk_score: score,
    primary_threat_vector,
    audit_findings,
    impact_assessment,
    requires_immediate_alert: alert,
  } = assessment;

  const sc = scoreColor(score);
  const ts = evaluation_timestamp.slice(0, 19).replace("T", " ");
  const pct = Math.min(score / 10, 1) * 100;

  return (
    <div className="space-y-4">
      {/* Alert banner */}
      {alert ? (
        <div className="bg-red-950/40 border border-red-900/50 rounded-xl px-5 py-3.5 text-red-300 text-sm font-medium">
          IMMEDIATE ESCALATION REQUIRED — Trading Desk Alert Active
        </div>
      ) : (
        <div className="bg-green-950/30 border border-green-900/40 rounded-xl px-5 py-3.5 text-green-300 text-sm font-medium">
          Standard Review Queue — No Immediate Escalation
        </div>
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

      <Accordion title="Response Metadata">
        <pre className="text-neutral-500 text-xs bg-[#0a0a0a] rounded-lg p-4 overflow-auto leading-relaxed">
          {JSON.stringify({ model_used, evaluation_timestamp, alert_dispatched }, null, 2)}
        </pre>
      </Accordion>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

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