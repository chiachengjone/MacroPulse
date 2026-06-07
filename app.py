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
_EMBEDDING_MODEL = ".multilingual-e5-small"


def _parse_hits(raw_text: str) -> list[dict]:
    """
    Best-effort parse of the Elastic MCP tool's text output.
    The server serialises the ES _search response; extract hits if possible.
    """
    try:
        data = json.loads(raw_text)
        return data.get("hits", {}).get("hits", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _format_tool_result(result: Any) -> str:
    """Normalise an MCP call_tool result into cited document chunks."""
    items = result if isinstance(result, list) else getattr(result, "content", [])
    chunks = []

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
                    chunks.append(
                        f"[Doc {i} | score={score} | source: {title}]\n{content}"
                    )
        else:
            # Plain-text fallback (server already formatted the response)
            chunks.append(item.text.strip())

    return "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."


@mcp.tool()
async def search_macro_data(query: str) -> str:
    """
    Search the MacroPulse macro-economic knowledge base using hybrid retrieval.

    Strategy:
      1. Attempt Elasticsearch RRF (Reciprocal Rank Fusion) combining a text
         multi_match across title/content with a kNN vector search via the
         .multilingual-e5-small embedding model.
      2. If the vector configuration is absent or returns an error, fall back
         to a high-fidelity keyword multi_match boosting title and content.

    Returns top document chunks with source citations and relevance metadata.
    """
    if _call_elastic_tool is None:
        return "Elastic MCP session not initialised — server is starting up."

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

    try:
        result = await _call_elastic_tool(
            "search", {"index": INDEX_NAME, "query_body": rrf_body}
        )
        formatted = _format_tool_result(result)
        if "No relevant documents found" not in formatted:
            logger.info("Hybrid RRF search succeeded | query=%r", query)
            return formatted
        logger.debug("RRF returned no hits — trying keyword fallback")
    except Exception as exc:
        logger.warning(
            "Hybrid RRF search failed (%s) — falling back to keyword search", exc
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
        formatted = _format_tool_result(result)
        logger.info("Keyword fallback search completed | query=%r", query)
        return formatted
    except Exception as exc:
        logger.error("Keyword fallback search also failed: %s", exc)
        return f"Search unavailable: {exc}"


if __name__ == "__main__":
    mcp.run()