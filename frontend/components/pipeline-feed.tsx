"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { PipelineStep, PipelineAgent } from "@/lib/types";

interface PipelineFeedProps {
  steps: PipelineStep[];
  running: boolean;
}

type AgentConfig = {
  label: string;
  dot: string;
  label_color: string;
};

const AGENT: Record<PipelineAgent, AgentConfig> = {
  agent1: {
    label: "Agent 1 — Fact Auditor",
    dot: "bg-lime-400",
    label_color: "text-lime-600",
  },
  crosscheck: {
    label: "Cross-check",
    dot: "bg-amber-400",
    label_color: "text-amber-600",
  },
  agent2: {
    label: "Agent 2 — Risk Specialist",
    dot: "bg-blue-400",
    label_color: "text-blue-600",
  },
};

export function PipelineFeed({ steps, running }: PipelineFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [steps.length]);

  // Group consecutive steps by agent
  const groups: Array<{ agent: PipelineAgent; items: PipelineStep[] }> = [];
  for (const step of steps) {
    const last = groups[groups.length - 1];
    if (last?.agent === step.agent) {
      last.items.push(step);
    } else {
      groups.push({ agent: step.agent, items: [step] });
    }
  }

  return (
    <div className="bg-[#0f0f0f] border border-[#1e1e1e] rounded-2xl overflow-hidden font-mono">
      {groups.map((group, gi) => {
        const cfg = AGENT[group.agent];
        const isActive = running && gi === groups.length - 1;

        return (
          <div
            key={gi}
            className={cn("px-5 py-4", gi > 0 && "border-t border-[#1a1a1a]")}
          >
            {/* Agent header */}
            <div className="flex items-center gap-2 mb-3">
              <span
                className={cn(
                  "w-1.5 h-1.5 rounded-full flex-shrink-0",
                  cfg.dot,
                  isActive && "animate-pulse"
                )}
              />
              <span
                className={cn(
                  "text-[0.62rem] uppercase tracking-widest",
                  cfg.label_color
                )}
              >
                {cfg.label}
              </span>
              {isActive && (
                <span className="ml-auto flex items-center gap-0.5">
                  <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:0ms]" />
                  <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:150ms]" />
                  <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:300ms]" />
                </span>
              )}
            </div>

            {/* Steps */}
            <div className="space-y-1.5 text-[0.76rem]">
              {group.items.map((step, si) => (
                <StepLine key={si} step={step} />
              ))}
            </div>
          </div>
        );
      })}

      {/* Running pulse when no steps yet */}
      {running && steps.length === 0 && (
        <div className="px-5 py-4 flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-lime-400 animate-pulse" />
          <span className="text-[0.62rem] uppercase tracking-widest text-lime-600">
            Agent 1 — Fact Auditor
          </span>
          <span className="ml-auto flex items-center gap-0.5">
            <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:0ms]" />
            <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:150ms]" />
            <span className="w-1 h-1 rounded-full bg-neutral-700 animate-bounce [animation-delay:300ms]" />
          </span>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

function StepLine({ step }: { step: PipelineStep }) {
  if (step.kind === "search") {
    return (
      <div className="flex items-start gap-2.5">
        <span className="w-1 h-1 rounded-full bg-neutral-700 flex-shrink-0 mt-[5px]" />
        <span className="text-neutral-500">
          Searching:{" "}
          <code className="text-lime-500 bg-[#1a1a1a] px-1.5 py-0.5 rounded text-[0.72rem]">
            {step.text}
          </code>
        </span>
      </div>
    );
  }

  if (step.kind === "complete") {
    return (
      <div className="flex items-start gap-2.5">
        <span className="w-1 h-1 rounded-full bg-lime-500 flex-shrink-0 mt-[5px]" />
        <span className="text-lime-400">{step.text}</span>
      </div>
    );
  }

  if (step.kind === "score") {
    const score = step.score ?? 0;
    const color =
      score >= 9
        ? "text-red-400"
        : score >= 7.5
        ? "text-orange-400"
        : score >= 5
        ? "text-amber-400"
        : "text-green-400";
    return (
      <div className="flex items-start gap-2.5">
        <span className="w-1 h-1 rounded-full bg-neutral-500 flex-shrink-0 mt-[5px]" />
        <span className={cn("font-semibold", color)}>{step.text}</span>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5">
      <span className="w-1 h-1 rounded-full bg-neutral-700 flex-shrink-0 mt-[5px]" />
      <span className="text-neutral-500">{step.text}</span>
    </div>
  );
}