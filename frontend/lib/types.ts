export type PipelineAgent = "agent1" | "crosscheck" | "agent2";
export type StepKind = "normal" | "search" | "complete" | "score";

export interface PipelineStep {
  agent: PipelineAgent;
  text: string;
  kind: StepKind;
  score?: number;
}

export interface SovereignRiskAssessment {
  sovereign_risk_score: number;
  primary_threat_vector: string;
  audit_findings: string;
  impact_assessment: string;
  requires_immediate_alert: boolean;
}

export interface EvaluationResponse {
  assessment: SovereignRiskAssessment;
  model_used: string;
  evaluation_timestamp: string;
  alert_dispatched: boolean;
}

export type AppState = "idle" | "running" | "done" | "error";