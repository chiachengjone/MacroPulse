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
# GenAI Client — Vertex AI enterprise path.
# ---------------------------------------------------------------------------
genai_client = genai.Client(
    vertexai=True,
    project=os.environ.get("GOOGLE_CLOUD_PROJECT", "project-a8903c34-ce47-431c-9f7"),
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
)

GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PERSONA = (
    "You are an elite Global Markets Macro Quantitative Analyst and Credit Risk Officer "
    "at a tier-1 investment bank. Your objective is to ingest complex, unstructured financial "
    "narratives, extract quantitative risk metrics, and output a strict, zero-fluff risk summary. "
    "Use the search_macro_data tool to retrieve relevant macro-economic research before finalising "
    "your assessment. Search at least once with targeted keywords, then produce your structured output."
)

# ---------------------------------------------------------------------------
# Elastic MCP search tool declaration for Gemini function calling
# ---------------------------------------------------------------------------
_SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_macro_data",
            description=(
                "Search the MacroPulse macro-economic knowledge base for relevant institutional "
                "finance documents. Call this with targeted economic keywords before producing "
                "the final risk assessment."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Search query — use economic indicators, sovereign risk terms, "
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

class RiskEvaluationRequest(BaseModel):
    narrative: str = Field(
        ...,
        description="Unstructured financial narrative or macro scenario to evaluate.",
        min_length=20,
    )
    context: Optional[str] = Field(
        None,
        description="Optional framing context such as asset class, issuer, or geographic region.",
    )


class RiskAssessment(BaseModel):
    sovereign_risk_score: float = Field(
        ...,
        ge=1.0,
        le=10.0,
        description="Composite sovereign risk score from 1.0 (negligible) to 10.0 (systemic collapse).",
    )
    primary_threat_vector: str = Field(
        ...,
        description=(
            "Single dominant risk classification, e.g. 'Liquidity Crunch', "
            "'FX Volatility', 'Fiscal Deficit Expansion', 'Sovereign Default Risk'."
        ),
    )
    impact_assessment: str = Field(
        ...,
        description=(
            "Concise structural narrative covering transmission mechanisms, "
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


class RiskEvaluationResponse(BaseModel):
    assessment: RiskAssessment
    model_used: str
    evaluation_timestamp: str
    alert_dispatched: bool

# ---------------------------------------------------------------------------
# Lifespan — Elastic MCP client startup / teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    elastic_mcp_url = os.environ.get("ELASTIC_MCP_URL", "").strip()

    _BUNDLED_BIN = "/usr/local/bin/elasticsearch-core-mcp-server"

    if elastic_mcp_url:
        # Explicit HTTP transport (reserved for future external MCP server).
        import google.auth.transport.requests
        import google.oauth2.id_token

        logger.info("Elastic MCP: fetching ID token for %s", elastic_mcp_url)
        _req = google.auth.transport.requests.Request()
        id_token = await asyncio.to_thread(
            google.oauth2.id_token.fetch_id_token, _req, elastic_mcp_url
        )

        logger.info("Elastic MCP: connecting via HTTP → %s/mcp", elastic_mcp_url)
        async with streamablehttp_client(
            url=f"{elastic_mcp_url}/mcp",
            headers={"Authorization": f"Bearer {id_token}"},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                app_module._call_elastic_tool = session.call_tool
                tools = await session.list_tools()
                logger.info(
                    "Elastic MCP HTTP session ready | tools=%s",
                    [t.name for t in tools.tools],
                )
                yield

    elif os.path.isfile(_BUNDLED_BIN):
        # Production (Cloud Run): use the Elastic MCP binary bundled in the image.
        # Runs as a stdio subprocess — same network context as MacroPulse, no
        # Docker-in-Docker, no separate service.
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
                logger.info(
                    "Elastic MCP binary session ready | tools=%s",
                    [t.name for t in tools.tools],
                )
                yield

    else:
        # Local development: spawn the official Elastic Docker MCP server via stdio.
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
                logger.info(
                    "Elastic MCP Docker session ready | tools=%s",
                    [t.name for t in tools.tools],
                )
                yield

    app_module._call_elastic_tool = None
    logger.info("Elastic MCP session closed")

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MacroPulse Analytics Bridge",
    description=(
        "Agentic Financial Intelligence Platform — multi-step macro research "
        "and credit risk orchestration via Gemini 2.5 Flash + Elastic MCP, exposed over MCP."
    ),
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Background-task tracking (prevents GC of fire-and-forget coroutines)
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
    webhook_url = os.environ.get(
        "TRADING_DESK_WEBHOOK_URL", "https://httpbin.org/post"
    )

    score: float = ticket.get("sovereign_risk_score", 0.0)

    if score >= 9.0:
        severity = "CRITICAL"
        escalation_policy = "IMMEDIATE_DESK_REVIEW"
        sla_minutes = 5
    elif score >= 7.5:
        severity = "HIGH"
        escalation_policy = "IMMEDIATE_DESK_REVIEW"
        sla_minutes = 15
    elif score >= 5.0:
        severity = "MEDIUM"
        escalation_policy = "STANDARD_REVIEW_QUEUE"
        sla_minutes = 60
    else:
        severity = "LOW"
        escalation_policy = "DAILY_DIGEST"
        sla_minutes = 1440

    payload = {
        "event_type": "MACRO_RISK_ALERT",
        "schema_version": "4.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "sla_resolution_minutes": sla_minutes,
        "source_system": "MacroPulse Analytics Bridge v4",
        "escalation_policy": escalation_policy,
        "routing_key": f"risk.{severity.lower()}.sovereign",
        "risk_parameters": {
            "sovereign_risk_score": ticket["sovereign_risk_score"],
            "primary_threat_vector": ticket["primary_threat_vector"],
            "impact_assessment": ticket["impact_assessment"],
            "requires_immediate_alert": ticket["requires_immediate_alert"],
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http_client:
        try:
            resp = await http_client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-MacroPulse-Version": "4.0",
                    "X-Alert-Severity": severity,
                },
            )
            resp.raise_for_status()
            logger.info(
                "Trading desk alert dispatched | severity=%s | score=%.2f | http_status=%d",
                severity, score, resp.status_code,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("Webhook delivery failed | http_status=%d | detail=%s", exc.response.status_code, exc)
        except httpx.RequestError as exc:
            logger.error("Webhook network error | detail=%s", exc)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    operation_id="health_check",
    summary="Service Health Check",
    tags=["Operations"],
)
async def health_check() -> dict:
    return {
        "status": "healthy",
        "service": "MacroPulse Analytics Bridge",
        "version": "4.0.0",
        "model": GEMINI_MODEL,
        "elastic_mcp": "connected" if app_module._call_elastic_tool else "disconnected",
    }


@app.post(
    "/evaluate-risk",
    response_model=RiskEvaluationResponse,
    operation_id="evaluate_macro_risk",
    summary="Evaluate Macro & Sovereign Risk",
    description=(
        "Ingest an unstructured financial narrative and return a structured credit risk "
        "assessment. Gemini 2.5 Flash runs a multi-step agentic loop: it calls the Elastic "
        "MCP search tool one or more times to retrieve grounding evidence, then produces a "
        "structured RiskAssessment. When sovereign_risk_score ≥ 7.5 or requires_immediate_alert "
        "is true, an async alert payload is dispatched to the configured trading desk webhook."
    ),
    tags=["Risk Intelligence"],
)
async def evaluate_risk(request: RiskEvaluationRequest) -> RiskEvaluationResponse:
    logger.info("Risk evaluation requested | narrative_chars=%d", len(request.narrative))

    # Build the initial user prompt
    prompt_parts = [request.narrative]
    if request.context:
        prompt_parts.append(f"Additional context: {request.context}")
    user_prompt = "\n\n".join(prompt_parts)

    # -----------------------------------------------------------------------
    # Phase 1 — Agentic tool-calling loop
    # Gemini decides when and how many times to call search_macro_data.
    # We cap at MAX_TURNS to avoid runaway loops.
    # -----------------------------------------------------------------------
    contents: list = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    MAX_TURNS = 6

    for turn in range(MAX_TURNS):
        response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PERSONA,
                tools=[_SEARCH_TOOL],
            ),
        )

        candidate = response.candidates[0]
        fn_calls = [p.function_call for p in candidate.content.parts if p.function_call]

        if not fn_calls:
            # Model finished reasoning — no more tool calls
            logger.info("Agent loop complete | turns_used=%d", turn + 1)
            break

        # Add model's response (with function calls) to conversation history
        contents.append(candidate.content)

        # Execute each tool call and collect responses
        fn_response_parts: list[types.Part] = []
        for fc in fn_calls:
            if fc.name == "search_macro_data":
                logger.info("Gemini → search_macro_data | query=%r", fc.args.get("query"))
                result = await app_module.search_macro_data(fc.args["query"])
                fn_response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

        contents.append(types.Content(role="user", parts=fn_response_parts))
    else:
        logger.warning("Agent loop hit MAX_TURNS=%d without stopping naturally", MAX_TURNS)

    # -----------------------------------------------------------------------
    # Phase 2 — Structured extraction
    # Force a final structured-output call using the full conversation history
    # as grounding context (no tools — we want JSON output, not another call).
    # -----------------------------------------------------------------------
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(
                text=(
                    "Based on all the macro research retrieved above, now produce "
                    "the final structured risk assessment for the original narrative."
                )
            )],
        )
    )

    try:
        final_response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PERSONA,
                response_mime_type="application/json",
                response_schema=RiskAssessment,
            ),
        )
    except Exception as exc:
        logger.error("Gemini structured-output call failed | detail=%s", exc)
        raise HTTPException(status_code=502, detail=f"LLM orchestration failure: {exc}")

    try:
        assessment: RiskAssessment = (
            final_response.parsed
            if getattr(final_response, "parsed", None) is not None
            else RiskAssessment.model_validate_json(final_response.text)
        )
    except Exception as exc:
        logger.error(
            "Schema validation failure | raw=%r | error=%s",
            getattr(final_response, "text", "<no text>"),
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail="Risk assessment schema validation failed.",
        )

    alert_dispatched = False
    if assessment.sovereign_risk_score >= 7.5 or assessment.requires_immediate_alert:
        _fire_and_forget(trigger_trading_desk_webhook(assessment.model_dump()))
        alert_dispatched = True
        logger.info(
            "Alert queued | score=%.2f | requires_immediate_alert=%s",
            assessment.sovereign_risk_score,
            assessment.requires_immediate_alert,
        )

    return RiskEvaluationResponse(
        assessment=assessment,
        model_used=GEMINI_MODEL,
        evaluation_timestamp=datetime.now(timezone.utc).isoformat(),
        alert_dispatched=alert_dispatched,
    )

# ---------------------------------------------------------------------------
# MCP Server — mount AFTER all routes are registered
# ---------------------------------------------------------------------------
mcp = FastApiMCP(
    app,
    name="MacroPulse Analytics Bridge",
    description=(
        "Model Context Protocol server exposing macro risk evaluation tools "
        "for Google Cloud Agent Builder integration. Provides structured sovereign "
        "risk scoring, threat vector classification, and automated desk alerting."
    ),
)
mcp.mount()