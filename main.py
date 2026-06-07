import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import app as app_module
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
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
    "Use the full audit context and grounding documents provided to you."
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
    sovereign_risk_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Composite sovereign risk score: 0.0 (negligible) → 10.0 (systemic collapse).",
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


class EvaluationResponse(BaseModel):
    assessment: SovereignRiskAssessment
    model_used: str
    evaluation_timestamp: str
    alert_dispatched: bool


# Internal model used by Agent 2 (excludes audit_findings — injected from Agent 1)
class _SpecialistOutput(BaseModel):
    sovereign_risk_score: float = Field(..., ge=0.0, le=10.0)
    primary_threat_vector: str
    impact_assessment: str
    requires_immediate_alert: bool

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
    version="5.0.0",
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
) -> tuple[str, str]:
    """
    Agent 1 — Fact Auditor.

    Uses the Elastic MCP search tool to gather grounding documents, then
    produces a structured audit report identifying data gaps, inflations,
    and factual errors in the narrative.

    Returns: (audit_findings_text, combined_grounding_docs)
    """
    prompt = (
        f"NARRATIVE TO AUDIT:\n{narrative}"
        + (f"\n\nCONTEXT: {context}" if context else "")
        + "\n\nUse search_macro_data to retrieve supporting documents, then write "
          "a structured fact-audit identifying: DATA GAPS, INFLATIONS, ERRORS."
    )

    contents: list = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    grounding_chunks: list[str] = []

    for turn in range(max_turns):
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

        if not fn_calls:
            logger.info("Auditor search loop done | turns=%d", turn + 1)
            break

        contents.append(candidate.content)
        fn_parts: list[types.Part] = []
        for fc in fn_calls:
            if fc.name == "search_macro_data":
                logger.info("Agent1 → search_macro_data | query=%r", fc.args.get("query"))
                result = await app_module.search_macro_data(fc.args["query"])
                grounding_chunks.append(result)
                fn_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name, response={"result": result}
                        )
                    )
                )
        contents.append(types.Content(role="user", parts=fn_parts))
    else:
        logger.warning("Auditor loop hit MAX_TURNS=%d", max_turns)

    # Force audit report extraction
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(
                text=(
                    "Now write your complete fact-audit report. Structure it with clear "
                    "sections: DATA GAPS, INFLATIONS, ERRORS, and an overall ACCURACY RATING (0-10). "
                    "Be specific and cite the retrieved documents."
                )
            )],
        )
    )
    audit_response = await genai_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=AUDITOR_PERSONA),
    )
    audit_text = getattr(audit_response, "text", "") or ""
    combined_grounding = "\n\n===\n\n".join(grounding_chunks) if grounding_chunks else ""
    return audit_text, combined_grounding


async def _run_agent2_specialist(
    narrative: str,
    context: Optional[str],
    audit_findings: str,
    grounding_docs: str,
) -> _SpecialistOutput:
    """
    Agent 2 — Sovereign Risk Specialist.

    Synthesises the original narrative, the Auditor's findings, and the raw
    grounding documents into a structured sovereign risk assessment.
    """
    prompt = "\n\n".join(filter(None, [
        f"ORIGINAL NARRATIVE:\n{narrative}",
        f"CONTEXT: {context}" if context else None,
        f"GROUNDING DOCUMENTS (Elasticsearch):\n{grounding_docs}" if grounding_docs else None,
        f"FACT AUDITOR REPORT:\n{audit_findings}",
        (
            "Based on ALL the above, produce the final sovereign risk assessment. "
            "Your score must reflect the audit-adjusted picture, not just the narrative's claims."
        ),
    ]))

    try:
        response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SPECIALIST_PERSONA,
                response_mime_type="application/json",
                response_schema=_SpecialistOutput,
            ),
        )
        if getattr(response, "parsed", None) is not None:
            return response.parsed
        return _SpecialistOutput.model_validate_json(response.text)
    except Exception as exc:
        logger.error("Agent 2 structured output failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Specialist agent failure: {exc}")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
        audit_findings, grounding_docs = await _run_agent1_auditor(
            request.narrative, request.context
        )
    except Exception as exc:
        logger.error("Agent 1 (Fact Auditor) failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Fact Auditor agent failure: {exc}")
    logger.info("Auditor complete | audit_chars=%d", len(audit_findings))

    # ── Agent 2: Sovereign Risk Specialist ────────────────────────────────
    specialist_output = await _run_agent2_specialist(
        request.narrative, request.context, audit_findings, grounding_docs
    )
    logger.info(
        "Specialist complete | score=%.2f | threat=%r",
        specialist_output.sovereign_risk_score,
        specialist_output.primary_threat_vector,
    )

    # Compose final assessment — inject Agent 1's audit into the output model
    assessment = SovereignRiskAssessment(
        sovereign_risk_score=specialist_output.sovereign_risk_score,
        primary_threat_vector=specialist_output.primary_threat_vector,
        audit_findings=audit_findings,
        impact_assessment=specialist_output.impact_assessment,
        requires_immediate_alert=specialist_output.requires_immediate_alert,
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