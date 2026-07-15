import json
import logging
from datetime import datetime, timezone

from langchain_aws import ChatBedrockConverse as ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agents.state import InvestigationState
from agents.tools.technical_tools import validate_iban_tool, validate_bic_tool, check_duplicate_tool, check_fx_tool
from agents.tools.payment_tools import get_payment_record

logger = logging.getLogger(__name__)

TOOLS = [validate_iban_tool, validate_bic_tool, check_duplicate_tool, check_fx_tool, get_payment_record]
TOOL_MAP = {t.name: t for t in TOOLS}

SYSTEM = """You are the Technical Diagnosis specialist for payment exceptions.
Investigate each detected error using your tools. For IBAN errors: validate the IBAN and report
which check failed. For BIC errors: validate the BIC. For duplicate UETR: check the database.
For FX inconsistency: check the math. After investigating, summarise your findings and the
recommended remediation. Be specific — name the exact field values and what is wrong."""


async def technical_node(state: InvestigationState, llm: ChatBedrock) -> dict:
    payment = state["payment"]
    errors = state["detected_errors"]
    context = state["investigation_context"]

    messages = [
        SystemMessage(content=SYSTEM),
        HumanMessage(content=(
            f"Payment record:\n{json.dumps(payment, indent=2)}\n\n"
            f"Detected errors:\n{json.dumps(errors, indent=2)}\n\n"
            f"Context:\n{json.dumps(context, indent=2)}\n\n"
            "Investigate each error using your tools. Report findings and remediation."
        )),
    ]

    llm_with_tools = llm.bind_tools(TOOLS)
    steps = list(state.get("steps", []))

    for _ in range(6):  # max 6 iterations of the ReAct loop
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
            if tool_fn:
                result = tool_fn.invoke(tool_args)
            else:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})

            steps.append({"agent": "tool", "cls": "tool", "text": f"↳ {str(result)[:200]}", "ts": ts})
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

    findings_text = response.content if hasattr(response, "content") else ""
    ts = datetime.now(timezone.utc).isoformat()
    steps.append({"agent": "Technical Diagnosis", "cls": "technical", "text": findings_text, "ts": ts})

    return {
        "technical_findings": {"raw": findings_text, "agent": "technical"},
        "steps": steps,
    }
