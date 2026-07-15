import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langchain_core.tools import tool

_client = None

def _bedrock_agent_runtime():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-agent-runtime",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )
    return _client


@tool
def search_knowledge_base(query: str) -> str:
    """Search the payment operations knowledge base for policy docs, compliance rules,
    SWIFT guidelines, error resolution guides, and past case references.
    Returns the top relevant excerpts with source locations."""
    kb_id = os.environ.get("KNOWLEDGE_BASE_ID", "")
    if not kb_id:
        return json.dumps({"error": "KNOWLEDGE_BASE_ID not configured", "results": []})

    try:
        response = _bedrock_agent_runtime().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5}
            },
        )
    except Exception as exc:
        return json.dumps({"error": str(exc), "results": []})

    results = []
    for r in response.get("retrievalResults", []):
        results.append({
            "content": r["content"]["text"],
            "score":   round(r.get("score", 0.0), 4),
            "source":  r.get("location", {}).get("s3Location", {}).get("uri", ""),
        })

    return json.dumps({"results": results})
