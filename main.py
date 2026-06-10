import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from dotenv import load_dotenv
load_dotenv()

import app as app_module
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from sse_starlette.sse import EventSourceResponse
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("macropulse")

# ---------------------------------------------------------------------------
# Vertex AI / Gemini client — uses project-bound Application Default Credentials.
# Locally: `gcloud auth application-default login`
# Cloud Run: resolved automatically from the attached service account.
# ---------------------------------------------------------------------------
genai_client = genai.Client(
    vertexai=True,
    project=os.environ.get("GOOGLE_CLOUD_PROJECT", "project-a8903c34-ce47-431c-9f7"),
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
)

GEMINI_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Agent personas
# ---------------------------------------------------------------------------
AUDITOR_PERSONA = (
    "You are a Fact Auditor at an independent sovereign credit rating agency. "
    "Your sole function is to rigorously cross-reference financial narratives against "
    "retrieved institutional research documents. Identify: (1) DATA GAPS — specific claims "
    "in the narrative that lack supporting evidence in the retrieved corpus; (2) INFLATIONS — "
    "language that appears exaggerated, sensationalist, or quantitatively unsupported; "
    "(3) ERRORS — factual inaccuracies contradicted by the evidence. "
    "Use the search_macro_data tool to gather supporting documents before writing your report. "
    "Be precise, cite specific discrepancies, and remain analytically neutral."
)

SPECIALIST_PERSONA = (
    "You are an elite Sovereign Risk Specialist at a tier-1 investment bank. "
    "You synthesise fact-audited financial narratives and institutional research to produce "
    "definitive country risk profiles that drive trading desk and risk committee decisions. "
    "Your assessments must be zero-fluff, quantitatively grounded, and structurally precise. "
    "Use the full audit context and grounding documents provided to you.\n\n"
    "SCORING RULES:\n"
    "1. If the Fact Auditor's report states that retrieved documents ACTIVELY CONTRADICT the "
    "narrative's specific claims (not merely 'not found'), reduce the score significantly — "
    "contradicted claims should not be scored as if true.\n"
    "2. If the Fact Auditor reports DATA GAPS (claims not verifiable in the knowledge base) "
    "for a narrative that describes well-established macro events (hyperinflation, formal default, "
    "full capital controls), score based on the described severity of those events — absence of "
    "evidence in the knowledge base is not contradiction.\n"
    "3. primary_threat_vector must describe the risk specific to this narrative. "
    "requires_immediate_alert must be False when sovereign_risk_score < 5.0.\n"
    "4. raw_narrative_score: score the narrative AS WRITTEN, taking all claims at face value "
    "and ignoring the audit findings. This is the pre-audit baseline. "
    "sovereign_risk_score is the audit-ADJUSTED final score — it must reflect the audit corrections. "
    "These two scores should differ whenever the audit found contradictions or inflations.\n"
    "5. GROUNDING ASSESSMENT — you are given the titles of the documents retrieved from the "
    "knowledge base. Judge how well they ground THIS specific narrative and set grounding_strength:\n"
    "   • STRONG — one or more retrieved documents are specifically about the country/entity/event "
    "in the narrative (e.g. an IMF Article IV for that exact country, or that central bank's report).\n"
    "   • PARTIAL — retrieved documents are topically relevant (general sovereign-risk / macro "
    "research) but NONE are specific to this narrative's country/entity. The score then rests "
    "largely on the narrative itself, not corroborating evidence.\n"
    "   • LIMITED — retrieved documents are off-topic or absent; there is essentially no usable "
    "grounding (e.g. the narrative is outside the sovereign/macro-risk domain).\n"
    "   grounding_note: one concise sentence naming the evidence basis (or its absence). When "
    "grounding is PARTIAL or LIMITED, explicitly state the assessment is narrative-driven and "
    "should be treated as lower-confidence."
)

# ---------------------------------------------------------------------------
# Elastic MCP search tool declaration (used by Agent 1's tool-calling loop)
# ---------------------------------------------------------------------------
_SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_macro_data",
            description=(
                "Search the MacroPulse macro-economic knowledge base using hybrid retrieval "
                "(RRF text + vector). Call with targeted economic keywords to retrieve "
                "institutional research documents that ground your analysis."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Search query — economic indicators, sovereign risk terms, "
                            "asset class names, or scenario-specific keywords."
                        ),
                    )
                },
                required=["query"],
            ),
        )
    ]
)

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class EvaluationRequest(BaseModel):
    narrative: str = Field(
        ...,
        description="Unstructured financial narrative or macro scenario to evaluate.",
        min_length=20,
    )
    context: Optional[str] = Field(
        None,
        description="Optional framing context — asset class, issuer, or geographic region.",
    )


class SovereignRiskAssessment(BaseModel):
    raw_narrative_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Pre-audit score: risk level if the narrative were taken at face value.",
    )
    sovereign_risk_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Audit-adjusted composite sovereign risk score: 0.0 (negligible) → 10.0 (systemic collapse).",
    )
    primary_threat_vector: str = Field(
        ...,
        description=(
            "Single dominant risk classification, e.g. 'Sovereign Default Risk', "
            "'FX Volatility', 'Liquidity Crunch', 'Fiscal Deficit Expansion'."
        ),
    )
    audit_findings: str = Field(
        ...,
        description=(
            "Structured findings from the Fact Auditor: data gaps, inflations, "
            "and factual errors identified in the original narrative."
        ),
    )
    impact_assessment: str = Field(
        ...,
        description=(
            "Definitive structural narrative covering transmission mechanisms, "
            "affected asset classes, and second-order macro consequences."
        ),
    )
    requires_immediate_alert: bool = Field(
        ...,
        description=(
            "True when the risk profile demands immediate escalation "
            "to the trading desk or risk committee."
        ),
    )
    grounding_strength: Literal["STRONG", "PARTIAL", "LIMITED"] = Field(
        ...,
        description=(
            "How well the retrieved knowledge-base documents ground THIS narrative. "
            "STRONG = entity-specific evidence found; PARTIAL = only general research; "
            "LIMITED = off-topic/absent (treat score as low-confidence)."
        ),
    )
    grounding_note: str = Field(
        ...,
        description="One sentence naming the evidence basis (or its absence) for the grounding rating.",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Titles of the knowledge-base documents retrieved to ground this assessment.",
    )


class EvaluationResponse(BaseModel):
    assessment: SovereignRiskAssessment
    model_used: str
    evaluation_timestamp: str
    alert_dispatched: bool


# Internal model used by Agent 2 (excludes audit_findings — injected from Agent 1)
class _SpecialistOutput(BaseModel):
    raw_narrative_score: float = Field(..., ge=0.0, le=10.0)
    sovereign_risk_score: float = Field(..., ge=0.0, le=10.0)
    primary_threat_vector: str
    impact_assessment: str
    requires_immediate_alert: bool
    grounding_strength: Literal["STRONG", "PARTIAL", "LIMITED"]
    grounding_note: str

# ---------------------------------------------------------------------------
# Lifespan — Elastic MCP client startup / teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Fail fast if required credentials are absent
    for var in ("ELASTIC_ENDPOINT", "ELASTIC_API_KEY"):
        if not os.environ.get(var):
            raise RuntimeError(
                f"Required environment variable '{var}' is not set. "
                "Copy .env.example to .env and populate it before starting."
            )

    elastic_mcp_url = os.environ.get("ELASTIC_MCP_URL", "").strip()
    _BUNDLED_BIN = "/usr/local/bin/elasticsearch-core-mcp-server"

    if elastic_mcp_url:
        # HTTP transport to an external Elastic MCP Cloud Run service
        import google.auth.transport.requests
        import google.oauth2.id_token

        logger.info("Elastic MCP: fetching ID token for %s", elastic_mcp_url)
        _req = google.auth.transport.requests.Request()
        id_token = await asyncio.to_thread(
            google.oauth2.id_token.fetch_id_token, _req, elastic_mcp_url
        )
        async with streamablehttp_client(
            url=f"{elastic_mcp_url}/mcp",
            headers={"Authorization": f"Bearer {id_token}"},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                app_module._call_elastic_tool = session.call_tool
                tools = await session.list_tools()
                logger.info("Elastic MCP HTTP ready | tools=%s", [t.name for t in tools.tools])
                yield

    elif os.path.isfile(_BUNDLED_BIN):
        # Production (Cloud Run): bundled binary as stdio subprocess — same network
        # context as MacroPulse, no Docker-in-Docker, no separate service.
        logger.info("Elastic MCP: spawning bundled binary %s", _BUNDLED_BIN)
        server_params = StdioServerParameters(
            command=_BUNDLED_BIN,
            args=["stdio"],
            env={
                **os.environ,
                "ES_URL": os.environ["ELASTIC_ENDPOINT"],
                "ES_API_KEY": os.environ["ELASTIC_API_KEY"],
            },
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                app_module._call_elastic_tool = session.call_tool
                tools = await session.list_tools()
                logger.info("Elastic MCP binary ready | tools=%s", [t.name for t in tools.tools])
                yield

    else:
        # Local development: Docker stdio subprocess
        logger.info("Elastic MCP: spawning Docker stdio subprocess")
        server_params = StdioServerParameters(
            command="docker",
            args=[
                "run", "--rm", "-i",
                "-e", f"ES_URL={os.environ['ELASTIC_ENDPOINT']}",
                "-e", f"ES_API_KEY={os.environ['ELASTIC_API_KEY']}",
                "docker.elastic.co/mcp/elasticsearch",
                "stdio",
            ],
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                app_module._call_elastic_tool = session.call_tool
                tools = await session.list_tools()
                logger.info("Elastic MCP Docker ready | tools=%s", [t.name for t in tools.tools])
                yield

    app_module._call_elastic_tool = None
    logger.info("Elastic MCP session closed")

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MacroPulse Analytics Bridge",
    description=(
        "Enterprise-grade multi-agent RAG platform — Gemini 2.5 Flash (Vertex AI) "
        "orchestrates a two-agent correction loop (Fact Auditor → Sovereign Risk Specialist) "
        "grounded by Elasticsearch Serverless hybrid retrieval (RRF text + vector), "
        "exposed over MCP for Agent Builder integration."
    ),
    version="5.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Background task tracking — prevents GC of fire-and-forget coroutines
# ---------------------------------------------------------------------------
_background_tasks: set = set()


def _fire_and_forget(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task

# ---------------------------------------------------------------------------
# Outbound Webhook — Trading Desk Alert
# ---------------------------------------------------------------------------

async def trigger_trading_desk_webhook(ticket: dict) -> None:
    webhook_url = os.environ.get("TRADING_DESK_WEBHOOK_URL", "https://httpbin.org/post")
    score: float = ticket.get("sovereign_risk_score", 0.0)

    if score >= 9.0:
        severity, policy, sla = "CRITICAL", "IMMEDIATE_DESK_REVIEW", 5
    elif score >= 7.5:
        severity, policy, sla = "HIGH", "IMMEDIATE_DESK_REVIEW", 15
    elif score >= 5.0:
        severity, policy, sla = "MEDIUM", "STANDARD_REVIEW_QUEUE", 60
    else:
        severity, policy, sla = "LOW", "DAILY_DIGEST", 1440

    payload = {
        "event_type": "MACRO_RISK_ALERT",
        "schema_version": "5.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "sla_resolution_minutes": sla,
        "source_system": "MacroPulse Analytics Bridge v5",
        "escalation_policy": policy,
        "routing_key": f"risk.{severity.lower()}.sovereign",
        "risk_parameters": ticket,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http_client:
        try:
            resp = await http_client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-MacroPulse-Version": "5.0",
                    "X-Alert-Severity": severity,
                },
            )
            resp.raise_for_status()
            logger.info(
                "Alert dispatched | severity=%s | score=%.2f | status=%d",
                severity, score, resp.status_code,
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.error("Webhook delivery failed: %s", exc)

# ---------------------------------------------------------------------------
# Core orchestration — two-agent cascade
# ---------------------------------------------------------------------------

async def _run_agent1_auditor(
    narrative: str,
    context: Optional[str],
    max_turns: int = 4,
    on_event: Optional[Callable[[str, dict], Any]] = None,
) -> tuple[str, str, list[str]]:
    """
    Agent 1 — Fact Auditor.

    Runs a tool-calling loop (≤ max_turns) where Gemini searches Elasticsearch
    for grounding documents. When the model stops calling tools its final text
    response IS the structured audit — no separate extraction call needed.

    Returns: (audit_findings_text, combined_grounding_docs, source_titles)
    """
    async def emit(evt: str, data: dict) -> None:
        if on_event:
            await on_event(evt, data)

    await emit("agent1_start", {"message": "Fact Auditor initializing..."})

    prompt = (
        f"NARRATIVE TO AUDIT:\n{narrative}"
        + (f"\n\nCONTEXT: {context}" if context else "")
        + "\n\nInstructions: Call search_macro_data once or twice with targeted keywords "
          "to retrieve relevant institutional research. Then, IN THIS SAME RESPONSE after "
          "your searches, write your complete fact-audit report with clear sections: "
          "DATA GAPS, INFLATIONS, ERRORS, and ACCURACY RATING (0-10). Cite the retrieved documents."
    )

    contents: list = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    grounding_chunks: list[str] = []
    source_titles: list[str] = []  # deduped doc titles retrieved across all searches
    inline_audit = ""  # captured directly from the loop's final turn

    for turn in range(max_turns):
        await emit("agent1_thinking", {"turn": turn + 1, "message": f"Analyzing narrative (pass {turn + 1})..."})
        response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=AUDITOR_PERSONA,
                tools=[_SEARCH_TOOL],
            ),
        )
        candidate = response.candidates[0]
        fn_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        text_parts = [p.text for p in candidate.content.parts if getattr(p, "text", None)]

        if not fn_calls:
            # Model finished — its text IS the audit report
            inline_audit = "\n".join(text_parts).strip()
            logger.info("Auditor produced inline audit | turns=%d | chars=%d", turn + 1, len(inline_audit))
            await emit("agent1_complete", {"chars": len(inline_audit), "message": "Audit report generated"})
            break

        contents.append(candidate.content)
        fn_parts: list[types.Part] = []
        for fc in fn_calls:
            if fc.name == "search_macro_data":
                query = fc.args.get("query", "")
                logger.info("Agent1 → search_macro_data | query=%r", query)
                result, titles = await app_module._search_with_sources(query)
                grounding_chunks.append(result)
                for t in titles:
                    if t not in source_titles:
                        source_titles.append(t)
                await emit("agent1_search", {"query": query, "sources": titles})
                fn_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name, response={"result": result}
                        )
                    )
                )
        contents.append(types.Content(role="user", parts=fn_parts))
    else:
        logger.warning("Auditor loop hit MAX_TURNS=%d without producing audit text", max_turns)

    if inline_audit:
        audit_text = inline_audit
    else:
        # Fallback: model hit max_turns without writing the audit — force one extraction call
        logger.info("Auditor falling back to explicit extraction call")
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text=(
                    "Now write your complete fact-audit report: DATA GAPS, INFLATIONS, "
                    "ERRORS, ACCURACY RATING (0-10). Cite the retrieved documents."
                ))],
            )
        )
        fallback = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=AUDITOR_PERSONA),
        )
        audit_text = getattr(fallback, "text", "") or ""
        await emit("agent1_complete", {"chars": len(audit_text), "message": "Audit report generated (fallback)"})

    combined_grounding = "\n\n===\n\n".join(grounding_chunks) if grounding_chunks else ""
    if len(combined_grounding) > 12_000:
        combined_grounding = combined_grounding[:12_000] + "\n\n[... grounding truncated for context limits ...]"
    return audit_text, combined_grounding, source_titles


async def _run_agent2_specialist(
    narrative: str,
    context: Optional[str],
    audit_findings: str,
    grounding_docs: str,
    sources: list[str],
    on_event: Optional[Callable[[str, dict], Any]] = None,
) -> _SpecialistOutput:
    """
    Agent 2 — Sovereign Risk Specialist.

    Synthesises the original narrative, the Auditor's findings, and the raw
    grounding documents into a structured sovereign risk assessment, and judges
    how well the retrieved sources actually ground this specific narrative.
    """
    async def emit(evt: str, data: dict) -> None:
        if on_event:
            await on_event(evt, data)

    await emit("agent2_start", {"message": "Risk Specialist synthesizing narrative + audit + grounding docs..."})

    sources_block = (
        "RETRIEVED SOURCE DOCUMENTS (titles — judge grounding_strength against these):\n"
        + ("\n".join(f"- {t}" for t in sources) if sources else "(none retrieved)")
    )
    prompt = "\n\n".join(filter(None, [
        f"ORIGINAL NARRATIVE:\n{narrative}",
        f"CONTEXT: {context}" if context else None,
        f"GROUNDING DOCUMENTS (Elasticsearch):\n{grounding_docs}" if grounding_docs else None,
        sources_block,
        f"FACT AUDITOR REPORT:\n{audit_findings}",
        (
            "Based on ALL the above, produce the final sovereign risk assessment. "
            "Your score must reflect the audit-adjusted picture, not just the narrative's claims. "
            "Set grounding_strength by judging whether the retrieved source documents are "
            "specific to this narrative's country/entity (STRONG), merely general research "
            "(PARTIAL), or off-topic/absent (LIMITED)."
        ),
    ]))

    try:
        await emit("cross_check", {"message": "Cross-checking audit ↔ narrative consistency..."})
        response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SPECIALIST_PERSONA,
                response_mime_type="application/json",
                response_schema=_SpecialistOutput,
            ),
        )
        result = response.parsed if getattr(response, "parsed", None) is not None else _SpecialistOutput.model_validate_json(response.text)
        await emit("agent2_complete", {
            "score": result.sovereign_risk_score,
            "raw_score": result.raw_narrative_score,
            "threat": result.primary_threat_vector,
            "alert": result.requires_immediate_alert,
            "grounding_strength": result.grounding_strength,
            "grounding_note": result.grounding_note,
            "message": f"Score computed: {result.sovereign_risk_score:.1f}/10",
        })
        return result
    except Exception as exc:
        logger.error("Agent 2 structured output failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Specialist agent failure: {exc}")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", operation_id="health_check", summary="Service Health Check", tags=["Operations"])
async def health_check() -> dict:
    return {
        "status": "healthy",
        "service": "MacroPulse Analytics Bridge",
        "version": "5.0.0",
        "model": GEMINI_MODEL,
        "elastic_mcp": "connected" if app_module._call_elastic_tool else "disconnected",
    }


async def _orchestrate(request: EvaluationRequest) -> EvaluationResponse:
    """Shared handler for both route aliases."""
    logger.info(
        "Evaluation requested | narrative_chars=%d | context=%r",
        len(request.narrative),
        request.context,
    )

    # ── Agent 1: Fact Auditor ─────────────────────────────────────────────
    try:
        audit_findings, grounding_docs, sources = await _run_agent1_auditor(
            request.narrative, request.context
        )
    except Exception as exc:
        logger.error("Agent 1 (Fact Auditor) failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Fact Auditor agent failure: {exc}")
    logger.info("Auditor complete | audit_chars=%d | sources=%d", len(audit_findings), len(sources))

    # ── Agent 2: Sovereign Risk Specialist ────────────────────────────────
    specialist_output = await _run_agent2_specialist(
        request.narrative, request.context, audit_findings, grounding_docs, sources
    )
    logger.info(
        "Specialist complete | score=%.2f | threat=%r | grounding=%s",
        specialist_output.sovereign_risk_score,
        specialist_output.primary_threat_vector,
        specialist_output.grounding_strength,
    )

    # Enforce consistency: alert cannot fire for low-risk scores regardless of model output.
    # Prevents hallucinated alerts when sovereign_risk_score < 5.0.
    alert_flag = specialist_output.requires_immediate_alert and specialist_output.sovereign_risk_score >= 5.0

    # Compose final assessment — inject Agent 1's audit into the output model
    assessment = SovereignRiskAssessment(
        raw_narrative_score=specialist_output.raw_narrative_score,
        sovereign_risk_score=specialist_output.sovereign_risk_score,
        primary_threat_vector=specialist_output.primary_threat_vector,
        audit_findings=audit_findings,
        impact_assessment=specialist_output.impact_assessment,
        requires_immediate_alert=alert_flag,
        grounding_strength=specialist_output.grounding_strength,
        grounding_note=specialist_output.grounding_note,
        sources=sources,
    )

    alert_dispatched = False
    if assessment.sovereign_risk_score >= 7.5 or assessment.requires_immediate_alert:
        _fire_and_forget(trigger_trading_desk_webhook(assessment.model_dump()))
        alert_dispatched = True
        logger.info(
            "Alert queued | score=%.2f | immediate=%s",
            assessment.sovereign_risk_score,
            assessment.requires_immediate_alert,
        )

    return EvaluationResponse(
        assessment=assessment,
        model_used=GEMINI_MODEL,
        evaluation_timestamp=datetime.now(timezone.utc).isoformat(),
        alert_dispatched=alert_dispatched,
    )


@app.post(
    "/api/v1/evaluate",
    response_model=EvaluationResponse,
    operation_id="evaluate_sovereign_risk",
    summary="Multi-Agent Sovereign Risk Evaluation",
    description=(
        "Two-agent cascade: Agent 1 (Fact Auditor) retrieves grounding documents via "
        "Elasticsearch hybrid search and audits the narrative for gaps/errors; "
        "Agent 2 (Sovereign Risk Specialist) synthesises the audit and evidence into "
        "a structured SovereignRiskAssessment. Alerts fire when score ≥ 7.5."
    ),
    tags=["Risk Intelligence"],
)
async def evaluate_v1(request: EvaluationRequest) -> EvaluationResponse:
    return await _orchestrate(request)


@app.post(
    "/evaluate-risk",
    response_model=EvaluationResponse,
    operation_id="evaluate_macro_risk_legacy",
    summary="Evaluate Macro & Sovereign Risk (legacy alias)",
    tags=["Risk Intelligence"],
    include_in_schema=True,
)
async def evaluate_risk_legacy(request: EvaluationRequest) -> EvaluationResponse:
    return await _orchestrate(request)


# ---------------------------------------------------------------------------
# Streaming SSE endpoint — real-time agent step events
# ---------------------------------------------------------------------------

async def _orchestrate_streaming(
    request: EvaluationRequest,
    on_event: Callable[[str, dict], Any],
) -> EvaluationResponse:
    """Run the two-agent pipeline, firing on_event callbacks at each step."""
    try:
        audit_findings, grounding_docs, sources = await _run_agent1_auditor(
            request.narrative, request.context, on_event=on_event
        )
    except Exception as exc:
        logger.error("Agent 1 (Fact Auditor) failed: %s", exc)
        await on_event("error", {"message": f"Fact Auditor failed: {exc}"})
        raise HTTPException(status_code=502, detail=f"Fact Auditor agent failure: {exc}")
    logger.info("Auditor complete | audit_chars=%d | sources=%d", len(audit_findings), len(sources))

    specialist_output = await _run_agent2_specialist(
        request.narrative, request.context, audit_findings, grounding_docs, sources,
        on_event=on_event,
    )
    logger.info(
        "Specialist complete | score=%.2f | threat=%r | grounding=%s",
        specialist_output.sovereign_risk_score,
        specialist_output.primary_threat_vector,
        specialist_output.grounding_strength,
    )

    alert_flag = specialist_output.requires_immediate_alert and specialist_output.sovereign_risk_score >= 5.0

    assessment = SovereignRiskAssessment(
        raw_narrative_score=specialist_output.raw_narrative_score,
        sovereign_risk_score=specialist_output.sovereign_risk_score,
        primary_threat_vector=specialist_output.primary_threat_vector,
        audit_findings=audit_findings,
        impact_assessment=specialist_output.impact_assessment,
        requires_immediate_alert=alert_flag,
        grounding_strength=specialist_output.grounding_strength,
        grounding_note=specialist_output.grounding_note,
        sources=sources,
    )

    alert_dispatched = False
    if assessment.sovereign_risk_score >= 7.5 or assessment.requires_immediate_alert:
        _fire_and_forget(trigger_trading_desk_webhook(assessment.model_dump()))
        alert_dispatched = True
        logger.info(
            "Alert queued | score=%.2f | immediate=%s",
            assessment.sovereign_risk_score,
            assessment.requires_immediate_alert,
        )

    result = EvaluationResponse(
        assessment=assessment,
        model_used=GEMINI_MODEL,
        evaluation_timestamp=datetime.now(timezone.utc).isoformat(),
        alert_dispatched=alert_dispatched,
    )
    await on_event("complete", result.model_dump())
    return result


@app.post(
    "/api/v1/evaluate/stream",
    operation_id="evaluate_sovereign_risk_stream",
    summary="Multi-Agent Sovereign Risk Evaluation (SSE stream)",
    description=(
        "Streaming variant of /api/v1/evaluate. Yields Server-Sent Events for each "
        "agent step (agent1_start, agent1_search, agent1_complete, agent2_start, "
        "cross_check, agent2_complete, complete). Final 'complete' event carries the "
        "full EvaluationResponse JSON."
    ),
    tags=["Risk Intelligence"],
)
async def evaluate_stream(request: EvaluationRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(event_type: str, data: dict) -> None:
        await queue.put((event_type, data))

    async def run_pipeline() -> None:
        try:
            await _orchestrate_streaming(request, on_event)
        except Exception as exc:
            await queue.put(("error", {"message": str(exc)}))
        finally:
            await queue.put(None)  # sentinel

    task = asyncio.create_task(run_pipeline())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    async def event_generator():
        while True:
            item = await queue.get()
            if item is None:
                break
            event_type, data = item
            yield {"event": event_type, "data": json.dumps(data)}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# MCP Server — mount AFTER all routes are registered
# ---------------------------------------------------------------------------
mcp = FastApiMCP(
    app,
    name="MacroPulse Analytics Bridge",
    description=(
        "MCP server exposing multi-agent sovereign risk evaluation for "
        "Google Cloud Agent Builder integration."
    ),
)
mcp.mount()