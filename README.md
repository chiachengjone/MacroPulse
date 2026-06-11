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

MacroPulse ingests unstructured financial narratives — news articles, analyst memos, market dispatches — and produces **fact-audited, evidence-grounded sovereign risk assessments** through a two-agent AI pipeline.

A **Fact Auditor** agent searches a self-curating Elasticsearch knowledge base of institutional macro research to cross-reference the narrative against real evidence, identifying data gaps, inflated claims, and factual errors. A **Sovereign Risk Specialist** agent then synthesises the audit and retrieved documents into a structured risk profile.

The platform is built on **Google Cloud's Vertex AI** (Gemini 2.5 Flash) and **Elasticsearch Serverless**, connected via Elastic's official **Model Context Protocol (MCP)** server. It runs fully on Google Cloud Run with no external compute dependencies.

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
│  User Input (narrative / article URL / preset)                  │
│        │                                                        │
│        ▼                                                        │
│  ┌─────────────┐   SSE stream    ┌────────────────────────────┐ │
│  │  frontend/  │ ──────────────▶ │  POST /api/v1/evaluate     │ │
│  │  (Next.js)  │ ◀──────────────  │       /stream              │ │
│  └─────────────┘  live pipeline  └────────────┬───────────────┘ │
│                                               ▼                 │
│                         ┌─────────────────────────────────┐     │
│                         │   AGENT 1 — Fact Auditor        │     │
│                         │   Model: Gemini 2.5 Flash       │     │
│                         │   Tool-calling loop:            │     │
│                         │   search_macro_data(query) ─────┼──▶  │
│                         └─────────────┬───────────────────┘     │
│                                       │                         │
│           ┌───────────────────────────▼──────────────────────┐  │
│           │     Elastic MCP Server (bundled binary)          │  │
│           │     docker.elastic.co/mcp/elasticsearch         │  │
│           └───────────────────────────┬──────────────────────┘  │
│                                       │                         │
│           ┌───────────────────────────▼──────────────────────┐  │
│           │     Elasticsearch Serverless                      │  │
│           │     Index: macro-pulse-files (169+ docs)         │  │
│           │     Hybrid Retrieval (RRF):                      │  │
│           │     ├── BM25 multi_match (title^3, content^2)    │  │
│           │     └── kNN (.multilingual-e5-small)             │  │
│           └───────────────────────────────────────────────────┘  │
│                                       │                         │
│                         ┌─────────────▼───────────────────┐     │
│                         │   AGENT 2 — Risk Specialist     │     │
│                         │   Model: Gemini 2.5 Flash       │     │
│                         │   Output: SovereignRiskAssessment│    │
│                         └─────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| **LLM Orchestration** | Gemini 2.5 Flash via Vertex AI (Application Default Credentials) |
| **Knowledge Base** | Elasticsearch Serverless — index `macro-pulse-files` |
| **Audit Trail** | Elasticsearch Serverless — index `macro-pulse-audit` |
| **Alert Subscribers** | Elasticsearch Serverless — index `macro-alert-subscribers` |
| **Bank Config** | Elasticsearch Serverless — index `macro-bank-config` |
| **MCP Integration** | Official Elastic MCP server (`docker.elastic.co/mcp/elasticsearch`) |
| **Retrieval** | Hybrid RRF: `multi_match` (BM25) + kNN (`.multilingual-e5-small`) |
| **API Framework** | FastAPI + `fastapi-mcp` (exposes `/mcp`) |
| **UI** | Next.js 15 (App Router) + React 19 + Tailwind CSS |
| **Deployment** | Google Cloud Run (two services — API + dashboard) |
| **Scheduler** | Google Cloud Scheduler (alert sweep + bank curation) |
| **Email** | Resend API |
| **Market Data** | FRED (St. Louis Fed) + open.er-api.com |
| **Bank Discovery** | Tavily Search API (restricted to whitelisted institutional domains) |
| **Container** | Python 3.11-slim + bundled Elastic MCP binary (multi-stage Docker) |

---

## Key Features

### 1. Two-Agent Correction Loop
`main.py` — `_run_agent1_auditor` + `_run_agent2_specialist`

**Agent 1 — Fact Auditor**: Tool-calling loop (up to 2 turns) against Elasticsearch. Identifies DATA GAPS, INFLATIONS, and ERRORS in the input narrative, citing retrieved documents.

**Agent 2 — Sovereign Risk Specialist**: Synthesises narrative + audit + grounding docs into a `SovereignRiskAssessment`:

| Field | Description |
|---|---|
| `raw_narrative_score` | 0–10 risk if the narrative is taken at face value |
| `sovereign_risk_score` | Audit-adjusted final score |
| `primary_threat_vector` | e.g. "Sovereign Default Risk", "FX Volatility" |
| `audit_findings` | Structured DATA GAPS / INFLATIONS / ERRORS |
| `impact_assessment` | Transmission mechanisms and second-order effects |
| `grounding_strength` | `STRONG / PARTIAL / LIMITED` |
| `grounding_note` | One sentence naming the evidence basis |
| `sources` | Titles of retrieved grounding documents |
| `action_disposition` | Autonomy decision (see §5) |

### 2. Hybrid Retrieval (RRF)
`app.py` — `_search_with_sources`

Elasticsearch Reciprocal Rank Fusion combining BM25 `multi_match` across `attachment.title^3` / `attachment.content^2` with kNN vector search via `.multilingual-e5-small`. Automatic keyword fallback if the vector index is unavailable.

### 3. Article Ingestion & URL Fetch
`main.py` — `POST /api/v1/fetch-article`

Paste a news article URL → the backend fetches it, strips HTML, and Gemini writes a clean 150–250 word financial summary that lands in the narrative input. Handles `<article>` / `<main>` extraction, `.ashx` PDF links, and graceful fallback to raw text. Alternatively, paste the article excerpt directly.

### 4. Grounding Transparency
Three tiers surfaced on every assessment:
- **STRONG** — entity-specific evidence found (e.g. the exact country's IMF Article IV)
- **PARTIAL** — only general macro research; score rests on the narrative
- **LIMITED** — off-topic or absent; treat score as low-confidence

Raw score → adjusted score delta makes the Auditor's correction visible and testable.

### 5. Confidence-Gated Autonomy
`main.py` — `decide_disposition`

A **deterministic code policy** (not an LLM call) maps `score × grounding` to an action:

| Grounding | Score ≥ 7.5 | Score 5–7.5 | Score < 5 |
|---|---|---|---|
| **STRONG** | `AUTO_ESCALATE` | `STANDARD_QUEUE` | `AUTO_CLEAR` |
| **PARTIAL** | `ESCALATE_FLAGGED` | `HUMAN_REVIEW` | `STANDARD_QUEUE` |
| **LIMITED** | `HUMAN_REVIEW` | `HUMAN_REVIEW` | `HUMAN_REVIEW` |

`LIMITED` grounding is always routed to `HUMAN_REVIEW`. The system only acts autonomously when confident.

### 6. Searchable Audit Trail
Every assessment is persisted to `macro-pulse-audit` in Elasticsearch with narrative, scores, grounding, disposition, sources, and timestamp. Exposed at `GET /api/v1/history` and queryable via the Elastic MCP server.

### 7. Real-Time SSE Dashboard
`frontend/` — Next.js 15 + React 19. Each pipeline step (agent start, Elasticsearch search query, retrieved sources, scoring, disposition decision) streams live over Server-Sent Events. Session-scoped assessment history (no cross-user leakage).

### 8. MCP Dual Role
MacroPulse both **consumes** Elastic's official MCP server (for knowledge retrieval) and **exposes** its own MCP server at `/mcp` via `fastapi-mcp`, making all API endpoints discoverable as tools for Google Cloud Agent Builder.

### 9. Alert Subscription & Live Briefings
`main.py` — `/api/v1/alerts/*`

Subscribers choose from **16 FRED-covered sovereigns** (US, Germany, UK, France, Italy, Spain, Canada, Japan, Australia, S. Korea, Mexico, Brazil, India, Türkiye, China, S. Africa) and two metrics (Interest Rates, Inflation). On each scheduled run or manual trigger:

1. Pulls live FRED indicators (10Y yields, CPI, Fed Funds, yield curve spread, VIX) for the selected countries — restricted to what FRED actually provides so every filter is backed by real data
2. Gemini writes a concise briefing covering every selected sovereign with its actual figures
3. Delivered via **Resend email** to the subscriber's address
4. Email alerts can be toggled on/off per profile

Cloud Scheduler runs the subscriber sweep every 15 minutes. Profiles are persisted in `macro-alert-subscribers` Elasticsearch index, keyed by email (no duplicates).

### 10. Self-Curating Document Bank
`main.py` — `/api/v1/bank/*`

An autonomous agent maintains the Elasticsearch knowledge base (cap: 1,000 documents):

- **Discovery**: Tavily Search API restricted to 69 whitelisted official institutional domains (IMF, BIS, World Bank, OECD, ECB, ADB, AfDB, IADB, EBRD, AIIB, FSB, NBER, CEPR, ~35 central banks, US Treasury/CBO/BLS)
- **Ingestion**: ML-health preflight before every ingest (aborts if e5 not started), max 5 PDFs per run, one-at-a-time with 2s pacing and verification — never a bulk spike
- **Removal**: Only trims above the 1,000-doc cap; only removes curator-added docs (originals protected); metadata archived before deletion
- **Schedule**: Driven by the user's focus filters (16 FRED countries + 9 macro topics) rotated deterministically across daily Cloud Scheduler runs
- **Controls**: "Preview" (dry-run), "Curate Now" (immediate run), interval (6h–weekly), autonomous ON/OFF — all in the dashboard card

### 11. Resilience
- 429-aware retry with jittered exponential backoff (5s → 12s → 25s) on all Gemini calls
- Per-instance concurrency semaphore prevents simultaneous pipeline executions from stacking against the Vertex AI quota
- All ES/API/LLM calls wrapped in try/except with graceful fallbacks

---

## Project Structure

```
MacroPulse/
├── app.py              # Elastic MCP client + hybrid search (FastMCP server)
├── main.py             # FastAPI server: two-agent pipeline, SSE, alerts, bank curator
├── ingest.py           # Bulk PDF → Elasticsearch loader (Tika + e5 embeddings)
├── requirements.txt    # Pinned Python dependencies
├── Dockerfile          # Multi-stage: bundles Elastic MCP binary
├── .env.example        # Environment variable template
├── LICENSE             # MIT
├── frontend/
│   ├── app/            # Next.js App Router pages
│   ├── components/
│   │   ├── chat-input.tsx         # Narrative input + URL fetch + preset pills
│   │   ├── pipeline-feed.tsx      # Live SSE pipeline display
│   │   ├── risk-results.tsx       # Assessment output card
│   │   ├── history-panel.tsx      # Session-scoped recent assessments
│   │   ├── alert-subscription.tsx # Alert monitor card (top-right)
│   │   └── document-bank.tsx      # Bank curator card (top-left)
│   └── lib/
│       ├── use-analysis.ts        # SSE streaming hook
│       ├── session-history.ts     # sessionStorage assessment log
│       └── types.ts               # Shared TypeScript types
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
# Edit .env with your credentials
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the API server

Ensure Docker Desktop is running (for the Elastic MCP stdio subprocess):

```bash
python -m uvicorn main:app --reload --port 8080
```

Swagger UI: `http://localhost:8080/docs`

### 4. Run the dashboard

```bash
cd frontend && npm install && npm run dev
```

Opens at `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL=http://localhost:8080` in `frontend/.env.local` to hit your local server.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | ✅ | GCP project ID for Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | ✅ | Vertex AI region (use `global`) |
| `ELASTIC_ENDPOINT` | ✅ | Elasticsearch Serverless HTTPS URL |
| `ELASTIC_API_KEY` | ✅ | Base64-encoded Elastic API key |
| `FRED_API_KEY` | ✅ | FRED API key (free at fred.stlouisfed.org) |
| `RESEND_API_KEY` | ✅ | Resend API key for email delivery |
| `TAVILY_API_KEY` | ✅ | Tavily Search API key for bank curation |
| `ALERT_SWEEP_TOKEN` | ✅ | Shared secret for Cloud Scheduler → sweep endpoints |
| `TRADING_DESK_WEBHOOK_URL` | — | Alert webhook (defaults to httpbin.org/post) |
| `ALERT_EMAIL_FROM` | — | Resend sender address (default: onboarding@resend.dev) |
| `ELASTIC_MCP_URL` | — | Leave blank (uses bundled binary) |

---

## API Reference

### Risk Assessment

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/evaluate` | Full two-agent assessment |
| `POST` | `/api/v1/evaluate/stream` | SSE-streaming variant |
| `POST` | `/api/v1/fetch-article` | Fetch + summarise a URL into a narrative |
| `GET` | `/api/v1/history` | Recent assessment audit trail |

### Alert Monitoring

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/alerts/catalog` | FRED-covered countries + metrics |
| `POST` | `/api/v1/alerts/subscribe` | Create or update a subscriber profile |
| `GET` | `/api/v1/alerts/subscriber/{email}` | Load a subscriber profile |
| `POST` | `/api/v1/alerts/market-feed` | Live FX snapshot |
| `POST` | `/api/v1/alerts/run` | Trigger an immediate briefing + email |
| `POST` | `/api/v1/alerts/sweep` | Scheduled sweep (token-protected) |

### Document Bank

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/bank/catalog` | Available countries, topics, intervals |
| `GET` | `/api/v1/bank/config` | Current curator configuration |
| `POST` | `/api/v1/bank/config` | Save curator configuration |
| `GET` | `/api/v1/bank/docs` | List all documents in the bank |
| `POST` | `/api/v1/bank/curate` | Run curation now (`?dry_run=true` for preview) |
| `POST` | `/api/v1/bank/sweep` | Scheduled curator sweep (token-protected) |

---

## Deploying to Google Cloud Run

```bash
# Backend
gcloud run deploy macropulse \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,..."

# Dashboard
cd frontend
gcloud run deploy macropulse-dashboard \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi
```

### Cloud Scheduler jobs

```bash
# Alert subscriber sweep (every 15 min)
gcloud scheduler jobs create http macropulse-alert-sweep \
  --schedule "*/15 * * * *" \
  --uri "https://<API_URL>/api/v1/alerts/sweep" \
  --http-method POST \
  --headers "X-Sweep-Token=YOUR_TOKEN"

# Daily document bank curation (06:00 UTC)
gcloud scheduler jobs create http macropulse-bank-curator \
  --schedule "0 6 * * *" \
  --uri "https://<API_URL>/api/v1/bank/sweep" \
  --http-method POST \
  --headers "X-Sweep-Token=YOUR_TOKEN"
```

---

## Hackathon Compliance — Elastic Track

| Requirement | Status | Implementation |
|---|---|---|
| Uses Google Cloud | ✅ | Vertex AI (Gemini 2.5 Flash), Cloud Run (2 services), Cloud Scheduler (2 jobs), Cloud Build |
| Uses Gemini | ✅ | `gemini-2.5-flash` via `google-genai` SDK on Vertex AI ADC — risk agents, article summariser, bank curator, alert briefer |
| Integrates Elastic's MCP server | ✅ | `docker.elastic.co/mcp/elasticsearch` — official binary bundled in multi-stage Docker image, spawned as stdio subprocess at runtime |
| Multi-step reasoning / agentic | ✅ | Agent 1 runs a tool-calling loop against Elasticsearch; Agent 2 synthesises multi-source context; bank curator discovers → ranks → ingests → trims autonomously |
| Functional agent (executes actions) | ✅ | Searches Elasticsearch, audits narratives, persists audit trail, fires confidence-gated alerts, curates and ingests knowledge-base documents |
| Exposes MCP server | ✅ | `fastapi-mcp` at `/mcp` — all FastAPI endpoints auto-exposed as MCP tools |
| Public open-source repo | ✅ | [github.com/chiachengjone/MacroPulse](https://github.com/chiachengjone/MacroPulse) |
| Open-source license | ✅ | MIT License |
| Publicly hosted URL | ✅ | `https://macropulse-270431042772.us-central1.run.app` |

---

## How the Elastic MCP Integration Works

MacroPulse uses Elastic's **official** MCP server binary bundled via a multi-stage Docker build:

```dockerfile
FROM --platform=linux/amd64 docker.elastic.co/mcp/elasticsearch AS elastic-mcp
FROM python:3.11-slim
COPY --from=elastic-mcp /usr/local/bin/elasticsearch-core-mcp-server /usr/local/bin/
```

At runtime, `main.py` spawns the binary as a `stdio` subprocess using the MCP Python SDK's `StdioServerParameters`. The Python `ClientSession` calls its tools — `search`, `list_indices`, `get_mappings` — via the standard Model Context Protocol.

This avoids Docker-in-Docker and keeps the Elastic MCP server in the same network context as the application.

---

## Sample Scenarios to Test

**High-risk (alert should fire):**
```
Turkey's central bank net FX reserves have collapsed to $24 billion after the CBRT
spent $38 billion defending the lira. USD/TRY has surged past 43, five-year sovereign
CDS spreads exceed 300bps. After five consecutive rate cuts, the CBRT held at 37% in
March 2026. Current account deficit is projected at $45 billion.
Context: FX volatility, sovereign CDS, EM credit, Turkey
```

**Low-risk (audit corrects inflated narrative):**
```
Argentina has defaulted on all external debt and the peso has collapsed 45% in 72
hours. EM contagion is spreading to Turkey and Pakistan, with CDS spreads widening
by 300-500bps across the board.
Context: Sovereign default, EM contagion
```
*(The bank contains IMF Argentina 2026 Article IV and World Bank reports documenting
Milei's stabilisation — the Auditor flags the default claim as contradicted, producing
a large raw→adjusted score correction with STRONG grounding.)*

**Singapore banking stress (strong grounding correction):**
```
Singapore's banks are carrying dangerous exposure to distressed Chinese property
developers as home prices crater and the SGD slides, with speculation MAS will
abandon its exchange-rate framework amid capital flight.
Context: Banking stability, SGD, MAS policy
```
*(Grounded by MAS Financial Stability Reviews — Auditor flags the alarmist framing
as contradicted, large score drop, STRONG grounding.)*

---

## Why Sovereign Risk?

Sovereign risk analysis is a high-stakes, information-dense domain where AI delivers outsized value:
- Analysts must synthesise hundreds of pages of institutional research on tight deadlines
- Financial media narratives are frequently exaggerated or selectively sourced
- A structured, evidence-grounded audit before any risk score is assigned is standard practice at rating agencies — but rarely automated

MacroPulse automates this workflow: **retrieve evidence → audit the narrative → score the risk → act only when confident**.

---

## License

MIT © 2026 — see [LICENSE](LICENSE)
