import json
import logging
import os
from datetime import datetime, timezone

import yaml
from langchain_aws import ChatBedrockConverse as ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import InvestigationState
from agents.tools.payment_tools import get_resolution_history

logger = logging.getLogger(__name__)

SYSTEM = """You are the Resolution Agent for a payment exception investigation system.
You synthesise findings from specialist agents and produce a single clear recommendation
for the human analyst.

Your output must be a JSON object with exactly these keys:
  action      — one sentence: what the analyst should do
  rationale   — 2-3 sentences: why, citing specific evidence from the investigation
  confidence  — a float 0.0–1.0
  requires_human_approval — always true

Do NOT recommend any autonomous action. The analyst makes the final decision."""


def _load_kb() -> dict:
    """Load agent_error_knowledge.yaml once at module level."""
    kb_path = os.path.join(os.path.dirname(__file__), "..", "agent_error_knowledge.yaml")
    try:
        with open(os.path.normpath(kb_path)) as f:
            data = yaml.safe_load(f)
        return {e["code"]: e for e in data.get("errors", [])}
    except Exception as exc:
        logger.warning("Could not load error KB: %s", exc)
        return {}


_KB: dict = _load_kb()


def _kb_context(error_codes: list[str]) -> str:
    """Return KB entries for the detected error codes as a formatted block."""
    entries = [_KB[c] for c in error_codes if c in _KB]
    if not entries:
        return ""
    lines = ["Error Knowledge Base (per-error guidance):"]
    for e in entries:
        lines.append(
            f"  [{e['code']}] severity={e['severity']} investigation_type={e['investigation_type']}\n"
            f"    suggested_action: {e['suggested_action']}\n"
            f"    auto_repairable: {e.get('auto_repairable', False)}"
        )
    return "\n".join(lines)


def _kb_fallback_recommendation(error_codes: list[str]) -> dict:
    """Rules-based fallback when the LLM call fails — mirrors ErrorResolutionAgent.fallbackSuggestion()."""
    entries = [_KB[c] for c in error_codes if c in _KB]
    if entries:
        primary = entries[0]
        action = primary["suggested_action"]
        rationale = (
            f"LLM unavailable; recommendation derived from static knowledge base. "
            f"Error {primary['code']} (severity={primary['severity']}, "
            f"type={primary['investigation_type']}) requires manual review."
        )
    else:
        action = "Route to a human analyst for manual investigation."
        rationale = "LLM unavailable and no knowledge-base entry found for the detected error codes."
    return {
        "action": action,
        "rationale": rationale,
        "confidence": 0.5,
        "requires_human_approval": True,
        "source": "rules_fallback",
    }


async def resolution_node(state: InvestigationState, llm: ChatBedrock) -> dict:
    technical = state.get("technical_findings") or {}
    compliance = state.get("compliance_findings") or {}
    errors = state["detected_errors"]

    error_codes = [e.get("code") for e in errors if e.get("code")]
    history_results = []
    for code in error_codes[:2]:
        history_results.append(get_resolution_history.invoke({"error_code": code}))

    kb_section = _kb_context(error_codes)

    prompt = (
        f"{kb_section}\n\n" if kb_section else ""
    ) + (
        f"Technical findings:\n{technical.get('raw', 'N/A')}\n\n"
        f"Compliance findings:\n{compliance.get('raw', 'N/A')}\n\n"
        f"Prior resolution history:\n{json.dumps(history_results)}\n\n"
        "Synthesise these findings and produce your recommendation as a JSON object with "
        "keys: action, rationale, confidence, requires_human_approval."
    )

    try:
        response = await llm.ainvoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
        raw = response.content.strip()

        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            recommendation = json.loads(raw)
        except Exception:
            recommendation = {"action": raw, "rationale": "See full agent output.", "confidence": 0.8,
                              "requires_human_approval": True}
    except Exception as exc:
        logger.warning("LLM call failed in resolution_node, falling back to KB rules: %s", exc)
        recommendation = _kb_fallback_recommendation(error_codes)

    recommendation.setdefault("requires_human_approval", True)

    ts = datetime.now(timezone.utc).isoformat()
    step = {
        "agent": "Resolution Agent",
        "cls": "resolution",
        "text": f"Recommendation: {recommendation.get('action', '')} (confidence {recommendation.get('confidence', 0):.0%})",
        "ts": ts,
    }

    return {
        "recommendation": recommendation,
        "steps": state.get("steps", []) + [step],
    }
