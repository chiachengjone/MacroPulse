"use client";

import { useEffect, useState, useCallback } from "react";
import { DatabaseIcon, XIcon, SparklesIcon, RefreshCwIcon, ChevronUpIcon, ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { API_URL } from "@/lib/use-analysis";

interface CountryOption {
  code: string;
  name: string;
}

interface CurateResult {
  dry_run: boolean;
  ml_healthy: boolean;
  ml_reason: string;
  bank_before: number;
  bank_after: number;
  candidates_found: number;
  candidates?: { title: string; url: string; institution: string }[];
  added: { title: string; chars?: number }[];
  add_errors: { error?: string; url?: string }[];
  removed: { title: string }[];
}

const INTERVAL_LABEL: Record<number, string> = {
  6: "6h",
  12: "12h",
  24: "Daily",
  48: "2 days",
  168: "Weekly",
};

export function DocumentBank() {
  const [open, setOpen] = useState(false);
  const [countries, setCountries] = useState<CountryOption[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [intervals, setIntervals] = useState<number[]>([6, 12, 24, 48, 168]);

  const [selCountries, setSelCountries] = useState<string[]>([]);
  const [selTopics, setSelTopics] = useState<string[]>([]);
  const [interval, setIntervalH] = useState(24);
  const [maxDocs, setMaxDocs] = useState(2000);
  const [active, setActive] = useState(true);
  const [docCount, setDocCount] = useState<number | null>(null);
  const [lastRun, setLastRun] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<"" | "preview" | "curate">("");
  const [status, setStatus] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [result, setResult] = useState<CurateResult | null>(null);
  const [showAdded, setShowAdded] = useState(true);
  const [showRemoved, setShowRemoved] = useState(true);

  // Load catalog + existing config when the card first opens
  useEffect(() => {
    if (!open || countries.length) return;
    Promise.all([
      fetch(`${API_URL}/api/v1/bank/catalog`).then((r) => r.json()),
      fetch(`${API_URL}/api/v1/bank/config`).then((r) => r.json()),
    ])
      .then(([cat, cfg]) => {
        setCountries(cat.countries ?? []);
        setTopics(cat.topics ?? []);
        setIntervals(cat.intervals ?? [6, 12, 24, 48, 168]);
        setSelCountries(cfg.focus_countries ?? []);
        setSelTopics(cfg.focus_topics ?? []);
        setIntervalH(cfg.interval_hours ?? 24);
        setMaxDocs(cfg.max_docs ?? 2000);
        setActive(cfg.is_active ?? true);
        setDocCount(cfg.current_doc_count ?? null);
        setLastRun(cfg.last_run_at ?? null);
      })
      .catch(() => setStatus({ kind: "err", msg: "Could not load curator settings." }));
  }, [open, countries.length]);

  const toggle = useCallback(
    (list: string[], set: (v: string[]) => void, value: string) => {
      set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
    },
    []
  );

  const save = async () => {
    setStatus(null);
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/bank/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interval_hours: interval,
          focus_countries: selCountries,
          focus_topics: selTopics,
          max_docs: maxDocs,
          is_active: active,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
      setDocCount(data.config.current_doc_count ?? docCount);
      setStatus({ kind: "ok", msg: "Curator settings saved." });
    } catch (e: unknown) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : "Save failed." });
    } finally {
      setSaving(false);
    }
  };

  const runCurate = async (dryRun: boolean) => {
    setStatus(null);
    setResult(null);
    setBusy(dryRun ? "preview" : "curate");
    try {
      const qs = dryRun ? "?dry_run=true" : "";
      const res = await fetch(`${API_URL}/api/v1/bank/curate${qs}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
      setResult(data as CurateResult);
      if (typeof data.bank_after === "number") setDocCount(data.bank_after);
    } catch (e: unknown) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : "Curation failed." });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="fixed top-4 left-4 z-50 w-[min(92vw,360px)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full border transition-colors backdrop-blur",
          open
            ? "bg-[#1a1a1a] border-[#3a3a3a] text-neutral-200"
            : "bg-[#111111]/90 border-[#242424] text-neutral-400 hover:border-[#3a3a3a] hover:text-neutral-200"
        )}
      >
        <DatabaseIcon className="w-3.5 h-3.5" />
        Document Bank
      </button>

      {open && (
        <div className="mt-2 bg-[#0d0d0d]/95 border border-[#242424] rounded-2xl shadow-2xl shadow-black/60 backdrop-blur p-4 max-h-[82vh] overflow-y-auto">
          <div className="flex items-center justify-between mb-3">
            <p className="text-[0.65rem] text-neutral-500 uppercase tracking-widest font-mono">
              Curator Agent
            </p>
            <div className="flex items-center gap-2">
              {docCount !== null && (
                <span className="text-[0.6rem] text-neutral-600 font-mono">{docCount} docs</span>
              )}
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-neutral-700 hover:text-neutral-400 transition-colors"
              >
                <XIcon className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Focus countries */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Focus sovereigns</label>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {countries.map((c) => {
              const on = selCountries.includes(c.code);
              return (
                <button
                  key={c.code}
                  type="button"
                  title={c.name}
                  onClick={() => toggle(selCountries, setSelCountries, c.code)}
                  className={cn(
                    "px-2.5 py-1 text-[0.68rem] rounded-full border transition-colors",
                    on
                      ? "bg-violet-950/50 border-violet-800/70 text-violet-300"
                      : "bg-[#111111] border-[#242424] text-neutral-500 hover:border-[#3a3a3a]"
                  )}
                >
                  {c.code}
                </button>
              );
            })}
          </div>

          {/* Focus topics */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Focus topics</label>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {topics.map((t) => {
              const on = selTopics.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggle(selTopics, setSelTopics, t)}
                  className={cn(
                    "px-2.5 py-1 text-[0.68rem] rounded-full border transition-colors",
                    on
                      ? "bg-violet-950/50 border-violet-800/70 text-violet-300"
                      : "bg-[#111111] border-[#242424] text-neutral-500 hover:border-[#3a3a3a]"
                  )}
                >
                  {t}
                </button>
              );
            })}
          </div>

          {/* Interval + max docs */}
          <div className="flex gap-3 mb-3">
            <div className="flex-1">
              <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Scan interval</label>
              <div className="flex gap-1">
                {intervals.map((iv) => (
                  <button
                    key={iv}
                    type="button"
                    onClick={() => setIntervalH(iv)}
                    className={cn(
                      "flex-1 px-1.5 py-1.5 text-[0.6rem] rounded-lg border transition-colors",
                      interval === iv
                        ? "bg-[#1f1f1f] border-[#3a3a3a] text-neutral-200"
                        : "bg-[#111111] border-[#242424] text-neutral-600 hover:border-[#3a3a3a]"
                    )}
                  >
                    {INTERVAL_LABEL[iv] ?? `${iv}h`}
                  </button>
                ))}
              </div>
            </div>
            <div className="w-20">
              <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Max docs</label>
              <input
                type="number"
                min={50}
                max={2000}
                value={maxDocs}
                onChange={(e) => setMaxDocs(Number(e.target.value))}
                className="w-full bg-[#111111] border border-[#242424] rounded-lg px-2 py-1.5 text-xs text-neutral-200 outline-none focus:border-[#3a3a3a]"
              />
            </div>
          </div>

          {/* Autonomous toggle */}
          <button
            type="button"
            onClick={() => setActive((v) => !v)}
            className="flex items-center justify-between w-full mb-4 px-3 py-2 rounded-lg bg-[#111111] border border-[#242424] hover:border-[#3a3a3a] transition-colors"
          >
            <span className="text-xs text-neutral-400">
              Autonomous ({INTERVAL_LABEL[interval] ?? `${interval}h`})
            </span>
            <span
              className={cn(
                "text-[0.6rem] px-2 py-0.5 rounded-full border",
                active
                  ? "border-emerald-900/50 text-emerald-400 bg-emerald-950/20"
                  : "border-[#2a2a2a] text-neutral-600"
              )}
            >
              {active ? "ON" : "OFF"}
            </span>
          </button>

          {/* Actions */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="flex-1 px-3 py-2 text-xs rounded-lg bg-[#1a1a1a] border border-[#2a2a2a] text-neutral-300 hover:border-[#3a3a3a] hover:text-white transition-colors disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => runCurate(true)}
              disabled={busy !== ""}
              title="Preview candidates without writing anything"
              className="flex items-center justify-center gap-1 px-3 py-2 text-xs rounded-lg bg-[#1a1a1a] border border-[#2a2a2a] text-neutral-300 hover:border-[#3a3a3a] hover:text-white transition-colors disabled:opacity-50"
            >
              <SparklesIcon className="w-3.5 h-3.5" />
              {busy === "preview" ? "…" : "Preview"}
            </button>
            <button
              type="button"
              onClick={() => runCurate(false)}
              disabled={busy !== ""}
              className="flex items-center justify-center gap-1 px-3 py-2 text-xs rounded-lg bg-white text-black font-medium hover:bg-neutral-200 transition-colors disabled:opacity-50"
            >
              <RefreshCwIcon className={cn("w-3.5 h-3.5", busy === "curate" && "animate-spin")} />
              {busy === "curate" ? "Curating…" : "Curate"}
            </button>
          </div>

          {lastRun && (
            <p className="mt-2 text-[0.6rem] text-neutral-700 font-mono">
              last run: {new Date(lastRun).toLocaleString()}
            </p>
          )}

          {status && (
            <p
              className={cn(
                "mt-3 text-[0.7rem] leading-relaxed",
                status.kind === "ok" ? "text-emerald-400" : "text-red-400"
              )}
            >
              {status.msg}
            </p>
          )}

          {/* Result */}
          {result && (
            <div className="mt-4 border-t border-[#1a1a1a] pt-3 space-y-3">
              <div className="flex items-center gap-2 text-[0.65rem]">
                <span
                  className={cn(
                    "px-1.5 py-0.5 rounded-full border text-[0.55rem]",
                    result.ml_healthy
                      ? "border-emerald-900/50 text-emerald-400 bg-emerald-950/20"
                      : "border-red-900/60 text-red-400 bg-red-950/30"
                  )}
                >
                  ML {result.ml_healthy ? "healthy" : "down"}
                </span>
                <span className="text-neutral-600">
                  {result.dry_run ? "preview · no writes" : `${result.bank_before} → ${result.bank_after} docs`}
                </span>
              </div>

              {/* Dry-run candidates */}
              {result.dry_run && result.candidates && (
                <div className="text-[0.65rem]">
                  <span className="text-neutral-600 uppercase tracking-wide font-mono text-[0.55rem]">
                    {result.candidates_found} candidate(s) found
                  </span>
                  <ul className="mt-1 space-y-1.5">
                    {result.candidates.map((c, i) => (
                      <li key={i} className="text-neutral-400">
                        <span className="text-violet-400">[{c.institution}]</span> {c.title}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Real run results — expandable Ingested / Deleted dropdowns */}
              {!result.dry_run && (
                <>
                  {result.added.length > 0 && (
                    <div className="text-[0.65rem]">
                      <button
                        type="button"
                        onClick={() => setShowAdded((v) => !v)}
                        className="flex items-center gap-1 text-emerald-500 uppercase tracking-wide font-mono text-[0.55rem]"
                      >
                        {showAdded ? (
                          <ChevronUpIcon className="w-3 h-3" />
                        ) : (
                          <ChevronDownIcon className="w-3 h-3" />
                        )}
                        Ingested {result.added.length}
                      </button>
                      {showAdded && (
                        <ul className="mt-1 space-y-0.5">
                          {result.added.map((a, i) => (
                            <li key={i} className="text-neutral-400 truncate">• {a.title}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {result.removed.length > 0 && (
                    <div className="text-[0.65rem]">
                      <button
                        type="button"
                        onClick={() => setShowRemoved((v) => !v)}
                        className="flex items-center gap-1 text-amber-500 uppercase tracking-wide font-mono text-[0.55rem]"
                      >
                        {showRemoved ? (
                          <ChevronUpIcon className="w-3 h-3" />
                        ) : (
                          <ChevronDownIcon className="w-3 h-3" />
                        )}
                        Deleted {result.removed.length}
                      </button>
                      {showRemoved && (
                        <ul className="mt-1 space-y-0.5">
                          {result.removed.map((r, i) => (
                            <li key={i} className="text-neutral-500 truncate">• {r.title}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {result.add_errors.length > 0 && (
                    <div className="text-[0.6rem] text-neutral-600">
                      {result.add_errors.length} skipped (validation/preflight)
                    </div>
                  )}
                  {result.added.length === 0 && result.removed.length === 0 && (
                    <p className="text-[0.65rem] text-neutral-600">
                      No changes this run.
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
