// Per-session assessment history — kept client-side in sessionStorage so the
// "Recent Assessments" panel shows only what THIS browser session produced,
// not the shared Elasticsearch audit trail (which spans all users/sessions).

export interface SessionAssessment {
  timestamp: string;
  narrative: string;
  sovereign_risk_score: number;
}

const KEY = "macropulse.session.assessments";
const MAX = 20;

export function getSessionAssessments(): SessionAssessment[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function addSessionAssessment(a: SessionAssessment): void {
  if (typeof window === "undefined") return;
  try {
    const list = getSessionAssessments();
    list.unshift(a);
    sessionStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    // sessionStorage unavailable (private mode / SSR) — ignore
  }
}
