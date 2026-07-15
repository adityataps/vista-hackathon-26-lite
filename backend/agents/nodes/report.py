import json
import logging
from datetime import datetime, timezone

from langchain_aws import ChatBedrockConverse as ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import InvestigationState

logger = logging.getLogger(__name__)

SYSTEM = """You are a financial compliance report writer for a major international bank.
Generate a concise, professional Payment Exception Investigation Report summary.

Output a valid JSON object with exactly these keys:
  executive_summary        — 2–3 sentences summarising the exception and investigation outcome
  exception_narrative      — 1–2 sentences on what caused the exception and why it was flagged
  findings_narrative       — 2–3 sentences summarising technical and/or compliance findings
  recommendation_narrative — 1–2 sentences on the recommended remediation action
  risk_level               — "HIGH", "MEDIUM", or "LOW"
  key_facts                — array of 4–6 concise strings (specific data points, codes, amounts)

Use formal banking English. Reference error codes, IBANs, BICs, and amounts directly.
Do not invent information not present in the provided context.
Return only the JSON object — no markdown fences, no extra text."""


async def report_node(state: InvestigationState, llm: ChatBedrock) -> dict:
    payment = state.get("payment") or {}
    errors = state.get("detected_errors") or []
    technical = state.get("technical_findings") or {}
    compliance = state.get("compliance_findings") or {}
    recommendation = state.get("recommendation") or {}
    error_codes = [e.get("code") for e in errors if e.get("code")]

    try:
        confidence_pct = f"{float(recommendation.get('confidence', 0)):.0%}"
    except (TypeError, ValueError):
        confidence_pct = "N/A"

    prompt = f"""Transaction details:
  MSG ID:       {payment.get('msg_id', 'N/A')}
  UETR:         {payment.get('uetr', 'N/A')}
  Amount:       {payment.get('amount', 'N/A')} {payment.get('currency', '')}
  Debtor:       {payment.get('debtor_name', 'N/A')} / IBAN: {payment.get('debtor_iban', 'N/A')}
  Creditor:     {payment.get('creditor_name', 'N/A')} / IBAN: {payment.get('creditor_iban', 'N/A')}
  Sender BIC:   {payment.get('sender_bic', 'N/A')}
  Receiver BIC: {payment.get('receiver_bic', 'N/A')}

Detected errors: {', '.join(error_codes) or 'None'}

Technical findings:
{technical.get('raw', 'Not applicable')}

Compliance findings:
{compliance.get('raw', 'Not applicable')}

Agent recommendation:
  Action:     {recommendation.get('action', 'N/A')}
  Rationale:  {recommendation.get('rationale', 'N/A')}
  Confidence: {confidence_pct}

Generate the investigation report JSON object now."""

    steps = list(state.get("steps", []))
    ts = datetime.now(timezone.utc).isoformat()

    report_content = None
    try:
        response = await llm.ainvoke([SystemMessage(content=SYSTEM), HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        raw = content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        report_content = json.loads(raw)
    except Exception as exc:
        logger.warning("Report agent LLM call failed: %s", exc)
        report_content = {
            "executive_summary": (
                f"Payment exception {', '.join(error_codes) or 'unknown'} was investigated by the "
                "PayInvestigator AI system. The investigation identified the root cause and produced "
                "a recommendation for analyst review."
            ),
            "exception_narrative": (
                f"Error code(s) {', '.join(error_codes) or 'unknown'} were detected during payment processing."
            ),
            "findings_narrative": (
                technical.get("raw") or compliance.get("raw") or "Investigation findings not available."
            ),
            "recommendation_narrative": recommendation.get("action", "Manual review required."),
            "risk_level": "MEDIUM",
            "key_facts": [f"Error code: {c}" for c in error_codes[:4]] or ["See investigation findings"],
        }

    report_content.setdefault("risk_level", "MEDIUM")

    steps.append({
        "agent": "Report Agent",
        "cls": "report",
        "text": (
            f"Investigation report prepared — risk level **{report_content.get('risk_level')}**. "
            "Use the **Download PDF Report** button in the investigation panel to export."
        ),
        "ts": ts,
    })

    return {"report_content": report_content, "steps": steps}
