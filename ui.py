"""
MacroPulse — Command & Control Interface
Streamlit operational dashboard for the multi-agent sovereign risk platform.

Run locally:
    streamlit run ui.py

Or point it at any deployed MacroPulse endpoint via the sidebar host input.
"""

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MacroPulse C2",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Pre-populated defaults (instantly clickable for demo / testing)
# ---------------------------------------------------------------------------
DEFAULT_HOST = "https://macropulse-270431042772.us-central1.run.app"

DEFAULT_NARRATIVE = (
    "Argentina has suspended all external debt payments following a breakdown in IMF "
    "negotiations. The Argentine peso has collapsed 45% against the USD in 72 hours, "
    "triggering emergency capital controls and a freeze on USD-denominated bank deposits. "
    "The central bank's FX reserves have fallen below $5 billion — an all-time low. "
    "Contagion fears are spreading to other highly indebted EM sovereigns including "
    "Turkey, Pakistan, and Egypt, with their sovereign CDS spreads widening by 180-320bps."
)

DEFAULT_CONTEXT = "Sovereign default, EM credit contagion, FX crisis, capital controls"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🌐 MacroPulse Command & Control")
st.caption(
    "Enterprise multi-agent sovereign risk intelligence — "
    "Gemini 2.5 Flash · Elasticsearch Serverless · Two-agent correction loop"
)
st.divider()

# ---------------------------------------------------------------------------
# Layout: left inputs | right outputs
# ---------------------------------------------------------------------------
left, right = st.columns([1, 1], gap="large")

# ── LEFT COLUMN: Inputs ───────────────────────────────────────────────────
with left:
    st.subheader("📥 Analysis Input")

    host_url = st.text_input(
        "API Endpoint",
        value=DEFAULT_HOST,
        placeholder="https://your-cloud-run-url.run.app",
        help="MacroPulse FastAPI service URL (Cloud Run or local).",
    )

    narrative = st.text_area(
        "Financial Narrative",
        value=DEFAULT_NARRATIVE,
        height=220,
        placeholder="Paste an unstructured financial narrative, news article, or macro scenario…",
        help="The raw narrative that will be fact-audited and risk-assessed.",
    )

    context = st.text_input(
        "Context / Keywords",
        value=DEFAULT_CONTEXT,
        placeholder="e.g. Sovereign default, EM credit, FX volatility",
        help="Optional framing context passed to both agents.",
    )

    run_btn = st.button("▶ Run Multi-Agent Analysis", type="primary", use_container_width=True)

# ── RIGHT COLUMN: Outputs ─────────────────────────────────────────────────
with right:
    st.subheader("📊 Risk Intelligence Output")

    if not run_btn:
        st.info(
            "Configure inputs on the left and click **Run Multi-Agent Analysis** "
            "to trigger the two-agent evaluation cascade."
        )
        st.stop()

    if not narrative.strip():
        st.error("Please enter a financial narrative before running the analysis.")
        st.stop()

    endpoint = f"{host_url.rstrip('/')}/api/v1/evaluate"

    with st.spinner("⏳ Running Fact Auditor → Sovereign Risk Specialist cascade…"):
        try:
            resp = httpx.post(
                endpoint,
                json={"narrative": narrative, "context": context or None},
                timeout=180.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            st.error(f"API error {exc.response.status_code}: {exc.response.text[:400]}")
            st.stop()
        except httpx.RequestError as exc:
            st.error(f"Connection error: {exc}")
            st.stop()

    assessment = data["assessment"]
    score: float = assessment["sovereign_risk_score"]
    alert: bool = assessment["requires_immediate_alert"]
    threat: str = assessment["primary_threat_vector"]
    audit: str = assessment["audit_findings"]
    impact: str = assessment["impact_assessment"]

    # ── Status badge ──────────────────────────────────────────────────────
    if alert:
        st.error("🚨 IMMEDIATE ESCALATION REQUIRED — Trading Desk Alert Active")
    else:
        st.success("✅ Standard Review Queue — No Immediate Escalation")

    # ── Risk score gauge ──────────────────────────────────────────────────
    if score >= 9.0:
        score_label, score_color = "CRITICAL", "🔴"
    elif score >= 7.5:
        score_label, score_color = "HIGH", "🟠"
    elif score >= 5.0:
        score_label, score_color = "MEDIUM", "🟡"
    else:
        score_label, score_color = "LOW", "🟢"

    col_score, col_threat = st.columns([1, 2])
    with col_score:
        st.metric(
            label=f"Sovereign Risk Score {score_color}",
            value=f"{score:.1f} / 10.0",
            delta=score_label,
            delta_color="inverse" if score >= 5.0 else "normal",
        )
    with col_threat:
        st.metric(label="Primary Threat Vector", value=threat)

    st.progress(
        min(score / 10.0, 1.0),
        text=f"Risk level: {score_label} ({score:.1f}/10)",
    )

    st.divider()

    # ── Auditor findings (expandable) ─────────────────────────────────────
    with st.expander("🔍 Fact Auditor Report", expanded=True):
        st.markdown(audit if audit.strip() else "_No audit findings returned._")

    # ── Impact assessment (expandable) ────────────────────────────────────
    with st.expander("📈 Macro Impact Assessment", expanded=True):
        st.markdown(impact if impact.strip() else "_No impact assessment returned._")

    # ── Response metadata ─────────────────────────────────────────────────
    with st.expander("ℹ️ Response Metadata"):
        st.json({
            "model_used": data.get("model_used"),
            "evaluation_timestamp": data.get("evaluation_timestamp"),
            "alert_dispatched": data.get("alert_dispatched"),
            "endpoint_called": endpoint,
        })