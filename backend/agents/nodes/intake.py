import json
import logging
from datetime import datetime, timezone

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import InvestigationState, ERROR_CATEGORY_MAP, TECHNICAL_CATEGORIES, COMPLIANCE_CATEGORIES

logger = logging.getLogger(__name__)

SYSTEM = """You are the Intake Agent for a payment exception investigation system.
Your job is to classify detected payment errors and set routing flags for specialist agents.
Be concise. Output a single sentence describing the exception type and routing decision."""


async def intake_node(state: InvestigationState, llm: ChatBedrock) -> dict:
    errors = state["detected_errors"]
    categories = {ERROR_CATEGORY_MAP.get(e.get("code", ""), "account_identifier") for e in errors}

    needs_technical = bool(categories & TECHNICAL_CATEGORIES)
    needs_compliance = bool(categories & COMPLIANCE_CATEGORIES)
    if not needs_technical and not needs_compliance:
        needs_technical = True

    error_summary = ", ".join(e.get("code", "UNKNOWN") for e in errors)
    prompt = f"Payment exception detected. Error codes: {error_summary}. Classify and describe the investigation routing in one sentence."

    response = await llm.ainvoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
    classification_text = response.content

    ts = datetime.now(timezone.utc).isoformat()
    step = {"agent": "Intake Agent", "cls": "intake", "text": classification_text, "ts": ts}

    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "input_tokens": response.usage_metadata.get("input_tokens", 0),
            "output_tokens": response.usage_metadata.get("output_tokens", 0),
            "total_tokens": response.usage_metadata.get("total_tokens", 0),
        }

    return {
        "intake_classification": {
            "error_categories": list(categories),
            "needs_technical": needs_technical,
            "needs_compliance": needs_compliance,
        },
        "steps": state.get("steps", []) + [step],
        "usage_metadata": usage,
    }
