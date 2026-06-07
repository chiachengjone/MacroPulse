import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scoreLabel(score: number): string {
  if (score >= 9) return "CRITICAL";
  if (score >= 7.5) return "HIGH";
  if (score >= 5) return "MEDIUM";
  return "LOW";
}

export function scoreColor(score: number) {
  if (score >= 9)
    return { text: "text-red-400", bar: "bg-red-400", label: "CRITICAL" } as const;
  if (score >= 7.5)
    return { text: "text-orange-400", bar: "bg-orange-400", label: "HIGH" } as const;
  if (score >= 5)
    return { text: "text-amber-400", bar: "bg-amber-400", label: "MEDIUM" } as const;
  return { text: "text-green-400", bar: "bg-green-400", label: "LOW" } as const;
}