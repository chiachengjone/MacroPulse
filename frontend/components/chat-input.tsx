"use client";

import { useRef, useCallback, useEffect, useState } from "react";
import { ArrowUpIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const PRESETS = [
  {
    label: "Argentina Default",
    narrative:
      "Argentina has suspended all external debt payments following a breakdown in IMF negotiations. The Argentine peso has collapsed 45% against the USD in 72 hours, triggering emergency capital controls and a freeze on USD-denominated bank deposits. The central bank's FX reserves have fallen below $5 billion — an all-time low. Contagion fears are spreading to other highly indebted EM sovereigns including Turkey, Pakistan, and Egypt, with their sovereign CDS spreads widening by 180-320bps.",
    context: "Sovereign default, EM credit contagion, FX crisis, capital controls",
  },
  {
    label: "Turkey FX Crisis",
    narrative:
      "The Turkish lira has depreciated 32% year-to-date amid persistent current account deficits and unorthodox monetary policy. The central bank has been selling reserves to defend the currency, with net reserves now negative when swaps are excluded. Inflation is running at 65% annually, eroding consumer purchasing power. Foreign investors are aggressively reducing exposure to Turkish sovereign bonds.",
    context: "FX volatility, inflation, current account deficit, central bank credibility",
  },
  {
    label: "EM Contagion",
    narrative:
      "Rising US Treasury yields and a strengthening dollar are triggering capital outflows from frontier markets. Pakistan's IMF programme is at risk of derailment after missed fiscal targets, Ghana has entered debt restructuring talks, and Sri Lanka's post-default recovery is stalling. Cross-country contagion is accelerating as investors reduce broad EM exposure and sovereign CDS spreads widen across the board.",
    context: "EM contagion, capital flows, IMF programmes, frontier market debt",
  },
  {
    label: "Singapore Banking Stress",
    narrative:
      "Singapore's financial system is flashing warning signs as private residential property prices reportedly crater under the highest interest rates in two decades. The three domestic banks — DBS, OCBC, and UOB — are said to be carrying dangerous exposure to distressed Chinese property developers and souring regional commercial real estate, with non-performing loans surging across the sector. The Singapore dollar has slid against the US dollar, and speculation is mounting that MAS will be forced to abandon its exchange-rate-centred policy framework. Analysts warn of accelerating capital flight as the city-state's safe-haven status erodes.",
    context: "Banking sector stability, property market, SGD / MAS exchange-rate policy, regional contagion",
  },
];

interface ChatInputProps {
  onSubmit: (narrative: string, context: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSubmit, disabled = false }: ChatInputProps) {
  const [narrative, setNarrative] = useState("");
  const [context, setContext] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "120px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 120), 280)}px`;
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [narrative, adjustHeight]);

  const handleSubmit = () => {
    if (!narrative.trim() || disabled) return;
    onSubmit(narrative.trim(), context.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
  };

  return (
    <div className="w-full space-y-3">
      {/* Preset pills */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[0.68rem] text-neutral-600 mr-1">Try an example:</span>
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => {
              setNarrative(p.narrative);
              setContext(p.context);
            }}
            className="px-4 py-1.5 text-xs text-neutral-500 bg-[#111111] border border-[#242424] rounded-full hover:border-[#3a3a3a] hover:text-neutral-300 transition-colors"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Main input card */}
      <div className="relative bg-[#111111] border border-[#242424] rounded-2xl focus-within:border-[#3a3a3a] transition-colors">
        {/* Narrative */}
        <textarea
          ref={textareaRef}
          value={narrative}
          onChange={(e) => {
            setNarrative(e.target.value);
            adjustHeight();
          }}
          onKeyDown={handleKeyDown}
          placeholder="Describe a macro or sovereign-risk scenario to analyze — paste a news headline, a financial report excerpt, or describe an event in your own words (e.g. a currency crash, debt default, or policy shock). Add optional context keywords below, then press the arrow or ⌘/Ctrl+Enter to run the two-agent analysis. Or pick an example above to get started."
          disabled={disabled}
          rows={1}
          className={cn(
            "w-full bg-transparent text-neutral-100 text-sm",
            "px-5 pt-4 pb-2 outline-none resize-none",
            "placeholder:text-neutral-600",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
          style={{ minHeight: 120, maxHeight: 280, overflow: "hidden" }}
        />

        {/* Bottom bar */}
        <div className="flex items-center gap-3 px-4 pb-3.5 pt-1 border-t border-[#1a1a1a]">
          {/* Context */}
          <input
            type="text"
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="Context / keywords  (e.g. sovereign default, EM credit)"
            disabled={disabled}
            className="flex-1 bg-transparent text-neutral-500 text-xs outline-none placeholder:text-neutral-700 disabled:opacity-50"
          />

          {/* Submit */}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!narrative.trim() || disabled}
            className={cn(
              "p-2 rounded-lg transition-all",
              narrative.trim() && !disabled
                ? "bg-white text-black hover:bg-neutral-200 cursor-pointer"
                : "bg-[#1a1a1a] text-neutral-700 cursor-not-allowed"
            )}
            aria-label="Analyse"
          >
            <ArrowUpIcon className="w-4 h-4" />
          </button>
        </div>
      </div>

      <p className="text-[0.68rem] text-neutral-700 text-right">
        Cmd+Enter to submit
      </p>
    </div>
  );
}