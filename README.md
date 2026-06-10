# MacroPulse 🌐

**Enterprise Multi-Agent Sovereign Risk Intelligence Platform**

> Submitted to the [Google Cloud Rapid Agent Hackathon 2026](https://rapid-agent.devpost.com) — **Elastic Track**
> Built by a NUS Business Analytics student

[![Live Dashboard](https://img.shields.io/badge/Live%20Dashboard-Cloud%20Run-4285F4?logo=googlecloud)](https://macropulse-dashboard-270431042772.us-central1.run.app)
[![API Docs](https://img.shields.io/badge/API%20Docs-Swagger-85EA2D?logo=swagger)](https://macropulse-270431042772.us-central1.run.app/docs)
[![MCP Server](https://img.shields.io/badge/MCP%20Server-%2Fmcp-orange)](https://macropulse-270431042772.us-central1.run.app/mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**▶ Try it live: [macropulse-dashboard-270431042772.us-central1.run.app](https://macropulse-dashboard-270431042772.us-central1.run.app)**

---

## What is MacroPulse?

MacroPulse ingests unstructured financial narratives — news articles, analyst memos, market dispatches — and produces **fact-audited, evidence-grounded sovereign risk assessments** using a two-agent AI pipeline.

A **Fact Auditor** agent searches an institutional macro-economics knowledge base (Elasticsearch Serverless) to cross-reference the narrative against real research documents, identifying data gaps, inflated claims, and factual errors. A **Sovereign Risk Specialist** agent then synthesises that audit with the retrieved evidence to produce a structured country risk profile with an alert escalation decision.

The platform is built on **Google Cloud's Vertex AI** (Gemini 2.5 Flash) and **Elasticsearch Serverless**, connected via Elastic's official **Model Context Protocol (MCP)** server.

---

## Live URLs

| Resource | URL |
|---|---|
| **Dashboard (Cloud Run)** | `https://macropulse-dashboard-270431042772.us-central1.run.app` |
| API (Cloud Run) | `https://macropulse-270431042772.us-central1.run.app` |
| Health check | `https://macropulse-270431042772.us-central1.run.app/health` |
| Swagger UI | `https://macropulse-270431042772.us-central1.run.app/docs` |
| MCP server | `https://macropulse-270431042772.us-central1.run.app/mcp` |
| GitHub | `https://github.com/chiachengjone/MacroPulse` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MacroPulse Platform                          │
│                                                                 │
│  User Input                                                     │
│  (Narrative + Context)                                          │
│        │                                                        │
│        ▼                                                        │
│  ┌─────────────┐   SSE stream API   ┌────────────────────────┐  │
│  │  frontend/  │ ──────────────────▶│ POST /api/v1/evaluate  │  │
│  │  (Next.js)  │ ◀──────────────────│        /stream         │  │
│  └─────────────┘   live pipeline    └───────────┬────────────┘  │
│                                                ▼                │
│                          ┌─────────────────────────────────┐    │
│                          │   AGENT 1 — Fact Auditor        │    │
│                          │   Persona: Credit Rating Agency  │    │
│                          │   Model: Gemini 2.5 Flash        │    │
│                          │                                 │    │
│                          │   Tool-calling loop:            │    │
│                          │   search_macro_data(query) ─────┼──▶ │
│                          └─────────────┬───────────────────┘    │
│                                        │                        │
│            ┌──────────────────────────▼──────────────────────┐  │
│            │        Elastic MCP Server (bundled binary)      │  │
│            │        docker.elastic.co/mcp/elasticsearch      │  │
│            │        stdio subprocess — same network context  │  │
│            └──────────────────────────┬──────────────────────┘  │
│                                       │                         │
│            ┌──────────────────────────▼──────────────────────┐  │
│            │     Elasticsearch Serverless                     │  │
│            │     Index: macro-pulse-files                    │  │
│            │                                                  │  │
│            │     Hybrid Retrieval (RRF):                     │  │
│            │     ├── Text: multi_match (title^3, content^2)  │  │
│            │     └── Vector: kNN (.multilingual-e5-small)    │  │
│            └──────────────────────────────────────────────────┘  │
│                                       │                         │
│                          ┌────────────▼────────────────────┐    │
│                          │   AGENT 2 — Risk Specialist     │    │
│                          │   Persona: Tier-1 IB Analyst    │    │
│                          │   Model: Gemini 2.5 Flash        │    │
│                          │                                 │    │
│                          │   Input: narrative + audit +    │    │
│                          │   grounding docs                │    │
│                          │   Output: SovereignRisk         │    │
│                          │   Assessment (JSON schema)      │    │
│                          └──────────────┬──────────────────┘    │
│                                         │                       │
│                          ┌──────────────▼──────────────────┐    │
│                          │   Webhook (async)               │    │
│                          │   Trading desk alert if         │    │
│                          │   score ≥ 7.5                   │    │
│                          └─────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| **LLM Orchestration** | Gemini 2.5 Flash via Vertex AI (Application Default Credentials) |
| **Knowledge Base** | Elasticsearch Serverless — index `macro-pulse-files` |
| **MCP Integration** | Official Elastic MCP server (`docker.elastic.co/mcp/elasticsearch`) |
| **Retrieval** | Hybrid RRF: `multi_match` (BM25) + kNN (`.multilingual-e5-small`) |
| **API Framework** | FastAPI + `fastapi-mcp` (exposes own MCP server at `/mcp`) |
| **UI** | Next.js 15 (App Router) + React 19 + Tailwind CSS — live SSE pipeline dashboard |
| **Deployment** | Google Cloud Run (serverless containers) |
| **Container** | Python 3.11-slim + bundled Elastic MCP binary (multi-stage Docker) |

---

## Key Features

### 1. Hybrid Retrieval (RRF)
`app.py` — `search_macro_data(query)`

Attempts **Elasticsearch Reciprocal Rank Fusion** combining:
- BM25 `multi_match` across `attachment.title^3` and `attachment.content^2`
- kNN vector search via `.multilingual-e5-small` on `attachment.content_embedding`

Automatically falls back to a high-fidelity keyword `multi_match` if the vector index is unavailable.

### 2. Two-Agent Correction Loop
`main.py` — `_run_agent1_auditor` + `_run_agent2_specialist`

**Agent 1 — Fact Auditor**: Uses Gemini's function-calling to iteratively retrieve documents from Elasticsearch, then writes a structured audit identifying DATA GAPS, INFLATIONS, and ERRORS in the input narrative.

**Agent 2 — Sovereign Risk Specialist**: Receives the narrative, the Auditor's full report, and the raw grounding documents. Produces a `SovereignRiskAssessment` with:
- `raw_narrative_score` (0.0 – 10.0) — risk if the narrative is taken at face value (pre-audit)
- `sovereign_risk_score` (0.0 – 10.0) — the audit-**adjusted** final score
- `primary_threat_vector`
- `audit_findings` (from Agent 1)
- `impact_assessment`
- `requires_immediate_alert` (bool)
- `grounding_strength` (`STRONG` / `PARTIAL` / `LIMITED`) + `grounding_note`
- `sources` — titles of the knowledge-base documents that grounded the assessment

### 3. Grounding Transparency (calibrated confidence)
`main.py` — Agent 2 judges how well the retrieved corpus actually grounds *this specific* narrative and emits a tier:
- **STRONG** — entity-specific evidence found (e.g. an IMF Article IV for the exact country, or that central bank's report)
- **PARTIAL** — only general macro/sovereign-risk research; the score rests on the narrative, not corroborating evidence
- **LIMITED** — retrieved documents are off-topic or absent (e.g. an out-of-domain prompt)

The dashboard surfaces this as a coloured badge + one-line rationale + a list of the cited source documents. Instead of silently degrading on unfamiliar inputs, the system **states how confident it is** — and the `raw → adjusted` score delta makes the two-agent correction visible.

### 4. MCP Dual Role
MacroPulse both **consumes** Elastic's MCP server (for knowledge retrieval) and **exposes** its own MCP server at `/mcp` via `fastapi-mcp`, making all API endpoints discoverable as tools for Google Cloud Agent Builder.

### 5. Next.js Live Pipeline Dashboard
`frontend/` — a Next.js 15 + React 19 dashboard that streams the two-agent pipeline in real time over Server-Sent Events (`POST /api/v1/evaluate/stream`). Users enter their own narrative (or pick an example), then watch each step appear live — auditor reasoning, Elasticsearch search queries, retrieved sources, the grounding verdict, and the final risk score, threat vector, and impact assessment.

### 6. Async Trading Desk Alerts
Fire-and-forget webhook dispatch when `sovereign_risk_score ≥ 7.5` or `requires_immediate_alert = true`, with severity tiers (CRITICAL / HIGH / MEDIUM / LOW) and SLA windows.

---

## Project Structure

```
MacroPulse/
├── app.py              # Elastic MCP client + hybrid search tool (FastMCP server)
├── main.py             # FastAPI server, two-agent orchestration, SSE stream, MCP mount
├── frontend/           # Next.js 15 dashboard (live SSE pipeline UI)
│   ├── app/            # App Router pages + client dashboard
│   ├── components/     # Chat input, pipeline feed, risk results
│   └── lib/            # SSE client hook, types, helpers
├── ingest.py           # Bulk PDF → Elasticsearch loader (Tika extract + e5 embeddings)
├── Dockerfile          # Multi-stage: bundles Elastic MCP binary into Python image
├── requirements.txt    # Pinned Python dependencies
├── .env.example        # Template for required environment variables
├── LICENSE             # MIT
└── README.md
```

---

## Setup & Running

### Prerequisites
- Python 3.11+
- Docker Desktop (for local development)
- Google Cloud project with Vertex AI API enabled
- Elasticsearch Serverless project on Elastic Cloud
- `gcloud` CLI authenticated: `gcloud auth application-default login`

### 1. Clone & configure

```bash
git clone https://github.com/chiachengjone/MacroPulse.git
cd MacroPulse

cp .env.example .env
# Edit .env with your ELASTIC_ENDPOINT, ELASTIC_API_KEY, GOOGLE_CLOUD_PROJECT
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the API server (local)

Ensure Docker Desktop is running (for the Elastic MCP stdio subprocess), then:

```bash
python -m uvicorn main:app --reload --port 8080
```

Server starts at `http://localhost:8080`. Swagger UI at `http://localhost:8080/docs`.

### 4. Run the Next.js dashboard (separate terminal)

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:3000`. By default `frontend/.env.local` points `NEXT_PUBLIC_API_URL` at the deployed Cloud Run instance; set it to `http://localhost:8080` to hit your local server.

---

## API Reference

### `POST /api/v1/evaluate`

Primary endpoint. Runs the full two-agent cascade.

**Request body:**
```json
{
  "narrative": "Argentina has suspended all external debt payments...",
  "context": "Sovereign default, EM credit, FX crisis"
}
```

**Response:**
```json
{
  "assessment": {
    "raw_narrative_score": 9.0,
    "sovereign_risk_score": 8.5,
    "primary_threat_vector": "Sovereign Default Risk",
    "audit_findings": "DATA GAPS: ...\nINFLATIONS: ...\nERRORS: ...",
    "impact_assessment": "The suspension of debt payments transmits via...",
    "requires_immediate_alert": true,
    "grounding_strength": "STRONG",
    "grounding_note": "Corroborated by the IMF Article IV consultation and BIS Quarterly Review for the region.",
    "sources": [
      "IMF Argentina 2026 Article IV Consultation + 2nd EFF Review",
      "BIS Quarterly Review (issue 2603)"
    ]
  },
  "model_used": "gemini-2.5-flash",
  "evaluation_timestamp": "2026-06-07T10:21:49.708179+00:00",
  "alert_dispatched": true
}
```

### `POST /api/v1/evaluate/stream`

Streaming variant of `/api/v1/evaluate`. Returns a Server-Sent Events stream, emitting one event per pipeline step (`agent1_start`, `agent1_thinking`, `agent1_search`, `agent1_complete`, `agent2_start`, `cross_check`, `agent2_complete`) before a final `complete` event carrying the full `EvaluationResponse` JSON. This powers the live pipeline feed in the Next.js dashboard.

### `GET /health`

```json
{
  "status": "healthy",
  "service": "MacroPulse Analytics Bridge",
  "version": "5.0.0",
  "model": "gemini-2.5-flash",
  "elastic_mcp": "connected"
}
```

### `GET /docs`
Interactive Swagger UI — test both endpoints in-browser.

### `GET /mcp`
MCP server endpoint — discoverable by Google Cloud Agent Builder and other MCP clients.

### `POST /evaluate-risk` *(legacy alias)*
Same handler as `/api/v1/evaluate`. Maintained for backward compatibility.

---

## Deploying to Google Cloud Run

```bash
# Authenticate and set project
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# Deploy (Cloud Build handles the multi-stage Docker build automatically)
gcloud run deploy macropulse \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,\
GOOGLE_CLOUD_LOCATION=global,\
ELASTIC_ENDPOINT=YOUR_ES_ENDPOINT,\
ELASTIC_API_KEY=YOUR_API_KEY,\
TRADING_DESK_WEBHOOK_URL=https://httpbin.org/post"
```

> The Dockerfile uses a multi-stage build to copy the AMD64 Elastic MCP binary from `docker.elastic.co/mcp/elasticsearch` into the Python image. No separate Elastic MCP service is needed on Cloud Run.

### Deploying the dashboard (frontend)

The Next.js dashboard deploys as a separate Cloud Run service using its own multi-stage Dockerfile (`frontend/Dockerfile`, standalone output):

```bash
cd frontend
gcloud run deploy macropulse-dashboard \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi
```

> `NEXT_PUBLIC_API_URL` is baked into the bundle at build time (set in `frontend/Dockerfile`). The backend's CORS policy (`allow_origins=["*"]`) permits the dashboard's cross-origin calls.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | ✅ | GCP project ID for Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | ✅ | Vertex AI region (use `global`) |
| `ELASTIC_ENDPOINT` | ✅ | HTTPS URL of your Elasticsearch Serverless deployment |
| `ELASTIC_API_KEY` | ✅ | Base64-encoded Elastic API key (id:secret format) |
| `ELASTIC_MCP_URL` | — | Leave empty; only set if running a separate Elastic MCP HTTP service |
| `TRADING_DESK_WEBHOOK_URL` | — | Alert webhook endpoint (defaults to `httpbin.org/post`) |

---

## Hackathon Compliance — Elastic Track

| Requirement | Status | Implementation |
|---|---|---|
| Uses Google Cloud | ✅ | Vertex AI (Gemini 2.5 Flash), Cloud Run, Cloud Build |
| Uses Gemini | ✅ | `gemini-2.5-flash` via `google-genai` SDK, Vertex AI ADC |
| Integrates Elastic's MCP server | ✅ | `docker.elastic.co/mcp/elasticsearch` — official binary bundled in Docker image, runs as stdio subprocess |
| Multi-step reasoning / agentic | ✅ | Agent 1 runs a tool-calling loop (up to 4 turns); Agent 2 synthesises multi-source context |
| Functional agent (executes actions) | ✅ | Searches Elasticsearch, audits narratives, fires trading desk webhooks |
| Exposes MCP server | ✅ | `fastapi-mcp` at `/mcp` — all FastAPI endpoints auto-exposed as MCP tools |
| Public open-source repo | ✅ | [github.com/chiachengjone/MacroPulse](https://github.com/chiachengjone/MacroPulse) |
| Open-source license | ✅ | MIT License |
| Publicly hosted URL | ✅ | `https://macropulse-270431042772.us-central1.run.app` |

---

## How the Elastic MCP Integration Works

MacroPulse uses Elastic's **official** MCP server binary rather than querying Elasticsearch directly. This is achieved via a multi-stage Docker build:

```dockerfile
FROM --platform=linux/amd64 docker.elastic.co/mcp/elasticsearch AS elastic-mcp
FROM python:3.11-slim
COPY --from=elastic-mcp /usr/local/bin/elasticsearch-core-mcp-server /usr/local/bin/
```

At runtime, `main.py` detects the bundled binary and spawns it as a `stdio` subprocess using the MCP Python SDK's `StdioServerParameters`. The Python `ClientSession` then calls its tools — `search`, `list_indices`, `get_mappings` — using the standard Model Context Protocol.

This approach avoids Docker-in-Docker and keeps the Elastic MCP server in the same network context as the application, resolving authentication issues that arise when running it as a separate Cloud Run service.

---

## Why Sovereign Risk?

Sovereign risk analysis is a high-stakes, information-dense task where AI can deliver outsized value:

- Analysts must synthesise hundreds of pages of institutional research on tight deadlines
- Narratives in financial media are often exaggerated or selectively sourced
- A structured, evidence-grounded audit before any risk score is assigned is standard practice at rating agencies — but rarely automated

MacroPulse automates exactly this workflow: retrieve evidence → audit the narrative → score the risk.

---

## Sample Scenarios to Test

**High-Risk (alerts should fire):**
```
Narrative: Turkey's central bank has exhausted its net FX reserves following a speculative
attack on the lira. The government has suspended convertibility and capital controls are
expected imminently. Dollarisation sentiment is rising rapidly.
Context: FX crisis, EM sovereign, liquidity crunch
```

**Low-Risk (audit should surface contradictions):**
```
Narrative: Argentina has defaulted on all external debt and the peso has collapsed 45%
in 72 hours. EM contagion is spreading to Turkey and Pakistan.
Context: Sovereign default, EM credit
```
*(The knowledge base contains BIS Quarterly Reviews showing Latin American currencies
outperforming in 2025-2026, so the Auditor will flag this as contradicted by evidence.)*

**Inflated narrative → strong audit correction (Singapore):**
```
Narrative: Singapore's banks are reportedly carrying dangerous exposure to distressed
Chinese property developers as home prices crater and the SGD slides, with speculation
MAS will abandon its exchange-rate framework amid capital flight.
Context: Banking stability, property, SGD, MAS policy
```
*(Grounded by MAS Financial Stability Reviews and the IMF Singapore Article IV — the
Auditor flags the alarmist framing as contradicted, producing a large `raw → adjusted`
score drop with **STRONG** grounding.)*

> **Knowledge base.** The corpus is institutional macro research — BIS Quarterly Reviews
> & Annual Economic Reports, IMF WEO / GFSR / Fiscal Monitor & country Article IVs
> (Türkiye, Argentina, Egypt, Pakistan, Singapore + Asia-Pacific), World Bank GEP, OECD,
> and NBER working papers on sovereign default & currency crises. It is loaded into the
> `macro-pulse-files` index by [`ingest.py`](ingest.py) (Apache Tika text extraction +
> `.multilingual-e5-small` embeddings via an Elasticsearch ingest pipeline).

---

## License

MIT © 2026 — see [LICENSE](LICENSE)