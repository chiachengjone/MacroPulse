"use client";

import { useEffect, useState, useCallback } from "react";
import { BellIcon, XIcon, ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { API_URL } from "@/lib/use-analysis";

interface CountryOption {
  code: string;
  name: string;
  currency: string;
}

interface MacroIndicator {
  label: string;
  value: number;
  unit: string;
  as_of: string;
}

interface AlertRunResult {
  monitored_countries: string[];
  monitored_metrics: string[];
  live_data: {
    macro_indicators: MacroIndicator[];
  };
  report: string;
  email_sent: boolean;
  email_status: string;
}

const INTERVAL_OPTIONS = [15, 30, 60, 120, 0];
const INTERVAL_LABEL: Record<number, string> = { 15: "15m", 30: "30m", 60: "1h", 120: "2h", 0: "Never" };
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function AlertSubscription() {
  const [open, setOpen] = useState(false);
  const [countries, setCountries] = useState<CountryOption[]>([]);
  const [metrics, setMetrics] = useState<string[]>([]);

  const [email, setEmail] = useState("");
  const [selCountries, setSelCountries] = useState<string[]>([]);
  const [selMetrics, setSelMetrics] = useState<string[]>([]);
  const [interval, setIntervalMin] = useState(60);
  const [emailOn, setEmailOn] = useState(true);

  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [result, setResult] = useState<AlertRunResult | null>(null);
  const [reportOpen, setReportOpen] = useState(true);

  // Load the catalog once when the card first opens
  useEffect(() => {
    if (!open || countries.length) return;
    fetch(`${API_URL}/api/v1/alerts/catalog`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        setCountries(d.countries ?? []);
        setMetrics(d.metrics ?? []);
      })
      .catch(() => setStatus({ kind: "err", msg: "Could not load options." }));
  }, [open, countries.length]);

  const toggle = useCallback(
    (list: string[], set: (v: string[]) => void, value: string) => {
      set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
    },
    []
  );

  const save = async () => {
    setStatus(null);
    if (!EMAIL_RE.test(email.trim())) {
      setStatus({ kind: "err", msg: "Enter a valid email address." });
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/alerts/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim(),
          countries: selCountries,
          metrics: selMetrics,
          interval_minutes: interval,
          is_active: emailOn,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
      setStatus({ kind: "ok", msg: data.message ?? "Preferences saved." });
    } catch (e: unknown) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : "Save failed." });
    } finally {
      setSaving(false);
    }
  };

  const runNow = async () => {
    setStatus(null);
    if (!EMAIL_RE.test(email.trim())) {
      setStatus({ kind: "err", msg: "Enter a valid email address first." });
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/alerts/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
      setResult(data as AlertRunResult);
      setReportOpen(true);
    } catch (e: unknown) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : "Alert run failed." });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="fixed top-4 right-4 z-50 w-[min(92vw,360px)]">
      {/* Toggle */}
      <div className="flex justify-end">
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
          <BellIcon className="w-3.5 h-3.5" />
          Alert Monitor
        </button>
      </div>

      {/* Panel */}
      {open && (
        <div className="mt-2 bg-[#0d0d0d]/95 border border-[#242424] rounded-2xl shadow-2xl shadow-black/60 backdrop-blur p-4 max-h-[82vh] overflow-y-auto">
          <div className="flex items-center justify-between mb-3">
            <p className="text-[0.65rem] text-neutral-500 uppercase tracking-widest font-mono">
              Background Monitor
            </p>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-neutral-700 hover:text-neutral-400 transition-colors"
            >
              <XIcon className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Email */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@desk.com"
            className="w-full bg-[#111111] border border-[#242424] rounded-lg px-3 py-2 text-sm text-neutral-200 outline-none focus:border-[#3a3a3a] placeholder:text-neutral-700 mb-3"
          />

          {/* Countries */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Sovereigns</label>
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
                      ? "bg-blue-950/50 border-blue-800/70 text-blue-300"
                      : "bg-[#111111] border-[#242424] text-neutral-500 hover:border-[#3a3a3a]"
                  )}
                >
                  {c.code}
                </button>
              );
            })}
          </div>

          {/* Metrics */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Indicators</label>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {metrics.map((m) => {
              const on = selMetrics.includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => toggle(selMetrics, setSelMetrics, m)}
                  className={cn(
                    "px-2.5 py-1 text-[0.68rem] rounded-full border transition-colors",
                    on
                      ? "bg-blue-950/50 border-blue-800/70 text-blue-300"
                      : "bg-[#111111] border-[#242424] text-neutral-500 hover:border-[#3a3a3a]"
                  )}
                >
                  {m}
                </button>
              );
            })}
          </div>

          {/* Interval */}
          <label className="block text-[0.62rem] text-neutral-600 mb-1.5">Scan interval</label>
          <div className="flex gap-1.5 mb-4">
            {INTERVAL_OPTIONS.map((iv) => (
              <button
                key={iv}
                type="button"
                onClick={() => setIntervalMin(iv)}
                className={cn(
                  "flex-1 px-2 py-1.5 text-[0.68rem] rounded-lg border transition-colors",
                  interval === iv
                    ? "bg-[#1f1f1f] border-[#3a3a3a] text-neutral-200"
                    : "bg-[#111111] border-[#242424] text-neutral-600 hover:border-[#3a3a3a]"
                )}
              >
                {INTERVAL_LABEL[iv]}
              </button>
            ))}
          </div>

          {/* Email alerts on/off */}
          <button
            type="button"
            onClick={() => setEmailOn((v) => !v)}
            className="flex items-center justify-between w-full mb-4 px-3 py-2 rounded-lg bg-[#111111] border border-[#242424] hover:border-[#3a3a3a] transition-colors"
          >
            <span className="text-xs text-neutral-400">Email alerts</span>
            <span
              className={cn(
                "text-[0.6rem] px-2 py-0.5 rounded-full border",
                emailOn
                  ? "border-emerald-900/50 text-emerald-400 bg-emerald-950/20"
                  : "border-[#2a2a2a] text-neutral-600"
              )}
            >
              {emailOn ? "ON" : "OFF"}
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
              {saving ? "Saving…" : "Save Preferences"}
            </button>
            <button
              type="button"
              onClick={runNow}
              disabled={running}
              className="flex-1 px-3 py-2 text-xs rounded-lg bg-white text-black font-medium hover:bg-neutral-200 transition-colors disabled:opacity-50"
            >
              {running ? "Scanning…" : "Trigger Now"}
            </button>
          </div>

          {/* Status */}
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

          {/* Report */}
          {result && (
            <div className="mt-4 border-t border-[#1a1a1a] pt-3">
              <button
                type="button"
                onClick={() => setReportOpen((v) => !v)}
                className="flex items-center justify-between w-full text-[0.62rem] text-neutral-500 uppercase tracking-widest font-mono mb-2"
              >
                <span>Live Briefing</span>
                {reportOpen ? (
                  <ChevronUpIcon className="w-3.5 h-3.5" />
                ) : (
                  <ChevronDownIcon className="w-3.5 h-3.5" />
                )}
              </button>

              {reportOpen && (
                <div className="space-y-3">
                  {/* Email status */}
                  <div
                    className={cn(
                      "text-[0.62rem] px-2.5 py-1.5 rounded-lg border",
                      result.email_sent
                        ? "border-emerald-900/50 text-emerald-400 bg-emerald-950/20"
                        : "border-amber-900/60 text-amber-400 bg-amber-950/20"
                    )}
                  >
                    {result.email_sent
                      ? `✓ Emailed to ${email.trim()}`
                      : `✉ Not emailed — ${result.email_status}`}
                  </div>

                  {/* Briefing text */}
                  <div className="text-xs leading-relaxed border border-[#2a2a2a] bg-[#141414] text-neutral-300 rounded-lg px-3 py-2.5 whitespace-pre-wrap">
                    {result.report}
                  </div>

                  {/* Live macro indicators (FRED) */}
                  {result.live_data.macro_indicators.length > 0 && (
                    <div className="text-[0.68rem] text-neutral-500">
                      <span className="text-neutral-600 uppercase tracking-wide font-mono text-[0.58rem]">
                        Live data · FRED
                      </span>
                      <div className="mt-1">
                        {result.live_data.macro_indicators.map((m) => (
                          <div key={m.label} className="flex justify-between tabular-nums py-0.5 gap-3">
                            <span className="text-neutral-500 truncate">{m.label}</span>
                            <span className="text-neutral-400 whitespace-nowrap">
                              {m.value}
                              {m.unit === "%" ? "%" : ` ${m.unit}`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
