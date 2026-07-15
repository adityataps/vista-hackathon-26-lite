"""
Bedrock converse wrapper with guardrail enforcement and audit trace logging.

Usage (in any agent node):
    from agents.guardrail import converse

    result = converse(messages, system_prompt, tools=[...])
    if result["blocked"]:
        # surface the block reason to the caller
        ...
    text = result["text"]
    content = result["content"]  # pass to tool-use loop as-is
"""
import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)

_client: Any = None

_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
)
_GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
_GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "1")


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _client


def converse(
    messages: list[dict],
    system_prompt: str,
    tools: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Call Bedrock converse with guardrail enforcement.

    Returns:
        text         — assistant text (empty if stop_reason is tool_use or guardrail_intervened)
        stop_reason  — Bedrock stopReason string
        content      — raw content block list (pass through for tool-use loops)
        guardrail_trace — raw Bedrock guardrail trace (None if guardrails disabled or no trace)
        blocked      — True if guardrail_intervened
    """
    kwargs: dict[str, Any] = {
        "modelId": _MODEL_ID,
        "system": [{"text": system_prompt}],
        "messages": messages,
    }

    if _GUARDRAIL_ID:
        kwargs["guardrailConfig"] = {
            "guardrailIdentifier": _GUARDRAIL_ID,
            "guardrailVersion": _GUARDRAIL_VERSION,
            "trace": "enabled",
        }

    if tools:
        kwargs["toolConfig"] = {"tools": tools}

    response = _bedrock().converse(**kwargs)

    stop_reason: str = response.get("stopReason", "")
    content: list[dict] = response.get("output", {}).get("message", {}).get("content", [])

    text = next((b["text"] for b in content if b.get("type") == "text"), "")

    # Log the guardrail trace for the Responsible AI audit trail
    trace = response.get("trace", {}).get("guardrail")
    if trace:
        logger.info("guardrail_trace stop_reason=%s trace=%s", stop_reason, json.dumps(trace))

    return {
        "text": text,
        "stop_reason": stop_reason,
        "content": content,
        "guardrail_trace": trace,
        "blocked": stop_reason == "guardrail_intervened",
    }
