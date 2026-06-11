import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

import app as app_module
import httpx
from fastapi import FastAPI, Header, HTTPException
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
    "and ignoring the audit findings. This is the pre-audit baseline. However, it must still "
    "reflect the sovereign's known structural risk floor — a positive headline about a country "
    "with a history of default, IMF programs, or chronic inflation (e.g. Argentina, Pakistan, "
    "Türkiye, Egypt) should never score below 4.5 on the raw scale, even if the claims are "
    "optimistic. Narrative framing can reduce the score from a country's typical risk band but "
    "cannot eliminate the underlying structural risk. "
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
    action_disposition: Literal[
        "AUTO_ESCALATE", "ESCALATE_FLAGGED", "STANDARD_QUEUE", "AUTO_CLEAR", "HUMAN_REVIEW"
    ] = Field(
        ...,
        description=(
            "Deterministic autonomy decision combining risk score × grounding confidence. "
            "The system only auto-acts (dispatches an alert) when confident; weak grounding "
            "is routed to HUMAN_REVIEW rather than acted on autonomously."
        ),
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
# Gemini call with retry — handles transient 429 RESOURCE_EXHAUSTED by waiting
# and retrying up to 3 times with jittered exponential backoff (5s, 12s, 25s).
# ---------------------------------------------------------------------------
_PIPELINE_SEMAPHORE = asyncio.Semaphore(1)  # one pipeline at a time per instance
_RETRY_BASE_DELAYS = [5, 12, 25]


async def _generate_with_retry(model: str, **kwargs):
    """Wrap genai_client.aio.models.generate_content with 429-aware retry."""
    for attempt, base_delay in enumerate([0] + _RETRY_BASE_DELAYS):
        if base_delay:
            jitter = random.uniform(0, 3)
            wait = base_delay + jitter
            logger.warning("Gemini 429 — waiting %.1fs before retry %d/3", wait, attempt)
            await asyncio.sleep(wait)
        try:
            return await genai_client.aio.models.generate_content(model=model, **kwargs)
        except Exception as exc:
            is_429 = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
            if is_429 and attempt < len(_RETRY_BASE_DELAYS):
                continue
            raise
    raise RuntimeError("Gemini rate limit exceeded after 3 retries — please try again in a moment.")

# ---------------------------------------------------------------------------
# Autonomy policy — deterministic gate combining risk score × grounding confidence.
# The system only ACTS on its own (dispatches an alert) when it is confident;
# weak grounding is routed to a human instead of acted on autonomously. Computing
# this in code (not via the LLM) keeps the autonomy rule transparent and auditable.
# ---------------------------------------------------------------------------
_DISPATCH_DISPOSITIONS = {"AUTO_ESCALATE", "ESCALATE_FLAGGED"}


def decide_disposition(score: float, grounding: str) -> str:
    """Map (audit-adjusted score, grounding strength) → an autonomy decision."""
    if grounding == "LIMITED":
        # No usable evidence — never act autonomously, regardless of the score.
        return "HUMAN_REVIEW"
    if score >= 7.5:
        return "AUTO_ESCALATE" if grounding == "STRONG" else "ESCALATE_FLAGGED"
    if score >= 5.0:
        return "STANDARD_QUEUE" if grounding == "STRONG" else "HUMAN_REVIEW"
    return "AUTO_CLEAR" if grounding == "STRONG" else "STANDARD_QUEUE"

# ---------------------------------------------------------------------------
# Audit trail — every assessment is persisted to a searchable Elasticsearch index
# (governance / provenance). Fire-and-forget, same pattern as the webhook. The
# index is queryable like any other — including via the Elastic MCP server.
# ---------------------------------------------------------------------------
_AUDIT_INDEX = "macro-pulse-audit"


async def record_audit(record: dict) -> None:
    endpoint = os.environ.get("ELASTIC_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("ELASTIC_API_KEY", "")
    if not endpoint or not api_key:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                f"{endpoint}/{_AUDIT_INDEX}/_doc",
                json=record,
                headers={
                    "Authorization": f"ApiKey {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            logger.info("Audit record persisted | disposition=%s", record.get("action_disposition"))
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Audit persistence failed: %s", exc)

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
    max_turns: int = 2,
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
        response = await _generate_with_retry(
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
        fallback = await _generate_with_retry(
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
        response = await _generate_with_retry(
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


def _finalize_response(
    request: EvaluationRequest,
    specialist_output: _SpecialistOutput,
    audit_findings: str,
    sources: list[str],
) -> EvaluationResponse:
    """
    Apply the autonomy policy, compose the final assessment, gate the alert on
    confidence, and persist the audit record. Shared by both route handlers.
    """
    score = specialist_output.sovereign_risk_score
    grounding = specialist_output.grounding_strength
    disposition = decide_disposition(score, grounding)

    # The LLM's recommendation is kept for transparency; the deterministic gate decides the action.
    alert_flag = specialist_output.requires_immediate_alert and score >= 5.0

    assessment = SovereignRiskAssessment(
        raw_narrative_score=specialist_output.raw_narrative_score,
        sovereign_risk_score=score,
        primary_threat_vector=specialist_output.primary_threat_vector,
        audit_findings=audit_findings,
        impact_assessment=specialist_output.impact_assessment,
        requires_immediate_alert=alert_flag,
        grounding_strength=grounding,
        grounding_note=specialist_output.grounding_note,
        sources=sources,
        action_disposition=disposition,
    )

    # Auto-dispatch ONLY when the policy is confident enough to act on its own.
    alert_dispatched = disposition in _DISPATCH_DISPOSITIONS
    if alert_dispatched:
        ticket = assessment.model_dump()
        ticket["low_confidence"] = disposition == "ESCALATE_FLAGGED"
        _fire_and_forget(trigger_trading_desk_webhook(ticket))

    ts = datetime.now(timezone.utc).isoformat()

    # Persist every assessment to the searchable audit trail (governance / provenance).
    _fire_and_forget(record_audit({
        "timestamp": ts,
        "narrative": request.narrative,
        "context": request.context,
        "raw_narrative_score": specialist_output.raw_narrative_score,
        "sovereign_risk_score": score,
        "primary_threat_vector": specialist_output.primary_threat_vector,
        "grounding_strength": grounding,
        "grounding_note": specialist_output.grounding_note,
        "action_disposition": disposition,
        "alert_dispatched": alert_dispatched,
        "sources": sources,
        "model_used": GEMINI_MODEL,
    }))

    logger.info(
        "Finalized | score=%.2f | grounding=%s | disposition=%s | dispatched=%s",
        score, grounding, disposition, alert_dispatched,
    )

    return EvaluationResponse(
        assessment=assessment,
        model_used=GEMINI_MODEL,
        evaluation_timestamp=ts,
        alert_dispatched=alert_dispatched,
    )


async def _orchestrate(request: EvaluationRequest) -> EvaluationResponse:
    """Shared handler for both route aliases."""
    logger.info(
        "Evaluation requested | narrative_chars=%d | context=%r",
        len(request.narrative),
        request.context,
    )

    async with _PIPELINE_SEMAPHORE:
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

        return _finalize_response(request, specialist_output, audit_findings, sources)


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
    async with _PIPELINE_SEMAPHORE:
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

        result = _finalize_response(request, specialist_output, audit_findings, sources)
        await on_event("decision", {
            "disposition": result.assessment.action_disposition,
            "dispatched": result.alert_dispatched,
        })
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
# Audit history — reads the persisted assessment trail (governance / provenance)
# ---------------------------------------------------------------------------

async def _read_audit_history(size: int = 20) -> list[dict]:
    endpoint = os.environ.get("ELASTIC_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("ELASTIC_API_KEY", "")
    if not endpoint or not api_key:
        return []
    body = {
        "size": max(1, min(size, 100)),
        "sort": [{"timestamp": {"order": "desc", "unmapped_type": "date"}}],
        "_source": [
            "timestamp", "narrative", "context", "raw_narrative_score",
            "sovereign_risk_score", "primary_threat_vector", "grounding_strength",
            "action_disposition", "alert_dispatched",
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                f"{endpoint}/{_AUDIT_INDEX}/_search",
                json=body,
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                return []  # no assessments recorded yet — index not created
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            return [h.get("_source", {}) for h in hits]
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Audit history read failed: %s", exc)
        return []


@app.get(
    "/api/v1/history",
    operation_id="evaluation_history",
    summary="Recent Assessment Audit Trail",
    description=(
        "Returns the most recent assessments from the searchable Elasticsearch "
        "audit trail — narrative, scores, grounding strength, and autonomy disposition. "
        "Every evaluation is persisted for governance and provenance."
    ),
    tags=["Risk Intelligence"],
)
async def evaluation_history(size: int = 20) -> dict:
    records = await _read_audit_history(size)
    return {"count": len(records), "assessments": records}


# ---------------------------------------------------------------------------
# Article fetch — extracts plain text from a URL for direct pipeline ingestion
# ---------------------------------------------------------------------------

class FetchArticleRequest(BaseModel):
    url: str = Field(..., description="Public URL of a news article or financial report to ingest.")


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_MAX_ARTICLE_CHARS = 4000  # keep context window reasonable


@app.post(
    "/api/v1/fetch-article",
    operation_id="fetch_article",
    summary="Fetch and extract plain text from a URL",
    description=(
        "Fetches a public URL (news article, report, press release) and returns cleaned "
        "plain text suitable for direct submission to /api/v1/evaluate. "
        "Paywalled or JavaScript-rendered pages may return partial or empty text."
    ),
    tags=["Risk Intelligence"],
)
async def fetch_article(request: FetchArticleRequest) -> dict:
    from bs4 import BeautifulSoup

    if not _URL_RE.match(request.url):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={"User-Agent": "MacroPulse/5 (+https://github.com/chiachengjone/MacroPulse)"},
        ) as client:
            resp = await client.get(request.url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"HTTP {exc.response.status_code} fetching URL")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Network error fetching URL: {exc}")

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove boilerplate nodes
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()

    title = (soup.find("title") or soup.find("h1") or soup.new_tag("t"))
    title_text = title.get_text(strip=True) if title else ""

    # Prefer article/main body; fall back to full body
    body = soup.find("article") or soup.find("main") or soup.find("body")
    raw_text = body.get_text(separator=" ", strip=True) if body else soup.get_text(separator=" ", strip=True)

    # Collapse whitespace
    clean = re.sub(r"\s{2,}", " ", raw_text).strip()
    truncated = len(clean) > _MAX_ARTICLE_CHARS
    text = clean[:_MAX_ARTICLE_CHARS] + ("…" if truncated else "")

    if len(text) < 100:
        raise HTTPException(
            status_code=422,
            detail="Could not extract usable text from this URL. Try pasting the article excerpt directly.",
        )

    # Summarise the raw text into a clean financial narrative using Gemini
    summary_prompt = (
        f"You are a financial analyst. Read the article below and write a concise 2–3 paragraph "
        f"summary (150–250 words) that captures the key macro-economic facts, figures, named "
        f"entities (countries, institutions, currencies), and the core risk signals. "
        f"Preserve specific numbers, percentages, and dates. Do not editorialize or add opinion. "
        f"Write in plain prose — no bullet points, no headers.\n\nArticle title: {title_text}\n\n{text}"
    )
    try:
        summary_resp = await _generate_with_retry(
            model=GEMINI_MODEL,
            contents=summary_prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        summary = (summary_resp.text or "").strip()
    except Exception as exc:
        logger.warning("Article summarisation failed, falling back to raw text: %s", exc)
        summary = text  # graceful fallback — still usable

    return {
        "title": title_text,
        "summary": summary,
        "url": str(request.url),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Alert Subscriptions & Live Market Briefings
# ---------------------------------------------------------------------------
# A persistent subscriber-profile store (Elasticsearch index
# `macro-alert-subscribers`) plus a monitoring agent that pulls LIVE market data
# (keyless FX + FRED macro indicators) for the subscriber's tracked countries and
# metrics, asks Gemini to write a concise briefing, and emails it via Resend —
# automatically on the subscriber's interval (Cloud Scheduler sweep) or on demand.
# All exposed as FastAPI endpoints, so fastapi-mcp surfaces them as MCP tools.
# ---------------------------------------------------------------------------
_SUBSCRIBERS_INDEX = "macro-alert-subscribers"

# Catalog of trackable sovereigns: ISO-3 code → display name + currency.
# Currency codes drive the keyless FX market feed below.
_COUNTRY_CATALOG: list[dict] = [
    {"code": "USA", "name": "United States", "currency": "USD"},
    {"code": "DEU", "name": "Germany (Euro area)", "currency": "EUR"},
    {"code": "GBR", "name": "United Kingdom", "currency": "GBP"},
    {"code": "JPN", "name": "Japan", "currency": "JPY"},
    {"code": "TUR", "name": "Türkiye", "currency": "TRY"},
    {"code": "ARG", "name": "Argentina", "currency": "ARS"},
    {"code": "EGY", "name": "Egypt", "currency": "EGP"},
    {"code": "PAK", "name": "Pakistan", "currency": "PKR"},
    {"code": "SGP", "name": "Singapore", "currency": "SGD"},
    {"code": "CHN", "name": "China", "currency": "CNY"},
    {"code": "IND", "name": "India", "currency": "INR"},
    {"code": "BRA", "name": "Brazil", "currency": "BRL"},
]
_CODE_TO_CCY = {c["code"]: c["currency"] for c in _COUNTRY_CATALOG}
_CODE_TO_NAME = {c["code"]: c["name"] for c in _COUNTRY_CATALOG}

_ALERT_METRICS = [
    "Inflation", "Interest Rates", "FX / Currency",
    "Sovereign Debt", "Current Account", "Banking Stability",
]

# Fallback FX snapshot (USD base) used when the live feed is unreachable, so the
# monitoring agent degrades gracefully instead of crashing the workflow.
_FALLBACK_FX = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 157.0, "TRY": 43.1,
    "ARS": 1478.0, "EGP": 49.5, "PKR": 278.0, "SGD": 1.35, "CNY": 7.25,
    "INR": 83.4, "BRL": 5.45,
}

_VALID_INTERVALS = {15, 30, 60, 120}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BRIEFER_PERSONA = (
    "You are a markets intelligence analyst writing a concise LIVE briefing for a client who "
    "tracks specific sovereigns. You are given a live market-data snapshot: per-sovereign FX "
    "rates, plus GLOBAL/US backdrop indicators. RULES: (1) You MUST address EACH of the client's "
    "selected sovereigns by name — every one of them gets at least one specific sentence about "
    "its currency level and macro implications. (2) The macro indicators are GLOBAL BACKDROP "
    "context only — do NOT make the briefing about the United States (or any country the client "
    "did not select); use them only to frame the selected sovereigns. (3) If a sovereign's only "
    "live data is its FX rate, still discuss its currency and likely sovereign-risk implications. "
    "Lead with one line prefixed 'HEADLINE: '. Be specific and quantitative, cite the live "
    "numbers, do NOT fabricate data not in the snapshot, and keep it plain prose (no markdown)."
)

# ── Live macro indicators via FRED — the macro alert is LIMITED to what FRED
# actually provides, so every selectable filter is backed by real data.
# Each country lists: ISO-3 code, display name, OECD 10Y-yield ISO-2 (or None),
# and whether FRED carries its CPI. Verified to return data (yields fresh ~2026;
# CPI lagged ~1yr — the 'as of' date is shown in the briefing). ──
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_FRED_COUNTRIES: list[dict] = [
    {"code": "USA", "name": "United States", "y": "US", "cpi": True},
    {"code": "DEU", "name": "Germany", "y": "DE", "cpi": True},
    {"code": "GBR", "name": "United Kingdom", "y": "GB", "cpi": True},
    {"code": "FRA", "name": "France", "y": "FR", "cpi": True},
    {"code": "ITA", "name": "Italy", "y": "IT", "cpi": True},
    {"code": "ESP", "name": "Spain", "y": "ES", "cpi": True},
    {"code": "CAN", "name": "Canada", "y": "CA", "cpi": True},
    {"code": "JPN", "name": "Japan", "y": "JP", "cpi": False},
    {"code": "AUS", "name": "Australia", "y": "AU", "cpi": False},
    {"code": "KOR", "name": "South Korea", "y": "KR", "cpi": False},
    {"code": "MEX", "name": "Mexico", "y": "MX", "cpi": False},
    {"code": "BRA", "name": "Brazil", "y": None, "cpi": True},
    {"code": "IND", "name": "India", "y": None, "cpi": True},
    {"code": "TUR", "name": "Türkiye", "y": None, "cpi": True},
    {"code": "CHN", "name": "China", "y": None, "cpi": True},
    {"code": "ZAF", "name": "South Africa", "y": None, "cpi": True},
]
_FRED_BY_CODE = {c["code"]: c for c in _FRED_COUNTRIES}
_FRED_NAME = {c["code"]: c["name"] for c in _FRED_COUNTRIES}
# Metrics the alert supports — limited to those FRED answers per-country.
_FRED_METRICS = ["Interest Rates", "Inflation"]
_FRED_VIX = ("VIXCLS", "CBOE Volatility Index (VIX)", "index", None)


def _es_credentials() -> tuple[str, str]:
    endpoint = os.environ.get("ELASTIC_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("ELASTIC_API_KEY", "")
    if not endpoint or not api_key:
        raise HTTPException(status_code=503, detail="Elasticsearch is not configured.")
    return endpoint, api_key


async def _get_subscriber(email: str) -> Optional[dict]:
    """Fetch a subscriber profile from Elasticsearch by document id (email)."""
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                f"{endpoint}/{_SUBSCRIBERS_INDEX}/_doc/{quote(email, safe='')}",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("_source")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Subscriber fetch failed: %s", exc)
        return None


async def _list_active_subscribers(limit: int = 100) -> list[dict]:
    """Return all active subscriber profiles (used by the scheduled sweep)."""
    endpoint, api_key = _es_credentials()
    body = {"size": max(1, min(limit, 500)), "query": {"term": {"is_active": True}}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                f"{endpoint}/{_SUBSCRIBERS_INDEX}/_search",
                json=body,
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                return []  # index not created yet — no subscribers
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            return [h["_source"] for h in hits if h.get("_source")]
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Subscriber list failed: %s", exc)
        return []


async def _touch_subscriber_last_run(email: str, ts: str) -> None:
    """Stamp last_run_at on a subscriber doc so the sweep can compute due-ness."""
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                f"{endpoint}/{_SUBSCRIBERS_INDEX}/_update/{quote(email, safe='')}",
                json={"doc": {"last_run_at": ts}},
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("Failed to stamp last_run_at for %s: %s", email, exc)


async def _fetch_live_market_feed(countries: list[str]) -> dict:
    """
    Pull a live FX snapshot (USD base) from a keyless public source for the
    requested sovereigns. Falls back to a cached static snapshot on any failure
    so the monitoring workflow never crashes.
    """
    codes = [c.upper() for c in countries if c.upper() in _CODE_TO_CCY] or ["USA"]
    feed: dict = {
        "base": "USD",
        "source": "open.er-api.com",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "degraded": False,
        "indicators": [],
    }
    rates: dict = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            resp.raise_for_status()
            payload = resp.json()
            rates = payload.get("rates", {}) or {}
            if payload.get("time_last_update_utc"):
                feed["as_of"] = payload["time_last_update_utc"]
        if not rates:
            raise ValueError("empty rates payload")
    except Exception as exc:
        logger.warning("Live market feed failed (%s) — using cached fallback snapshot", exc)
        feed["degraded"] = True
        feed["source"] = "fallback-cache"
        rates = _FALLBACK_FX

    for code in codes:
        ccy = _CODE_TO_CCY[code]
        if ccy == "USD":
            # USD is the feed's base — a USD/USD rate of 1.0 carries no information.
            # The US is represented by its FRED indicators (rates, yields, CPI) instead.
            continue
        rate = rates.get(ccy) or _FALLBACK_FX.get(ccy)
        feed["indicators"].append({
            "country": code,
            "country_name": _CODE_TO_NAME.get(code, code),
            "currency": ccy,
            "local_per_usd": round(rate, 4) if rate else None,
        })
    return feed


async def _fetch_one_fred(client: httpx.AsyncClient, spec: tuple) -> Optional[dict]:
    """Fetch the latest observation for one FRED series. Returns None on any failure."""
    series_id, label, unit, transform = spec
    params = {
        "series_id": series_id,
        "api_key": os.environ["FRED_API_KEY"],
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }
    if transform:
        params["units"] = transform
    try:
        resp = await client.get(_FRED_BASE, params=params)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        if not obs or obs[0].get("value") in (None, "", "."):
            return None
        return {
            "series_id": series_id,
            "label": label,
            "unit": unit,
            "value": round(float(obs[0]["value"]), 4),
            "as_of": obs[0]["date"],
        }
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError) as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return None


async def _fetch_fred_indicators(metrics: list[str], countries: list[str]) -> list[dict]:
    """
    Build the FRED series list strictly from the selected countries × metrics,
    so the briefing only ever reports data FRED actually has. Adds US policy/curve
    extras when the US is tracked, and VIX as a single global risk gauge.
    """
    if not os.environ.get("FRED_API_KEY"):
        return []
    codes = [c.upper() for c in countries if c.upper() in _FRED_BY_CODE] or ["USA"]
    specs: dict[str, tuple] = {}
    for code in codes:
        c = _FRED_BY_CODE[code]
        name = c["name"]
        if "Interest Rates" in metrics and c["y"]:
            sid = f"IRLTLT01{c['y']}M156N"
            specs[sid] = (sid, f"{name} 10Y Govt Bond Yield", "%", None)
        if "Inflation" in metrics and c["cpi"]:
            sid = f"{code}CPIALLMINMEI"
            specs[sid] = (sid, f"{name} CPI Inflation (YoY)", "%", "pc1")
    # US-specific policy + curve signals when the US is a tracked sovereign.
    if "USA" in codes and "Interest Rates" in metrics:
        specs["FEDFUNDS"] = ("FEDFUNDS", "US Fed Funds Rate", "%", None)
        specs["T10Y2Y"] = ("T10Y2Y", "US 10Y–2Y Curve Spread", "%", None)
    # One global risk gauge as backdrop.
    specs.setdefault(_FRED_VIX[0], _FRED_VIX)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            results = await asyncio.gather(*[_fetch_one_fred(client, s) for s in specs.values()])
        return [r for r in results if r]
    except Exception as exc:
        logger.error("FRED indicator batch failed: %s", exc)
        return []


async def _fetch_live_data(countries: list[str], metrics: list[str]) -> dict:
    """FRED-only live snapshot — the alert is limited to what FRED provides."""
    macro = await _fetch_fred_indicators(metrics, countries)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "macro_indicators": macro,
    }


async def _send_email_resend(to: str, subject: str, text: str, html: str) -> dict:
    """Send the briefing via Resend. No-ops gracefully when RESEND_API_KEY is absent."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return {"sent": False, "status": "RESEND_API_KEY not configured — email skipped (logged only)."}
    sender = os.environ.get("ALERT_EMAIL_FROM", "MacroPulse <onboarding@resend.dev>")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"from": sender, "to": [to], "subject": subject, "text": text, "html": html},
            )
            if resp.status_code >= 400:
                return {"sent": False, "status": f"Resend error {resp.status_code}: {resp.text[:200]}"}
            return {"sent": True, "status": "delivered", "id": resp.json().get("id")}
    except httpx.RequestError as exc:
        return {"sent": False, "status": f"network error: {exc}"}


def _briefing_html(report: str, live: dict, country_names: list[str], metrics: list[str]) -> str:
    """Render the briefing + FRED data table as a simple, email-safe HTML document."""
    paras = "".join(
        f"<p style='margin:0 0 12px;line-height:1.5'>{p.strip()}</p>"
        for p in report.split("\n") if p.strip()
    )
    macro_rows = "".join(
        f"<tr><td style='padding:3px 16px 3px 0;color:#444'>{m['label']}</td>"
        f"<td style='text-align:right;font-variant-numeric:tabular-nums'>"
        f"{m['value']}{'%' if m['unit'] == '%' else ' ' + m['unit']}"
        f"<span style='color:#999'> · {m['as_of']}</span></td></tr>"
        for m in live["macro_indicators"]
    )
    macro_block = (
        f"<h4 style='margin:18px 0 6px'>Live macro indicators (FRED)</h4>"
        f"<table style='font-size:13px;border-collapse:collapse'>{macro_rows}</table>"
        if macro_rows else ""
    )
    return (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;color:#111'>"
        "<h2 style='margin:0 0 4px'>MacroPulse Live Briefing</h2>"
        f"<p style='color:#666;margin:0 0 16px;font-size:13px'>Tracking {', '.join(country_names)} · "
        f"{', '.join(metrics)}</p>"
        f"{paras}"
        f"{macro_block}"
        "<p style='color:#999;font-size:11px;margin-top:20px'>Sent by MacroPulse · "
        "data: FRED (St. Louis Fed)</p>"
        "</div>"
    )


# ── Pydantic request models ────────────────────────────────────────────────

class AlertSubscriberRequest(BaseModel):
    email: str = Field(..., description="Subscriber email — normalised lowercase, used as the document id.")
    countries: list[str] = Field(default_factory=list, description="ISO-3 country codes to monitor (e.g. ['USA','TUR']).")
    metrics: list[str] = Field(default_factory=list, description="Macro indicators to monitor (e.g. ['Inflation']).")
    interval_minutes: int = Field(60, description="Background scan cadence in minutes (15/30/60/120).")
    sensitivity_threshold: str = Field("high", description="Alert sensitivity tier.")
    is_active: bool = Field(True, description="Whether email alerts are enabled (scheduled + manual sends).")


class MarketFeedRequest(BaseModel):
    countries: list[str] = Field(default_factory=list, description="ISO-3 country codes to fetch live rates for.")


class AlertRunRequest(BaseModel):
    email: str = Field(..., description="Email of the subscriber whose alert run to execute now.")


@app.get(
    "/api/v1/alerts/catalog",
    operation_id="alert_catalog",
    summary="Trackable sovereigns and metrics",
    tags=["Alert Monitoring"],
)
async def alert_catalog() -> dict:
    """Catalog for the subscription UI — limited to sovereigns/metrics FRED covers."""
    return {
        "countries": [{"code": c["code"], "name": c["name"]} for c in _FRED_COUNTRIES],
        "metrics": _FRED_METRICS,
        "intervals": sorted(_VALID_INTERVALS),
    }


@app.post(
    "/api/v1/alerts/subscribe",
    operation_id="register_alert_subscriber",
    summary="Register or update an alert subscriber",
    description=(
        "Upserts a subscriber profile into the `macro-alert-subscribers` Elasticsearch "
        "index, keyed by lowercased email (guaranteeing one profile per account)."
    ),
    tags=["Alert Monitoring"],
)
async def register_alert_subscriber(request: AlertSubscriberRequest) -> dict:
    email = request.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address.")

    interval = request.interval_minutes if request.interval_minutes in _VALID_INTERVALS else 60
    countries = [c.upper() for c in request.countries if c.upper() in _FRED_BY_CODE]
    metrics = [m for m in request.metrics if m in _FRED_METRICS]

    doc = {
        "email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_active": request.is_active,
        "alert_triggers": {
            "countries": countries,
            "metrics": metrics,
            "sensitivity_threshold": request.sensitivity_threshold or "high",
            "interval_minutes": interval,
        },
    }

    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            # Upsert by id (email) — PUT replaces the whole doc, so no duplicates.
            resp = await client.put(
                f"{endpoint}/{_SUBSCRIBERS_INDEX}/_doc/{quote(email, safe='')}",
                json=doc,
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Subscriber upsert failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Failed to save subscriber: {exc}")

    logger.info(
        "Subscriber synced | email=%s | countries=%s | metrics=%s | interval=%dm",
        email, countries, metrics, interval,
    )
    return {
        "status": "synchronized",
        "message": (
            f"Alert profile for {email} saved — monitoring {len(countries)} "
            f"sovereign(s) every {interval} minutes."
        ),
        "subscriber": doc,
    }


@app.get(
    "/api/v1/alerts/subscriber/{email}",
    operation_id="get_alert_subscriber",
    summary="Load an existing subscriber profile",
    tags=["Alert Monitoring"],
)
async def get_alert_subscriber(email: str) -> dict:
    profile = await _get_subscriber(email.strip().lower())
    if not profile:
        raise HTTPException(status_code=404, detail="No subscriber profile found for that email.")
    return profile


@app.post(
    "/api/v1/alerts/market-feed",
    operation_id="fetch_live_market_feed",
    summary="Fetch a live market-data snapshot",
    description="Returns a live FX snapshot (USD base) for the requested sovereigns from a keyless public source, with a cached fallback.",
    tags=["Alert Monitoring"],
)
async def fetch_live_market_feed(request: MarketFeedRequest) -> dict:
    return await _fetch_live_market_feed(request.countries)


async def _run_alert_for_profile(email: str, profile: dict, force_send: bool = False) -> dict:
    """
    Live-briefing agent, shared by the manual trigger and the scheduled sweep:
    pull live market data (FX + FRED) for the subscriber's countries/metrics, ask
    Gemini for a concise briefing, and email it. No knowledge-base retrieval.
    """
    triggers = profile.get("alert_triggers", {})
    countries = triggers.get("countries") or ["USA"]
    metrics = triggers.get("metrics") or ["Interest Rates", "Inflation"]
    country_names = [_FRED_NAME.get(c, c) for c in countries]

    # 1 — pull the live FRED snapshot (the alert is limited to what FRED provides).
    live = await _fetch_live_data(countries, metrics)
    macro_lines = [
        f"- {m['label']}: {m['value']}{'%' if m['unit'] == '%' else ' ' + m['unit']} (as of {m['as_of']})"
        for m in live["macro_indicators"]
    ]
    data_block = (
        "LIVE FRED INDICATORS for the selected sovereigns/metrics:\n" + "\n".join(macro_lines)
        if macro_lines else "No live FRED indicators available for the current selection."
    )

    # 2 — Gemini writes the briefing strictly from the FRED data provided.
    prompt = "\n\n".join([
        f"CLIENT'S SELECTED SOVEREIGNS: {', '.join(country_names)}\n"
        f"CLIENT'S METRICS OF INTEREST: {', '.join(metrics)}",
        data_block,
        f"Write the live briefing now, STRICTLY from the FRED indicators above. Devote at least "
        f"one specific sentence to EACH selected sovereign ({', '.join(country_names)}) for which "
        f"data is shown, citing its actual figure. Do not discuss countries with no data shown, "
        f"and do not invent numbers.",
    ])
    try:
        async with _PIPELINE_SEMAPHORE:
            resp = await _generate_with_retry(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=BRIEFER_PERSONA,
                    temperature=0.3,
                ),
            )
        report = (resp.text or "").strip()
    except Exception as exc:
        logger.error("Briefer LLM failed: %s", exc)
        report = (
            "HEADLINE: Briefing unavailable — model could not be reached.\n"
            "The live market data was still gathered and is included below."
        )

    # 3 — email the briefing. force_send (manual trigger) always sends regardless of is_active.
    ts = datetime.now(timezone.utc).isoformat()
    if force_send or profile.get("is_active", True):
        subject = f"MacroPulse Briefing — {', '.join(country_names)}"
        html = _briefing_html(report, live, country_names, metrics)
        email_result = await _send_email_resend(email, subject, report, html)
    else:
        email_result = {"sent": False, "status": "email alerts are turned off"}

    logger.info(
        "📢 BRIEFING | %s | sovereigns=%s | fred=%d | email_sent=%s (%s)",
        email, countries, len(live["macro_indicators"]),
        email_result.get("sent"), email_result.get("status"),
    )
    await _touch_subscriber_last_run(email, ts)

    return {
        "email": email,
        "executed_at": ts,
        "monitored_countries": country_names,
        "monitored_metrics": metrics,
        "live_data": live,
        "report": report,
        "email_sent": email_result.get("sent", False),
        "email_status": email_result.get("status", ""),
    }


@app.post(
    "/api/v1/alerts/run",
    operation_id="execute_immediate_alert_run",
    summary="Run a subscriber's live briefing now",
    description=(
        "Bypasses the background interval: loads the subscriber profile, pulls live market "
        "data (FX + FRED) for their tracked countries/metrics, generates a Gemini briefing, "
        "emails it via Resend, and returns the report."
    ),
    tags=["Alert Monitoring"],
)
async def execute_immediate_alert_run(request: AlertRunRequest) -> dict:
    email = request.email.strip().lower()
    # Step 1 — load the subscriber's profile.
    profile = await _get_subscriber(email)
    if not profile:
        raise HTTPException(status_code=404, detail="No subscriber profile found — save your preferences first.")
    return await _run_alert_for_profile(email, profile, force_send=True)


def _is_due(profile: dict, now: datetime) -> bool:
    """True if the subscriber's interval has elapsed since its last run."""
    interval = profile.get("alert_triggers", {}).get("interval_minutes", 60)
    if not interval:
        return False  # interval=0 means Never — skip in sweep
    last = profile.get("last_run_at")
    if not last:
        return True  # never run — due immediately
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    return (now - last_dt).total_seconds() >= interval * 60


@app.post(
    "/api/v1/alerts/sweep",
    operation_id="run_alert_sweep",
    summary="Scheduled sweep — run all due subscribers",
    description=(
        "Intended for Cloud Scheduler. Loads every active subscriber, runs the monitoring "
        "agent for those whose interval has elapsed, and stamps last_run_at. Protected by the "
        "X-Sweep-Token header when ALERT_SWEEP_TOKEN is configured."
    ),
    tags=["Alert Monitoring"],
)
async def run_alert_sweep(x_sweep_token: Optional[str] = Header(default=None)) -> dict:
    expected = os.environ.get("ALERT_SWEEP_TOKEN", "")
    if expected and x_sweep_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing sweep token.")

    now = datetime.now(timezone.utc)
    subscribers = await _list_active_subscribers()
    due = [s for s in subscribers if s.get("email") and _is_due(s, now)]

    ran: list[str] = []
    for profile in due:
        email = profile["email"]
        try:
            await _run_alert_for_profile(email, profile)
            ran.append(email)
        except Exception as exc:
            logger.error("Sweep run failed for %s: %s", email, exc)

    logger.info("Alert sweep | active=%d | due=%d | ran=%d", len(subscribers), len(due), len(ran))
    return {
        "swept_at": now.isoformat(),
        "active_subscribers": len(subscribers),
        "due": len(due),
        "ran": ran,
    }


# ---------------------------------------------------------------------------
# Document-Bank Curator — autonomous, trusted-source knowledge-base maintenance
# ---------------------------------------------------------------------------
# A scheduled agent that discovers authoritative PDFs from a whitelist of
# institutions (via Gemini + Google Search grounding), ingests them through the
# EXISTING `macropulse-ingest` ES pipeline (Tika + e5 embeddings), and trims by
# relevance only when the bank exceeds its cap. Ingestion is the ML-tier risk on
# the trial deployment (a 57-doc bulk run once caused a multi-hour outage), so
# every run does an ML-health PREFLIGHT and ingests at most a few PDFs, one at a
# time with verification and pacing — never a bulk burst. The 2000-doc cap is a
# ceiling the agent grows toward gradually, not a target to fill at once.
# ---------------------------------------------------------------------------
_BANK_INDEX = "macro-pulse-files"
_BANK_PIPELINE = "macropulse-ingest"
_BANK_CONFIG_INDEX = "macro-bank-config"
_BANK_ARCHIVE_INDEX = "macro-bank-archive"
_BANK_CONFIG_ID = "default"
_EMBED_MODEL_ID = ".multilingual-e5-small-elasticsearch"

_BANK_MAX_DOCS_DEFAULT = 1000          # ceiling; agent grows toward comprehensive coverage
_BANK_PER_RUN_ADD_CAP = 5              # paced growth — never a bulk spike
_BANK_MAX_TRIM_PER_RUN = 20            # bound destructive deletes per run
_BANK_DEFAULT_INTERVAL_HOURS = 24
_BANK_VALID_INTERVALS = [6, 12, 24, 48, 168]
_BANK_MAX_PDF_BYTES = 25 * 1024 * 1024

_BANK_TOPICS = [
    "Sovereign debt", "FX / currency crises", "Banking stability",
    "Inflation", "Monetary policy", "Trade / current account",
    "Capital flows", "Fiscal policy", "Financial stability",
]

# Whitelist of trusted institutional domains for discovery + ingest validation.
# Official IFIs/multilaterals, ~35 central banks, US fiscal/stats, and official research.
_BANK_ALLOWED_DOMAINS = (
    # ── Multilateral / IFIs / official research ──
    "imf.org", "bis.org", "worldbank.org", "openknowledge.worldbank.org",
    "documents.worldbank.org", "oecd.org", "oecd-ilibrary.org", "ec.europa.eu",
    "esm.europa.eu", "eba.europa.eu", "esrb.europa.eu", "adb.org", "afdb.org",
    "iadb.org", "ebrd.com", "aiib.org", "wto.org", "unctad.org", "un.org",
    "fsb.org", "nber.org", "cepr.org",
    # ── Central banks ──
    "ecb.europa.eu", "federalreserve.gov", "newyorkfed.org", "stlouisfed.org",
    "kansascityfed.org", "bankofengland.co.uk", "bundesbank.de", "banque-france.fr",
    "bancaditalia.it", "bde.es", "dnb.nl", "nbb.be", "snb.ch", "riksbank.se",
    "norges-bank.no", "bankofcanada.ca", "rba.gov.au", "rbnz.govt.nz", "boj.or.jp",
    "bok.or.kr", "pbc.gov.cn", "rbi.org.in", "bcb.gov.br", "banxico.org.mx",
    "bcra.gob.ar", "bcentral.cl", "banrep.gov.co", "bcrp.gob.pe", "tcmb.gov.tr",
    "sbp.org.pk", "cbe.org.eg", "sarb.co.za", "bnm.gov.my", "bot.or.th",
    "bsp.gov.ph", "bi.go.id", "cbn.gov.ng", "centralbank.go.ke", "mas.gov.sg",
    "hkma.gov.hk", "centralbank.ie", "bportugal.pt", "bankofgreece.gr",
    # ── US fiscal / statistical agencies ──
    "treasury.gov", "cbo.gov", "bls.gov", "bea.gov",
)

# Stable institutional listing pages whose links we harvest for REAL publication
# URLs (vs. asking an LLM to guess URLs, which fabricates 404s). Each linked item
# is either a direct .pdf or a publication page from which we extract the PDF.
# Prefer RSS/Atom feeds and classic static HTML — they expose REAL individual
# publication links (JS-rendered hubs like modern IMF/NBER pages do not).
_BANK_SOURCE_PAGES = (
    ("NBER — new working papers", "https://www.nber.org/rss/new.xml"),
    ("NBER — working papers", "https://www2.nber.org/rss/new.xml"),
    ("BIS — working papers", "https://www.bis.org/list/wpapers/index.rss"),
    ("BIS — working papers", "https://www.bis.org/wppubl.htm"),
    ("BIS — quarterly review", "https://www.bis.org/quarterlyreviews/index.htm"),
    ("BIS — all publications", "https://www.bis.org/publ/index.htm"),
    ("BIS — speeches/other", "https://www.bis.org/list/bisbulletins/index.rss"),
    ("IMF — working papers", "https://www.imf.org/en/Publications/WP"),
    ("IMF — country reports", "https://www.imf.org/en/Publications/CR"),
    ("ECB — working papers RSS", "https://www.ecb.europa.eu/rss/wppub.html"),
)
# href substrings that indicate an individual publication (page or PDF).
_BANK_PUB_HINTS = (
    "/publ/", "/papers/", "/publications/", "/publication/", "/media/files/",
    "/pub/", "/work", "/document", "/cr/", "/wp/", "/weo/", "/gfsr",
)
# A real individual publication URL has a year (20xx) or a numeric doc id —
# hubs/nav links ("World Economic Outlook") do not.
_BANK_ID_RE = re.compile(r"(20\d{2}|/w?\d{3,})")


def _bank_default_config() -> dict:
    return {
        "interval_hours": _BANK_DEFAULT_INTERVAL_HOURS,
        "focus_countries": [],
        "focus_topics": [],
        "max_docs": _BANK_MAX_DOCS_DEFAULT,
        "is_active": True,
        "last_run_at": None,
    }


async def _get_bank_config() -> dict:
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                f"{endpoint}/{_BANK_CONFIG_INDEX}/_doc/{_BANK_CONFIG_ID}",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
            if resp.status_code == 404:
                return _bank_default_config()
            resp.raise_for_status()
            return {**_bank_default_config(), **(resp.json().get("_source") or {})}
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Bank config fetch failed: %s", exc)
        return _bank_default_config()


async def _save_bank_config(cfg: dict) -> None:
    endpoint, api_key = _es_credentials()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.put(
            f"{endpoint}/{_BANK_CONFIG_INDEX}/_doc/{_BANK_CONFIG_ID}",
            json=cfg,
            headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()


async def _ml_embedding_healthy() -> tuple[bool, str]:
    """Preflight: is the e5 model deployed with a live ML node? Gate every ingest on this."""
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            resp = await client.get(
                f"{endpoint}/_ml/trained_models/{_EMBED_MODEL_ID}/_stats",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
            if resp.status_code >= 400:
                return False, f"ML stats HTTP {resp.status_code}: {resp.text[:120]}"
            for s in resp.json().get("trained_model_stats", []):
                dep = s.get("deployment_stats") or {}
                if dep.get("state") == "started":
                    return True, "e5 deployment started"
            return False, "e5 deployment not started (no ML node?)"
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        return False, f"ML stats error: {exc}"


async def _bank_count() -> int:
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                f"{endpoint}/{_BANK_INDEX}/_count",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
            resp.raise_for_status()
            return int(resp.json().get("count", 0))
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
        logger.error("Bank count failed: %s", exc)
        return 0


async def _list_bank_docs(size: int = 2000) -> list[dict]:
    endpoint, api_key = _es_credentials()
    body = {
        "size": max(1, min(size, 2000)),
        "_source": ["attachment.title", "curator_added", "source_url", "ingested_at"],
        "query": {"match_all": {}},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                f"{endpoint}/{_BANK_INDEX}/_search",
                json=body,
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            out = []
            for h in resp.json().get("hits", {}).get("hits", []):
                src = h.get("_source", {})
                out.append({
                    "id": h.get("_id"),
                    "title": (src.get("attachment", {}) or {}).get("title") or h.get("_id"),
                    "curator_added": bool(src.get("curator_added")),
                    "source_url": src.get("source_url"),
                    "ingested_at": src.get("ingested_at"),
                })
            return out
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Bank list failed: %s", exc)
        return []


def _domain_allowed(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return False
    return any(netloc == d or netloc.endswith("." + d) for d in _BANK_ALLOWED_DOMAINS)


def _parse_json_array(text: str) -> list[dict]:
    """Extract a JSON array from a model response that may be fenced or prose-wrapped."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rstrip("`").rstrip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(t[start:end + 1])
        return [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        return []


async def _harvest_real_candidates(limit_per_page: int = 12) -> list[dict]:
    """
    Fetch the curated institutional listing pages and extract REAL links to
    publications/PDFs on whitelisted domains. No LLM URL generation — every URL
    here actually exists on the page.
    """
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup

    pool: list[dict] = []
    seen: set = set()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), follow_redirects=True,
        headers={"User-Agent": "MacroPulse/5 (+https://github.com/chiachengjone/MacroPulse)"},
    ) as client:
        for inst, page in _BANK_SOURCE_PAGES:
            try:
                r = await client.get(page)
                r.raise_for_status()
            except Exception as exc:
                logger.warning("harvest: %s failed (%s)", page, exc)
                continue
            ctype = r.headers.get("content-type", "").lower()
            is_feed = page.lower().endswith((".rss", ".xml")) or "xml" in ctype
            n = 0

            if is_feed:
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all(["item", "entry"]):
                    link_el = item.find("link")
                    href = ""
                    if link_el is not None:
                        href = (link_el.get("href") or link_el.text or "").strip()
                    title_el = item.find("title")
                    title = " ".join((title_el.text if title_el else "").split())[:160]
                    if not href or href in seen or not _domain_allowed(href) or len(title) < 15:
                        continue
                    seen.add(href)
                    pool.append({"institution": inst, "title": title, "url": href})
                    n += 1
                    if n >= limit_per_page:
                        break
            else:
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.find_all("a", href=True):
                    href = urljoin(str(r.url), a["href"].strip()).split("#")[0]
                    low = href.split("?")[0].lower()
                    if href in seen or not _domain_allowed(href):
                        continue
                    title = " ".join(a.get_text(" ", strip=True).split())[:160]
                    if len(title) < 15:
                        continue
                    is_pdf = low.endswith(".pdf")
                    looks_pub = any(h in low for h in _BANK_PUB_HINTS) and _BANK_ID_RE.search(low)
                    if is_pdf or looks_pub:
                        seen.add(href)
                        pool.append({"institution": inst, "title": title, "url": href})
                        n += 1
                        if n >= limit_per_page:
                            break
    logger.info("Harvested %d real candidate links from %d sources", len(pool), len(_BANK_SOURCE_PAGES))
    return pool


async def _discover_via_tavily(
    focus_countries: list[str], focus_topics: list[str], existing_titles: list[str], want: int
) -> Optional[list[dict]]:
    """
    Primary discovery: Tavily search API returns REAL result URLs (no LLM URL
    invention) restricted to the whitelisted institutional domains. Returns None
    if no API key is configured (caller falls back to listing-page harvest).
    """
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return None

    cnames = [_CODE_TO_NAME.get(c, c) for c in focus_countries]
    topics = focus_topics or _BANK_TOPICS
    # The selected filters ARE the daily search scope. Rotate deterministically
    # through them (a new window each hour) so every selected country/topic gets
    # searched across successive scheduled runs, rather than a random subset.
    slot = int(datetime.now(timezone.utc).timestamp() // 3600)

    def _rotate(items: list[str], k: int) -> list[str]:
        if not items:
            return []
        k = min(k, len(items))
        start = (slot * k) % len(items)
        return [items[(start + i) % len(items)] for i in range(k)]

    csel = _rotate(cnames, 2)
    tsel = _rotate(topics, 2)
    focus = " ".join(csel + tsel) or "sovereign risk macroeconomic stability"
    query = f"{focus} IMF OR BIS OR World Bank report OR working paper OR Article IV filetype:pdf"

    payload = {
        "api_key": key,
        "query": query,
        "max_results": min(20, want * 4),
        "search_depth": "advanced",
        "include_domains": list(_BANK_ALLOWED_DOMAINS)[:50],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            results = resp.json().get("results", []) or []
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Tavily search failed: %s", exc)
        return []

    seen = {t.lower() for t in existing_titles}
    seen_urls: set = set()
    out: list[dict] = []
    for r in results:
        url = (r.get("url") or "").strip()
        title = " ".join((r.get("title") or "").split())[:160]
        title = re.sub(r"^\[?pdf\]?\s*[-–:]?\s*", "", title, flags=re.IGNORECASE).strip()
        if not url or not title or url in seen_urls or title.lower() in seen:
            continue
        if not _domain_allowed(url):
            continue
        seen_urls.add(url)
        seen.add(title.lower())
        netloc = urlparse(url).netloc.replace("www.", "")
        out.append({"title": title, "url": url, "institution": netloc, "why": "tavily"})
    logger.info("Tavily discovery | query=%r | %d usable candidates", query, len(out))
    return out


async def _discover_bank_candidates(
    focus_countries: list[str], focus_topics: list[str], existing_titles: list[str], want: int
) -> list[dict]:
    """
    Discovery: Tavily search API (primary, real URLs) → otherwise fall back to
    harvesting institutional listing pages and ranking with Gemini.
    """
    tavily = await _discover_via_tavily(focus_countries, focus_topics, existing_titles, want)
    if tavily is not None:
        return tavily[: max(want, _BANK_PER_RUN_ADD_CAP)]

    pool = await _harvest_real_candidates()
    seen = {t.lower() for t in existing_titles}
    pool = [c for c in pool if c["title"].lower() not in seen]
    if not pool:
        return []

    cnames = [_CODE_TO_NAME.get(c, c) for c in focus_countries] or ["global / all major sovereigns"]
    topics = focus_topics or _BANK_TOPICS
    listing = "\n".join(f"{i}. [{c['institution']}] {c['title']}" for i, c in enumerate(pool[:120]))
    prompt = (
        f"From this list of REAL institutional publications, choose the {want} MOST relevant to add "
        f"to a macro-economics research library focused on\n"
        f"COUNTRIES: {', '.join(cnames)}\nTOPICS: {', '.join(topics)}\n\n"
        f"{listing}\n\n"
        f"Return ONLY a JSON array of the chosen integer indices (most relevant first), e.g. [2,5,9]."
    )
    try:
        async with _PIPELINE_SEMAPHORE:
            resp = await _generate_with_retry(
                model=GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1),
            )
        raw = (resp.text or "").strip()
        s, e = raw.find("["), raw.rfind("]")
        idxs = json.loads(raw[s:e + 1]) if s != -1 and e != -1 else []
    except Exception as exc:
        logger.error("Candidate ranking failed: %s", exc)
        idxs = []

    chosen = [pool[i] for i in idxs if isinstance(i, int) and 0 <= i < len(pool)]
    if not chosen:  # ranking failed → fall back to the first real candidates
        chosen = pool
    # de-dupe preserving order, cap to a sane discovery window
    out, seen_u = [], set()
    for c in chosen:
        if c["url"] not in seen_u:
            seen_u.add(c["url"])
            out.append({"title": c["title"], "url": c["url"], "institution": c["institution"], "why": "ranked"})
    return out[: max(want, _BANK_PER_RUN_ADD_CAP)]


def _bank_doc_id(url: str, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or url).lower()).strip("-")[:72] or "doc"
    return f"curated-{base}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


async def _extract_pdf_link(html: str, base_url: str) -> Optional[str]:
    """
    Find a document link on a landing page (whitelisted domain). Handles direct
    .pdf, IMF-style .ashx download links, and anchors whose text says PDF/Download.
    """
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None
    fallback = None
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not _domain_allowed(href):
            continue
        low = href.split("?")[0].lower()
        text = a.get_text(" ", strip=True).lower()
        if low.endswith(".pdf") or low.endswith(".ashx"):
            return href  # direct document link — best
        if fallback is None and ("download" in text or "pdf" in text) and "/media/" in low:
            fallback = href
    return fallback


async def _ingest_pdf(url: str, title: str, focus_tags: list[str]) -> dict:
    """
    Resolve a candidate to a real PDF and index it through the e5 pipeline.
    Follows redirects (grounding URLs are redirect links); if it lands on an HTML
    page, extracts the actual PDF link from it. Validates the FINAL domain against
    the whitelist. Verifies the embedding after indexing.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(35.0), follow_redirects=True,
            headers={"User-Agent": "MacroPulse/5 (+https://github.com/chiachengjone/MacroPulse)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            final_url = str(resp.url)
            # Landed on a page rather than a PDF → find the real PDF link on it.
            if "pdf" not in ctype and not final_url.split("?")[0].lower().endswith(".pdf"):
                if "html" in ctype or "text" in ctype:
                    pdf_link = await _extract_pdf_link(resp.text, final_url)
                    if not pdf_link:
                        return {"url": url, "ok": False, "error": "no PDF link found on page"}
                    resp = await client.get(pdf_link)
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "").lower()
                    final_url = str(resp.url)
                else:
                    return {"url": url, "ok": False, "error": f"not a PDF (content-type={ctype or 'unknown'})"}
            content = resp.content
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        return {"url": url, "ok": False, "error": f"download failed: {exc}"}

    if not _domain_allowed(final_url):
        return {"url": url, "ok": False, "error": f"final domain not whitelisted ({urlparse(final_url).netloc})"}
    if "pdf" not in ctype and not final_url.split("?")[0].lower().endswith(".pdf"):
        return {"url": url, "ok": False, "error": f"not a PDF (content-type={ctype or 'unknown'})"}
    if len(content) > _BANK_MAX_PDF_BYTES:
        return {"url": url, "ok": False, "error": f"too large ({len(content)} bytes)"}
    if len(content) < 1000:
        return {"url": url, "ok": False, "error": "suspiciously small file"}

    doc_id = _bank_doc_id(final_url, title)
    body = {
        "data": base64.b64encode(content).decode(),
        "doc_title": title,
        "curator_added": True,
        "source_url": final_url,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "focus_tags": focus_tags,
    }
    endpoint, api_key = _es_credentials()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            resp = await client.put(
                f"{endpoint}/{_BANK_INDEX}/_doc/{doc_id}?pipeline={_BANK_PIPELINE}&refresh=wait_for",
                json=body,
                headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code >= 300:
                return {"url": url, "ok": False, "error": f"index HTTP {resp.status_code}: {resp.text[:160]}"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            v = await client.get(
                f"{endpoint}/{_BANK_INDEX}/_doc/{doc_id}"
                "?_source_includes=embedding_error,attachment.content_length",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
            vs = v.json().get("_source", {})
        emb_err = vs.get("embedding_error")
        return {
            "url": final_url, "ok": emb_err is None, "doc_id": doc_id, "title": title,
            "chars": (vs.get("attachment", {}) or {}).get("content_length"),
            "embedding_error": emb_err,
        }
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        return {"url": url, "ok": False, "error": f"index error: {exc}"}


async def _archive_and_delete(doc: dict) -> bool:
    """Archive a doc's metadata then delete it from the bank (ML-free, recoverable trail)."""
    endpoint, api_key = _es_credentials()
    headers = {"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                f"{endpoint}/{_BANK_ARCHIVE_INDEX}/_doc",
                json={**doc, "archived_at": datetime.now(timezone.utc).isoformat()},
                headers=headers,
            )
            d = await client.delete(f"{endpoint}/{_BANK_INDEX}/_doc/{quote(doc['id'], safe='')}", headers=headers)
            return d.status_code < 300
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Archive/delete failed for %s: %s", doc.get("id"), exc)
        return False


async def _decide_removals(config: dict, protect_ids: set) -> list[dict]:
    """
    CONSERVATIVE removal — guards against unintended data loss:
      • Only ever trims when the bank EXCEEDS its size cap (e.g. 2000).
      • Only ever removes CURATOR-ADDED docs; the original seed corpus is never
        auto-deleted, and docs added this run are protected.
      • De-selecting a filter does NOT purge existing docs — it only changes what
        NEW docs get prioritised. (Purging-on-toggle previously deleted good docs.)
    Bounded per run, archived before delete.
    """
    max_docs = config.get("max_docs", _BANK_MAX_DOCS_DEFAULT)
    over = await _bank_count() - max_docs
    if over <= 0:
        return []  # under cap → never remove anything

    # Eligible for trimming: curator-added only, never originals or this run's adds.
    docs = [
        d for d in await _list_bank_docs()
        if d.get("curator_added") and d["id"] not in protect_ids
    ]
    if not docs:
        return []
    over = min(over, _BANK_MAX_TRIM_PER_RUN, len(docs))

    fc = config.get("focus_countries", [])
    ft = config.get("focus_topics", [])
    cnames = [_CODE_TO_NAME.get(c, c) for c in fc] or ["broad macro coverage"]
    topics = ft or _BANK_TOPICS
    listing = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(docs))
    prompt = (
        f"A macro research library is {over} document(s) OVER its capacity and must drop exactly "
        f"{over}. It should best cover FOCUS COUNTRIES: {', '.join(cnames)}; "
        f"FOCUS TOPICS: {', '.join(topics)}.\n"
        f"Candidate documents eligible for removal (index. title):\n{listing}\n\n"
        f"Return ONLY a JSON array of the {over} integer indices that are LEAST relevant or most "
        f"redundant. Example: [3, 17]"
    )
    try:
        async with _PIPELINE_SEMAPHORE:
            resp = await _generate_with_retry(
                model=GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1),
            )
        raw = (resp.text or "").strip()
        start, end = raw.find("["), raw.rfind("]")
        idxs = json.loads(raw[start:end + 1]) if start != -1 and end != -1 else []
    except Exception as exc:
        logger.error("Removal ranking failed: %s", exc)
        return []

    removed = []
    for i in idxs[:over]:
        if isinstance(i, int) and 0 <= i < len(docs):
            if await _archive_and_delete(docs[i]):
                removed.append({"id": docs[i]["id"], "title": docs[i]["title"]})
    return removed


async def _run_curation(config: dict, reason: str, dry_run: bool = False, max_add: Optional[int] = None) -> dict:
    """Full curation pass: discover → (preflight) ingest → trim. Shared by manual + sweep."""
    add_cap = max(0, min(max_add, _BANK_PER_RUN_ADD_CAP)) if max_add is not None else _BANK_PER_RUN_ADD_CAP
    bank_before = await _bank_count()
    existing = await _list_bank_docs()
    existing_titles = [d["title"] for d in existing if d.get("title")]

    candidates = await _discover_bank_candidates(
        config.get("focus_countries", []), config.get("focus_topics", []),
        existing_titles, _BANK_PER_RUN_ADD_CAP,
    )

    ml_ok, ml_reason = await _ml_embedding_healthy()
    added, add_errors, removed = [], [], []

    if dry_run:
        ts = datetime.now(timezone.utc).isoformat()
        return {
            "ran_at": ts, "reason": reason, "dry_run": True, "ml_healthy": ml_ok,
            "ml_reason": ml_reason, "bank_before": bank_before, "bank_after": bank_before,
            "max_docs": config.get("max_docs"), "candidates_found": len(candidates),
            "candidates": candidates, "added": [], "add_errors": [], "removed": [],
        }

    if candidates and not ml_ok:
        add_errors.append({"error": f"ingest skipped — ML preflight failed: {ml_reason}"})
    elif candidates and add_cap > 0:
        focus_tags = list(config.get("focus_countries", [])) + list(config.get("focus_topics", []))
        for c in candidates[:add_cap]:
            res = await _ingest_pdf(c["url"], c["title"], focus_tags)
            (added if res.get("ok") else add_errors).append(res)
            await asyncio.sleep(2)  # pacing — protect the ML tier
            ml_ok, ml_reason = await _ml_embedding_healthy()
            if not ml_ok:
                add_errors.append({"error": f"halted further ingest — ML degraded mid-run: {ml_reason}"})
                break

    just_added_ids = {a.get("doc_id") for a in added if a.get("doc_id")}
    removed = await _decide_removals(config, protect_ids=just_added_ids)

    bank_after = await _bank_count()
    ts = datetime.now(timezone.utc).isoformat()
    config["last_run_at"] = ts
    try:
        await _save_bank_config(config)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Failed to stamp bank last_run_at: %s", exc)

    logger.info(
        "📚 CURATION | reason=%s | before=%d after=%d added=%d removed=%d ml=%s",
        reason, bank_before, bank_after, len(added), len(removed), ml_ok,
    )
    return {
        "ran_at": ts, "reason": reason, "dry_run": False, "ml_healthy": ml_ok, "ml_reason": ml_reason,
        "bank_before": bank_before, "bank_after": bank_after, "max_docs": config.get("max_docs"),
        "candidates_found": len(candidates), "added": added, "add_errors": add_errors, "removed": removed,
    }


# ── Bank request models + endpoints ─────────────────────────────────────────

class BankConfigRequest(BaseModel):
    interval_hours: int = Field(_BANK_DEFAULT_INTERVAL_HOURS, description="Scan cadence in hours.")
    focus_countries: list[str] = Field(default_factory=list, description="ISO-3 codes to prioritise.")
    focus_topics: list[str] = Field(default_factory=list, description="Macro topics to prioritise.")
    max_docs: int = Field(_BANK_MAX_DOCS_DEFAULT, description="Ceiling on bank size (50–2000).")
    is_active: bool = Field(True, description="Whether the scheduled sweep runs this curator.")


@app.get("/api/v1/bank/catalog", operation_id="bank_catalog", summary="Curator options", tags=["Document Bank"])
async def bank_catalog() -> dict:
    return {
        "countries": _COUNTRY_CATALOG,
        "topics": _BANK_TOPICS,
        "intervals": _BANK_VALID_INTERVALS,
        "max_docs_default": _BANK_MAX_DOCS_DEFAULT,
        "allowed_domains": list(_BANK_ALLOWED_DOMAINS),
    }


@app.get("/api/v1/bank/config", operation_id="get_bank_config", summary="Get curator config", tags=["Document Bank"])
async def get_bank_config() -> dict:
    cfg = await _get_bank_config()
    cfg["current_doc_count"] = await _bank_count()
    return cfg


@app.post("/api/v1/bank/config", operation_id="set_bank_config", summary="Save curator config", tags=["Document Bank"])
async def set_bank_config(request: BankConfigRequest) -> dict:
    cfg = await _get_bank_config()
    cfg.update({
        "interval_hours": request.interval_hours if request.interval_hours in _BANK_VALID_INTERVALS else _BANK_DEFAULT_INTERVAL_HOURS,
        "focus_countries": [c.upper() for c in request.focus_countries if c.upper() in _CODE_TO_CCY],
        "focus_topics": [t for t in request.focus_topics if t in _BANK_TOPICS],
        "max_docs": max(50, min(request.max_docs, _BANK_MAX_DOCS_DEFAULT)),
        "is_active": bool(request.is_active),
    })
    await _save_bank_config(cfg)
    cfg["current_doc_count"] = await _bank_count()
    logger.info("Bank config saved | active=%s | interval=%dh | cap=%d | focus=%s/%s",
                cfg["is_active"], cfg["interval_hours"], cfg["max_docs"],
                cfg["focus_countries"], cfg["focus_topics"])
    return {"status": "saved", "config": cfg}


@app.get("/api/v1/bank/docs", operation_id="list_bank_docs", summary="List bank documents", tags=["Document Bank"])
async def list_bank_docs(size: int = 200) -> dict:
    docs = await _list_bank_docs(size)
    return {"count": len(docs), "documents": docs}


@app.post("/api/v1/bank/curate", operation_id="run_bank_curation", summary="Run the curator now", tags=["Document Bank"])
async def run_bank_curation(dry_run: bool = False, max_add: Optional[int] = None) -> dict:
    cfg = await _get_bank_config()
    return await _run_curation(cfg, reason="manual", dry_run=dry_run, max_add=max_add)


@app.post("/api/v1/bank/sweep", operation_id="run_bank_sweep", summary="Scheduled curator sweep", tags=["Document Bank"])
async def run_bank_sweep(x_sweep_token: Optional[str] = Header(default=None)) -> dict:
    expected = os.environ.get("ALERT_SWEEP_TOKEN", "")
    if expected and x_sweep_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing sweep token.")
    cfg = await _get_bank_config()
    if not cfg.get("is_active"):
        return {"skipped": "curator inactive"}
    last = cfg.get("last_run_at")
    if last:
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < cfg["interval_hours"] * 3600:
                return {"skipped": "not due", "last_run_at": last, "interval_hours": cfg["interval_hours"]}
        except (ValueError, TypeError):
            pass
    return await _run_curation(cfg, reason="scheduled")


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