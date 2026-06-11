import json
import logging
from typing import Any, Callable, Optional

from fastmcp import FastMCP

logger = logging.getLogger("macropulse.retrieval")

mcp = FastMCP("MacroPulse-Intelligence-Engine")

INDEX_NAME = "macro-pulse-files"

# Injected by main.py during lifespan startup.
_call_elastic_tool: Optional[Callable[..., Any]] = None

# Embedding configuration for Elasticsearch Serverless kNN retriever.
# .multilingual-e5-small is the default bundled E5 model on Elastic Serverless.
_VECTOR_FIELD = "attachment.content_embedding"
_EMBEDDING_MODEL = ".multilingual-e5-small-elasticsearch"

# Set to True after the first failed RRF attempt so subsequent calls skip
# straight to keyword search rather than retrying a known-missing vector index.
_rrf_unavailable: bool = False


def _parse_hits(raw_text: str) -> list[dict]:
    """
    Best-effort parse of the Elastic MCP tool's text output into hit dicts
    of the form {"_source": {...}}.

    The official Elastic MCP `search` tool returns a JSON ARRAY of _source
    documents (e.g. [{"attachment": {...}}, ...]), while a raw ES response uses
    the {"hits": {"hits": [...]}} envelope. Handle both.
    """
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return []

    if isinstance(data, dict):
        return data.get("hits", {}).get("hits", [])
    if isinstance(data, list):
        # Each element is a bare _source document — wrap to a uniform hit shape.
        return [
            elem if (isinstance(elem, dict) and "_source" in elem) else {"_source": elem}
            for elem in data
            if isinstance(elem, dict)
        ]
    return []


def _format_tool_result(result: Any) -> tuple[str, list[str]]:
    """
    Normalise an MCP call_tool result into cited document chunks.

    Returns (formatted_text, source_titles) — the titles let the orchestrator
    surface which documents grounded the analysis and judge grounding strength.
    """
    items = result if isinstance(result, list) else getattr(result, "content", [])
    chunks: list[str] = []
    titles: list[str] = []

    for item in items:
        if not (hasattr(item, "text") and item.text):
            continue

        hits = _parse_hits(item.text)
        if hits:
            # Structured ES response — render each hit with citation header
            for i, hit in enumerate(hits, start=1):
                score = round(hit.get("_score") or 0, 4)
                src = hit.get("_source", {})
                att = src.get("attachment", {})
                title = att.get("title") or hit.get("_id", "Untitled")
                content = (att.get("content") or "").strip()
                if content:
                    _MAX = 4000
                    snippet = content[:_MAX] + (" …[truncated]" if len(content) > _MAX else "")
                    chunks.append(
                        f"[Doc {i} | score={score} | source: {title}]\n{snippet}"
                    )
                    titles.append(title)
        else:
            # Plain-text fallback (server already formatted the response)
            chunks.append(item.text.strip()[:20000])

    text = "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."
    return text, titles


async def _search_with_sources(query: str) -> tuple[str, list[str]]:
    """
    Hybrid retrieval against the knowledge base.

    Strategy:
      1. Attempt Elasticsearch RRF (Reciprocal Rank Fusion) combining a text
         multi_match across title/content with a kNN vector search via the
         .multilingual-e5-small embedding model.
      2. If the vector configuration is absent or returns an error, fall back
         to a high-fidelity keyword multi_match boosting title and content.

    Returns (formatted_text, source_titles).
    """
    global _rrf_unavailable  # must be declared before any read or write of the flag

    if _call_elastic_tool is None:
        return "Elastic MCP session not initialised — server is starting up.", []

    # ── Strategy 1: RRF hybrid (text + vector) ───────────────────────────
    rrf_body = {
        "retriever": {
            "rrf": {
                "retrievers": [
                    {
                        "standard": {
                            "query": {
                                "multi_match": {
                                    "query": query,
                                    "fields": [
                                        "attachment.title^3",
                                        "attachment.content^2",
                                    ],
                                    "type": "best_fields",
                                }
                            }
                        }
                    },
                    {
                        "knn": {
                            "field": _VECTOR_FIELD,
                            "query_vector_builder": {
                                "text_embedding": {
                                    "model_id": _EMBEDDING_MODEL,
                                    "model_text": query,
                                }
                            },
                            "num_candidates": 20,
                            "k": 3,
                        }
                    },
                ],
                "rank_constant": 60,
                "rank_window_size": 10,
            }
        },
        "size": 5,
    }

    if not _rrf_unavailable:
        try:
            result = await _call_elastic_tool(
                "search", {"index": INDEX_NAME, "query_body": rrf_body}
            )
            formatted, titles = _format_tool_result(result)
            if "No relevant documents found" not in formatted:
                logger.info("Hybrid RRF search succeeded | query=%r", query)
                return formatted, titles
            logger.debug("RRF returned no hits — trying keyword fallback")
        except Exception as exc:
            _rrf_unavailable = True
            logger.warning(
                "Hybrid RRF unavailable (%s) — switching to keyword search for this session",
                exc,
            )

    # ── Strategy 2: Keyword multi_match fallback ─────────────────────────
    keyword_body = {
        "query": {
            "multi_match": {
                "query": query,
                "fields": [
                    "attachment.title^4",
                    "attachment.content^2",
                ],
                "type": "best_fields",
                "operator": "or",
                "minimum_should_match": "30%",
            }
        },
        "size": 5,
    }

    try:
        result = await _call_elastic_tool(
            "search", {"index": INDEX_NAME, "query_body": keyword_body}
        )
        formatted, titles = _format_tool_result(result)
        logger.info("Keyword fallback search completed | query=%r", query)
        return formatted, titles
    except Exception as exc:
        logger.error("Keyword fallback search also failed: %s", exc)
        return f"Search unavailable: {exc}", []


@mcp.tool()
async def search_macro_data(query: str) -> str:
    """
    Search the MacroPulse macro-economic knowledge base using hybrid retrieval
    (RRF text + vector, with keyword fallback). Returns top document chunks with
    source citations and relevance metadata.
    """
    text, _titles = await _search_with_sources(query)
    return text


if __name__ == "__main__":
    mcp.run()