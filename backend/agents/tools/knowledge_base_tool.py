import json

from langchain_core.tools import tool

from bedrock_guard import BedrockDailyLimitExceeded
from kb import search_chunks


@tool
def search_knowledge_base(query: str) -> str:
    """Search the payment operations knowledge base for policy docs, compliance rules,
    SWIFT guidelines, error resolution guides, and past case references.
    Returns the top relevant excerpts with source locations."""
    if not query.strip():
        return json.dumps({"error": "query must not be empty", "results": []})

    try:
        return json.dumps({"results": search_chunks(query)})
    except BedrockDailyLimitExceeded:
        raise
    except Exception as exc:
        return json.dumps({"error": str(exc), "results": []})
