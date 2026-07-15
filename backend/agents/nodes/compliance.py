import json
import logging
from datetime import datetime, timezone

from langchain_aws import ChatBedrockConverse as ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agents.state import InvestigationState
from agents.tools.compliance_tools import screen_entity_tool, check_address_completeness_tool
from agents.tools.payment_tools import get_payment_record

logger = logging.getLogger(__name__)

TOOLS = [screen_entity_tool, check_address_completeness_tool, get_payment_record]
TOOL_MAP = {t.name: t for t in TOOLS}

SYSTEM = """You are the Compliance specialist for payment exceptions.
For beneficiary name errors: screen the creditor name against the sanctions list.
For address errors: check FATF Travel Rule address completeness.
Report your findings clearly: match scores, which list, what the risk is, and your recommendation.
Never auto-reject — if uncertain, recommend hold + escalation for human review."""


async def compliance_node(state: InvestigationState, llm: ChatBedrock) -> dict:
    payment = state["payment"]
    errors = state["detected_errors"]
    context = state["investigation_context"]

    messages = [
        SystemMessage(content=SYSTEM),
        HumanMessage(content=(
            f"Payment record:\n{json.dumps(payment, indent=2)}\n\n"
            f"Detected errors:\n{json.dumps(errors, indent=2)}\n\n"
            f"Context:\n{json.dumps(context, indent=2)}\n\n"
            "Investigate compliance concerns using your tools. Report findings."
        )),
    ]

    llm_with_tools = llm.bind_tools(TOOLS)
    steps = list(state.get("steps", []))

    for _ in range(6):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_args.items())
            ts = datetime.now(timezone.utc).isoformat()
            steps.append({"agent": "tool", "cls": "tool", "text": f"🔧 {tool_name}({args_str})", "ts": ts})

            tool_fn = TOOL_MAP.get(tool_name)
            result = tool_fn.invoke(tool_args) if tool_fn else json.dumps({"error": f"Unknown tool: {tool_name}"})

            steps.append({"agent": "tool", "cls": "tool", "text": f"↳ {str(result)[:200]}", "ts": ts})
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

    findings_text = response.content if hasattr(response, "content") else ""
    ts = datetime.now(timezone.utc).isoformat()
    steps.append({"agent": "Compliance Agent", "cls": "compliance", "text": findings_text, "ts": ts})

    return {
        "compliance_findings": {"raw": findings_text, "agent": "compliance"},
        "steps": steps,
    }
