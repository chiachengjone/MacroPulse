from typing import Any, Callable, Optional

from fastmcp import FastMCP

mcp = FastMCP("MacroPulse-Intelligence-Engine")

INDEX_NAME = "macro-pulse-files"

# Injected by main.py during lifespan startup.
# Points to the underlying MCP ClientSession's call_tool method so that
# both the FastAPI app and this standalone MCP server share one Elastic connection.
_call_elastic_tool: Optional[Callable[..., Any]] = None


def _format_tool_result(result: Any) -> str:
    """Normalise an MCP call_tool result to a plain string."""
    items = result if isinstance(result, list) else getattr(result, "content", [])
    chunks = [item.text for item in items if hasattr(item, "text") and item.text]
    return "\n\n---\n\n".join(chunks) if chunks else "No relevant documents found."


@mcp.tool()
async def search_macro_data(query: str) -> str:
    """
    Search the MacroPulse macro-economic knowledge base for relevant content.
    Returns the top matching text chunks from indexed institutional finance documents.
    """
    if _call_elastic_tool is None:
        return "Elastic MCP session not initialised — server is starting up."
    result = await _call_elastic_tool(
        "search",
        {
            "index": INDEX_NAME,
            "query_body": {
                "query": {
                    "match": {
                        "attachment.content": {
                            "query": query,
                            "operator": "or",
                        }
                    }
                },
                "size": 3,
            },
        },
    )
    return _format_tool_result(result)


if __name__ == "__main__":
    mcp.run()