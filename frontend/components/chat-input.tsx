"use client";

import { useRef, useCallback, useEffect, useState } from "react";
import { ArrowUpIcon, LinkIcon, XIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "https://macropulse-270431042772.us-central1.run.app";

// Real recent news excerpts (June 2026)
const PRESETS = [
  {
    label: "Turkey FX Stress",
    narrative:
      "Turkey's central bank net FX reserves have collapsed to roughly $24 billion after the CBRT spent more than $38 billion intervening to defend the lira since early 2025. The USD/TRY rate has surged past 43, while five-year sovereign CDS spreads have widened beyond 300 basis points — a nine-month high. After five consecutive rate cuts, the CBRT held its benchmark at 37% in March 2026, but analysts at ING warn that services inflation and wage pressure could push headline CPI back above 30% by year-end. The current account deficit is projected at $45 billion for the full year, and net non-resident capital inflows have stalled.",
    context: "FX volatility, central bank reserves, current account deficit, sovereign CDS, EM credit",
  },
  {
    label: "Argentina — Milei Rebound",
    narrative:
      "Argentina's sovereign risk premium has fallen to a seven-year low following President Milei's larger-than-expected midterm election victory and the completion of a $20 billion IMF program — the largest the Fund has ever extended. Annual CPI decelerated from a peak above 200% to 31.8% by November 2025, and GDP growth of 4.5% in 2025 is set to moderate to 3.5% in 2026 per IMF projections. Morgan Stanley analysts caution, however, that a credible reserve-rebuilding plan requires the peso to weaken a further 10–15%, and Argentina still owes the IMF roughly $43 billion — the institution's single largest outstanding exposure. Debt service obligations exceed $20 billion for 2026.",
    context: "Sovereign debt, IMF programme, peso exchange rate, disinflation, EM recovery",
  },
  {
    label: "Pakistan Debt Trap",
    narrative:
      "Pakistan's debt-servicing costs now consume 89% of net federal revenues, leaving the government with almost no fiscal space outside of IMF-mandated spending cuts. Inflation plummeted from a 38% peak in May 2023 to a fleeting 0.3% in April 2025 before reversing sharply to 11.7% by May 2026, illustrating the structural fragility of the stabilisation. Pakistan owes the IMF approximately $10.5 billion — the fourth-largest outstanding balance globally. Critics warn that repeated Fund programmes have become a 'comfort trap', suppressing the structural reforms needed to widen the tax base and reduce import dependence. External financing needs remain acute, with the current account running a deficit and reserves providing less than two months of import cover.",
    context: "Sovereign debt, IMF dependency, fiscal deficit, inflation volatility, EM frontier",
  },
  {
    label: "Egypt Suez Pressure",
    narrative:
      "Egypt's external financing position has deteriorated sharply as Suez Canal revenues slumped following the rerouting of commercial shipping away from the Red Sea corridor amid ongoing regional security tensions. The Egyptian pound has come under renewed pressure, with the central bank's FX reserves tightening and the country's outstanding IMF credit standing at approximately $10.7 billion. Inflation remains elevated after the post-pandemic commodity surge, and sovereign spread widening has accelerated. A controversial 'financial engineering' proposal to lease Suez Canal infrastructure to Gulf sovereign wealth funds in exchange for debt relief has emerged in policy circles but remains unconfirmed, raising concerns about asset stripping and long-term revenue loss.",
    context: "Suez Canal, FX reserves, sovereign spreads, IMF programme, geopolitical risk",
  },
];

interface ChatInputProps {
  onSubmit: (narrative: string, context: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSubmit, disabled = false }: ChatInputProps) {
  const [narrative, setNarrative] = useState("");
  const [context, setContext] = useState("");
  const [urlMode, setUrlMode] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [urlFetching, setUrlFetching] = useState(false);
  const [urlError, setUrlError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "120px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 120), 280)}px`;
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [narrative, adjustHeight]);

  useEffect(() => {
    if (urlMode) urlInputRef.current?.focus();
  }, [urlMode]);

  const handleSubmit = () => {
    if (!narrative.trim() || disabled) return;
    onSubmit(narrative.trim(), context.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
  };

  const fetchArticle = async (url: string) => {
    if (!url.trim()) return;
    setUrlFetching(true);
    setUrlError("");
    try {
      const res = await fetch(`${API_URL}/api/v1/fetch-article`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json();
      setNarrative(data.summary);
      setUrlMode(false);
      setUrlInput("");
      setTimeout(() => textareaRef.current?.focus(), 50);
    } catch (e: unknown) {
      setUrlError(e instanceof Error ? e.message : "Failed to fetch article");
    } finally {
      setUrlFetching(false);
    }
  };

  const handleUrlKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") fetchArticle(urlInput);
    if (e.key === "Escape") {
      setUrlMode(false);
      setUrlError("");
    }
  };

  return (
    <div className="w-full space-y-3">
      {/* Top bar: presets + URL toggle */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[0.68rem] text-neutral-600 mr-1">Try an example:</span>
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => {
              setNarrative(p.narrative);
              setContext(p.context);
              setUrlMode(false);
            }}
            className="px-4 py-1.5 text-xs text-neutral-500 bg-[#111111] border border-[#242424] rounded-full hover:border-[#3a3a3a] hover:text-neutral-300 transition-colors"
          >
            {p.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            setUrlMode((v) => !v);
            setUrlError("");
          }}
          className={cn(
            "ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full border transition-colors",
            urlMode
              ? "bg-[#1a1a1a] border-[#3a3a3a] text-neutral-300"
              : "bg-[#111111] border-[#242424] text-neutral-600 hover:border-[#3a3a3a] hover:text-neutral-400"
          )}
        >
          <LinkIcon className="w-3 h-3" />
          Paste URL
        </button>
      </div>

      {/* URL fetch bar */}
      {urlMode && (
        <div className="flex items-center gap-2 bg-[#111111] border border-[#2a2a2a] rounded-xl px-4 py-2.5">
          <LinkIcon className="w-3.5 h-3.5 text-neutral-600 shrink-0" />
          <input
            ref={urlInputRef}
            type="url"
            value={urlInput}
            onChange={(e) => {
              setUrlInput(e.target.value);
              setUrlError("");
            }}
            onKeyDown={handleUrlKeyDown}
            placeholder="https://www.ft.com/… or any public news article"
            disabled={urlFetching}
            className="flex-1 bg-transparent text-neutral-300 text-sm outline-none placeholder:text-neutral-700 disabled:opacity-50"
          />
          {urlFetching ? (
            <span className="text-[0.68rem] text-neutral-600 animate-pulse">Fetching & summarising…</span>
          ) : (
            <button
              type="button"
              onClick={() => fetchArticle(urlInput)}
              disabled={!urlInput.trim()}
              className="text-xs text-neutral-500 hover:text-neutral-200 transition-colors disabled:opacity-30"
            >
              Extract
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setUrlMode(false);
              setUrlError("");
            }}
            className="text-neutral-700 hover:text-neutral-400 transition-colors"
          >
            <XIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {urlError && (
        <p className="text-[0.7rem] text-red-400 px-1">
          {urlError} — try pasting the article text directly instead.
        </p>
      )}

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
          placeholder="Paste a news article excerpt, a financial report summary, or describe a sovereign-risk scenario — or fetch one from a URL above. Add optional context keywords below, then press ⌘/Ctrl+Enter."
          disabled={disabled}
          rows={1}
          className={cn(
            "w-full bg-transparent text-neutral-100 text-sm",
            "px-5 pt-4 pb-2 outline-none resize-none",
            "placeholder:text-neutral-600",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
          style={{ minHeight: 120, maxHeight: 280, overflowY: "auto" }}
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
